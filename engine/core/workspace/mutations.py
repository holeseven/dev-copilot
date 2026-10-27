# @author lxy
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal, Optional, Tuple
from uuid import uuid4

from engine.core.workspace.models import Workspace
from engine.core.workspace.path_guard import (
    GuardedRepository,
    OutsideWorkspaceError,
    WorkspacePathError,
    WorkspacePathGuard,
)


class MutationError(RuntimeError):
    """Base error for a controlled workspace mutation."""


class MutationValidationError(MutationError):
    """A mutation proposal is unsafe, malformed, or no longer valid."""


class MutationConflictError(MutationError):
    """The target changed after its proposal was generated."""


@dataclass(frozen=True, slots=True)
class MutationProposal:
    approval_id: str
    workspace_id: str
    repository: str
    relative_path: str
    virtual_path: str
    operation: Literal["replace", "create"]
    before_sha256: Optional[str]
    after_sha256: str
    patch_sha256: str
    diff: str
    repository_root: str = field(repr=False)
    repository_identity: Tuple[int, int] = field(repr=False)
    new_content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MutationResult:
    approval_id: str
    workspace_id: str
    virtual_path: str
    operation: Literal["replace", "create"]
    before_sha256: Optional[str]
    after_sha256: str


class WorkspaceMutator:
    """Preview and atomically apply narrowly scoped UTF-8 workspace edits.

    Caller-controlled paths and contents are untrusted. A malicious local process
    running as the same OS user is outside this boundary because it can mutate the
    workspace directly; identity checks still detect ordinary concurrent moves.
    Callers must also serialize Dev Copilot writes per repository. An external
    editor that ignores that coordination can still race the final replace; the
    later Git/index baseline layer is responsible for reconciling that case.
    """

    def __init__(
        self,
        *,
        workspace: Workspace,
        path_guard: WorkspacePathGuard,
        max_file_bytes: int = 1024 * 1024,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.workspace = workspace
        self.path_guard = path_guard
        self.max_file_bytes = max_file_bytes
        self._repositories = {
            repository.name: repository for repository in workspace.repositories
        }
        self._issued: dict[str, str] = {}
        self._consumed: set[str] = set()

    def preview_replace(
        self,
        virtual_path: str,
        old_text: str,
        new_text: str,
    ) -> MutationProposal:
        if not old_text:
            raise MutationValidationError("old_text must not be empty")
        guarded, relative = self._repository_for_path(virtual_path)
        target = self._resolve_write_target(guarded, relative, must_exist=True)
        before = self._read_utf8(target)
        if before.count(old_text) != 1:
            raise MutationValidationError("old_text must occur exactly once")
        after = before.replace(old_text, new_text, 1)
        return self._proposal(
            guarded=guarded,
            relative=relative,
            operation="replace",
            before=before,
            after=after,
        )

    def preview_write(self, virtual_path: str, content: str) -> MutationProposal:
        guarded, relative = self._repository_for_path(virtual_path)
        target = self._resolve_write_target(guarded, relative, must_exist=False)
        if target.exists():
            raise MutationValidationError(
                "target already exists; use an exact replacement proposal"
            )
        return self._proposal(
            guarded=guarded,
            relative=relative,
            operation="create",
            before=None,
            after=content,
        )

    def apply(self, proposal: MutationProposal) -> MutationResult:
        self.validate(proposal)

        guarded, relative = self._repository_for_path(proposal.virtual_path)
        target = self._resolve_write_target(
            guarded, relative, must_exist=proposal.operation == "replace"
        )
        self._atomic_write(
            guarded.root,
            target,
            proposal.new_content,
            proposal.before_sha256,
        )
        self._consumed.add(proposal.approval_id)
        return MutationResult(
            approval_id=proposal.approval_id,
            workspace_id=proposal.workspace_id,
            virtual_path=proposal.virtual_path,
            operation=proposal.operation,
            before_sha256=proposal.before_sha256,
            after_sha256=proposal.after_sha256,
        )

    def validate(self, proposal: MutationProposal) -> None:
        """Validate an issued proposal without writing or consuming it."""
        if proposal.workspace_id != self.workspace.id:
            raise MutationValidationError("proposal belongs to a different workspace")
        if proposal.approval_id in self._consumed:
            raise MutationValidationError("proposal has already been applied")
        issued_hash = self._issued.get(proposal.approval_id)
        if issued_hash is None or issued_hash != proposal.patch_sha256:
            raise MutationValidationError("proposal integrity check failed")
        self._validate_integrity(proposal)

        guarded, relative = self._repository_for_path(proposal.virtual_path)
        if guarded.name != proposal.repository or relative != proposal.relative_path:
            raise MutationValidationError("proposal path integrity check failed")
        if (
            str(guarded.root) != proposal.repository_root
            or self._path_identity(guarded.root) != proposal.repository_identity
        ):
            raise MutationConflictError("repository changed since preview")

        must_exist = proposal.operation == "replace"
        target = self._resolve_write_target(
            guarded, relative, must_exist=must_exist
        )
        self._assert_expected_state(target, proposal.before_sha256)

    def discard(self, approval_id: str) -> None:
        """Forget an unneeded issued proposal while preserving consumed history."""
        self._issued.pop(approval_id, None)

    def _proposal(
        self,
        *,
        guarded: GuardedRepository,
        relative: str,
        operation: Literal["replace", "create"],
        before: Optional[str],
        after: str,
    ) -> MutationProposal:
        after_bytes = self._encode_text(after)
        before_sha256 = (
            self._sha256(before.encode("utf-8")) if before is not None else None
        )
        after_sha256 = self._sha256(after_bytes)
        virtual_path = f"{guarded.name}/{Path(relative).as_posix()}"
        diff = self._unified_diff(virtual_path, before or "", after)
        patch_sha256 = self._proposal_hash(
            workspace_id=self.workspace.id,
            repository=guarded.name,
            relative_path=relative,
            virtual_path=virtual_path,
            operation=operation,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            diff=diff,
            repository_root=str(guarded.root),
            repository_identity=self._path_identity(guarded.root),
            new_content=after,
        )
        proposal = MutationProposal(
            approval_id=uuid4().hex,
            workspace_id=self.workspace.id,
            repository=guarded.name,
            relative_path=relative,
            virtual_path=virtual_path,
            operation=operation,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            patch_sha256=patch_sha256,
            diff=diff,
            repository_root=str(guarded.root),
            repository_identity=self._path_identity(guarded.root),
            new_content=after,
        )
        self._issued[proposal.approval_id] = proposal.patch_sha256
        return proposal

    def _validate_integrity(self, proposal: MutationProposal) -> None:
        after_bytes = self._encode_text(proposal.new_content)
        if self._sha256(after_bytes) != proposal.after_sha256:
            raise MutationValidationError("proposal integrity check failed")
        expected_diff = self._unified_diff(
            proposal.virtual_path,
            self._read_before_for_diff(proposal),
            proposal.new_content,
        )
        if expected_diff != proposal.diff:
            raise MutationValidationError("proposal integrity check failed")
        expected_hash = self._proposal_hash(
            workspace_id=proposal.workspace_id,
            repository=proposal.repository,
            relative_path=proposal.relative_path,
            virtual_path=proposal.virtual_path,
            operation=proposal.operation,
            before_sha256=proposal.before_sha256,
            after_sha256=proposal.after_sha256,
            diff=proposal.diff,
            repository_root=proposal.repository_root,
            repository_identity=proposal.repository_identity,
            new_content=proposal.new_content,
        )
        if expected_hash != proposal.patch_sha256:
            raise MutationValidationError("proposal integrity check failed")

    def _read_before_for_diff(self, proposal: MutationProposal) -> str:
        if proposal.operation == "create":
            return ""
        guarded, relative = self._repository_for_path(proposal.virtual_path)
        target = self._resolve_write_target(guarded, relative, must_exist=True)
        before = self._read_utf8(target)
        if self._sha256(before.encode("utf-8")) != proposal.before_sha256:
            raise MutationConflictError("target changed since preview")
        return before

    def _atomic_write(
        self,
        root: Path,
        target: Path,
        content: str,
        before_sha256: Optional[str],
    ) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._assert_no_links(root, target)
        except (OSError, WorkspacePathError) as exc:
            raise MutationValidationError("cannot prepare safe target directory") from exc

        temporary: Optional[Path] = None
        committed = False
        parent_identity = self._path_identity(target.parent)
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".dev-copilot-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            self._assert_same_identity(target.parent, parent_identity)
            if target.exists():
                os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            self._assert_expected_state(target, before_sha256)
            self._assert_no_links(root, target)
            self._assert_same_identity(target.parent, parent_identity)
            if before_sha256 is None:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise MutationConflictError("target changed since preview") from exc
                committed = True
                try:
                    temporary.unlink()
                    temporary = None
                except OSError:
                    # The target is already committed. A leftover random temp file
                    # is safer than reporting that the approved write did not occur.
                    pass
            else:
                os.replace(temporary, target)
                committed = True
                temporary = None
            if os.name != "nt":
                try:
                    directory_fd = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # Commit already happened. Durability diagnostics are handled
                    # by the persistent run/event layer rather than misreporting
                    # this operation as an unapplied mutation.
                    pass
        except MutationConflictError:
            raise
        except (OSError, WorkspacePathError) as exc:
            if committed:
                return
            raise MutationValidationError("could not atomically apply mutation") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _assert_expected_state(
        self, target: Path, before_sha256: Optional[str]
    ) -> None:
        if before_sha256 is None:
            if target.exists():
                raise MutationConflictError("target changed since preview")
            return
        if not target.exists():
            raise MutationConflictError("target changed since preview")
        current = self._read_bytes(target)
        if self._sha256(current) != before_sha256:
            raise MutationConflictError("target changed since preview")

    def _repository_for_path(
        self, virtual_path: str
    ) -> Tuple[GuardedRepository, str]:
        candidate = Path(virtual_path)
        if candidate.is_absolute() or not candidate.parts:
            raise PermissionError(
                f"path is outside the virtual workspace: {virtual_path}"
            )
        repository = self._repositories.get(candidate.parts[0])
        if repository is None:
            raise PermissionError(
                f"path is outside the virtual workspace: {virtual_path}"
            )
        try:
            guarded = self.path_guard.bind(repository)
        except WorkspacePathError as exc:
            raise MutationValidationError("repository is unavailable or unsafe") from exc
        relative_path = (
            Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path(".")
        )
        if relative_path == Path("."):
            raise MutationValidationError("a mutation target must be a file path")
        relative = Path(os.path.normpath(str(relative_path))).as_posix()
        try:
            guarded.resolve(relative, must_exist=False)
        except OutsideWorkspaceError:
            raise PermissionError(
                f"path is outside repository {repository.name}: {virtual_path}"
            ) from None
        except WorkspacePathError as exc:
            raise MutationValidationError("mutation target is unavailable or unsafe") from exc
        return guarded, relative

    def _resolve_write_target(
        self,
        guarded: GuardedRepository,
        relative: str,
        *,
        must_exist: bool,
    ) -> Path:
        try:
            guarded.resolve(
                relative,
                kind="file" if must_exist else None,
                must_exist=must_exist,
            )
            target = guarded.root / Path(relative)
            self._assert_no_links(guarded.root, target)
        except (OutsideWorkspaceError, WorkspacePathError) as exc:
            if isinstance(exc, OutsideWorkspaceError):
                raise PermissionError(f"path is outside repository: {relative}") from None
            raise MutationValidationError("mutation target is unavailable or unsafe") from exc
        return target

    @staticmethod
    def _path_identity(path: Path) -> Tuple[int, int]:
        try:
            metadata = path.stat()
        except OSError as exc:
            raise MutationValidationError("workspace path identity is unavailable") from exc
        return metadata.st_dev, metadata.st_ino

    @classmethod
    def _assert_same_identity(
        cls, path: Path, expected: Tuple[int, int]
    ) -> None:
        if cls._path_identity(path) != expected:
            raise MutationConflictError("workspace directory changed during apply")

    @staticmethod
    def _assert_no_links(root: Path, target: Path) -> None:
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise OutsideWorkspaceError(f"path is outside repository: {target}") from exc
        current = root
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise WorkspacePathError(f"cannot inspect mutation path: {current}") from exc
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise WorkspacePathError(
                    f"mutation path contains a link or reparse point: {current}"
                )

    def _read_utf8(self, path: Path) -> str:
        data = self._read_bytes(path)
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MutationValidationError("mutation target must be valid UTF-8 text") from exc

    def _read_bytes(self, path: Path) -> bytes:
        try:
            with path.open("rb") as handle:
                data = handle.read(self.max_file_bytes + 1)
            if len(data) > self.max_file_bytes:
                raise MutationValidationError("mutation target exceeds the size limit")
            return data
        except MutationValidationError:
            raise
        except OSError as exc:
            raise MutationValidationError("cannot read mutation target") from exc

    def _encode_text(self, content: str) -> bytes:
        try:
            encoded = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise MutationValidationError("mutation content must be valid UTF-8 text") from exc
        if len(encoded) > self.max_file_bytes:
            raise MutationValidationError("mutation content exceeds the size limit")
        return encoded

    @staticmethod
    def _sha256(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _unified_diff(virtual_path: str, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{virtual_path}",
                tofile=f"b/{virtual_path}",
            )
        )

    @classmethod
    def _proposal_hash(cls, **values: object) -> str:
        canonical = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls._sha256(canonical)
