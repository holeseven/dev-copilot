# @author lxy
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Collection, Optional, Tuple, Union

from engine.core.workspace.models import Repository


class WorkspacePathError(ValueError):
    """A workspace path cannot be resolved or safely accessed."""


class OutsideWorkspaceError(WorkspacePathError):
    """A path resolves outside the configured workspace boundaries."""


def _resolve(path: Path, *, context: str) -> Path:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError(f"cannot resolve {context}: {path}") from exc


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class WorkspacePathGuard:
    allowed_roots: Optional[Tuple[Path, ...]]

    @classmethod
    def from_roots(
        cls,
        roots: Optional[Collection[Union[str, Path]]],
    ) -> "WorkspacePathGuard":
        if roots is None:
            return cls(allowed_roots=None)
        return cls(
            allowed_roots=tuple(
                _resolve(Path(root), context="workspace allowed root") for root in roots
            )
        )

    def bind(self, repository: Repository) -> "GuardedRepository":
        context = f"repository path for {repository.name!r}"
        root = _resolve(Path(repository.path), context=context)
        self._validate_existing_kind(root, kind="dir", context=context)
        if self.allowed_roots is not None and not any(
            _within(root, allowed_root) for allowed_root in self.allowed_roots
        ):
            raise OutsideWorkspaceError(
                f"repository {repository.name!r} is outside allowed roots: {root}"
            )
        return GuardedRepository(name=repository.name, root=root)

    @staticmethod
    def _validate_existing_kind(path: Path, *, kind: str, context: str) -> None:
        try:
            exists = path.exists()
            matches_kind = path.is_dir() if kind == "dir" else path.is_file()
        except OSError as exc:
            raise WorkspacePathError(f"cannot inspect {context}: {path}") from exc
        if not exists:
            raise WorkspacePathError(f"{context} does not exist: {path}")
        if not matches_kind:
            label = "directory" if kind == "dir" else kind
            raise WorkspacePathError(f"{context} is not a {label}: {path}")


@dataclass(frozen=True)
class GuardedRepository:
    name: str
    root: Path

    def resolve(
        self,
        relative: Union[str, Path],
        *,
        kind: Optional[str] = None,
        must_exist: bool = True,
    ) -> Path:
        candidate = self._candidate(relative)
        if candidate == self.root:
            resolved = self.root
        else:
            try:
                is_broken_link = candidate.is_symlink() and not candidate.exists()
            except OSError as exc:
                raise WorkspacePathError(f"cannot inspect repository path: {relative}") from exc
            if is_broken_link:
                raise WorkspacePathError(f"repository path is a broken link: {relative}")
            resolved = _resolve(candidate, context=f"repository path {relative!s}")
        if not _within(resolved, self.root):
            raise OutsideWorkspaceError(
                f"path is outside repository {self.name}: {relative}"
            )
        try:
            exists = resolved.exists()
            if must_exist and not exists:
                raise WorkspacePathError(f"repository path does not exist: {relative}")
            if exists and kind == "file" and not resolved.is_file():
                raise WorkspacePathError(f"repository path is not a file: {relative}")
            if exists and kind == "dir" and not resolved.is_dir():
                raise WorkspacePathError(f"repository path is not a directory: {relative}")
        except OSError as exc:
            raise WorkspacePathError(f"cannot inspect repository path: {relative}") from exc
        return resolved

    def list_dir(self, relative: Union[str, Path] = ".") -> Tuple[str, ...]:
        directory = self.resolve(relative, kind="dir", must_exist=True)
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspacePathError(f"cannot list repository directory: {relative}") from exc
        validated = []
        for entry in entries:
            entry_relative = self.relative_path(entry, resolve=False)
            resolved = self.resolve(entry_relative, must_exist=True)
            try:
                is_directory = resolved.is_dir()
            except OSError as exc:
                raise WorkspacePathError(
                    f"cannot inspect repository entry: {entry_relative}"
                ) from exc
            validated.append(f"{entry.name}/" if is_directory else entry.name)
        return tuple(validated)

    def read_text(
        self,
        relative: Union[str, Path],
        *,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> str:
        path = self.resolve(relative, kind="file", must_exist=True)
        try:
            return path.read_text(encoding=encoding, errors=errors)
        except OSError as exc:
            raise WorkspacePathError(f"cannot read repository file: {relative}") from exc

    def iter_files(self, relative: Union[str, Path] = ".") -> Tuple[Path, ...]:
        start = self.resolve(relative, must_exist=True)
        try:
            if start.is_file():
                return (start,)
            if not start.is_dir():
                raise WorkspacePathError(f"repository path is not readable: {relative}")
        except OSError as exc:
            raise WorkspacePathError(
                f"cannot inspect repository path: {relative}"
            ) from exc

        files = []

        def fail_walk(error: OSError) -> None:
            raise WorkspacePathError(f"cannot walk repository directory: {relative}") from error

        for current, directories, filenames in os.walk(
            start,
            topdown=True,
            onerror=fail_walk,
            followlinks=False,
        ):
            current_path = Path(current)
            current_relative = self.relative_path(current_path, resolve=False)
            self.resolve(current_relative, kind="dir", must_exist=True)

            retained_directories = []
            for directory_name in sorted(directories):
                directory_path = current_path / directory_name
                directory_relative = self.relative_path(directory_path, resolve=False)
                self.resolve(directory_relative, kind="dir", must_exist=True)
                if not self._is_directory_link(directory_path):
                    retained_directories.append(directory_name)
            directories[:] = retained_directories

            for filename in sorted(filenames):
                file_path = current_path / filename
                file_relative = self.relative_path(file_path, resolve=False)
                files.append(self.resolve(file_relative, kind="file", must_exist=True))

        return tuple(sorted(files, key=self.relative_path))

    def relative_path(self, path: Union[str, Path], *, resolve: bool = True) -> str:
        candidate = Path(path)
        if resolve:
            candidate = _resolve(candidate, context="repository result path")
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise OutsideWorkspaceError(
                f"path is outside repository {self.name}: {path}"
            ) from exc
        value = relative.as_posix()
        return value if value else "."

    def _candidate(self, relative: Union[str, Path]) -> Path:
        raw = str(relative)
        candidate = Path(relative)
        windows = PureWindowsPath(raw)
        if candidate.is_absolute() or candidate.anchor or windows.drive or windows.anchor:
            raise OutsideWorkspaceError(f"absolute repository path is not allowed: {relative}")
        return self.root if candidate == Path(".") else self.root / candidate

    @staticmethod
    def _is_directory_link(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError as exc:
            raise WorkspacePathError(f"cannot inspect repository directory: {path}") from exc
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
