from __future__ import annotations

import os
import tempfile
from pathlib import Path
from shutil import rmtree
from typing import Collection, List, Optional, Union
from uuid import uuid4

import yaml

from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.path_guard import WorkspacePathError, WorkspacePathGuard


def resolve_path(path: Union[str, Path], *, context: str) -> Path:
    """Resolve a path and expose platform-specific failures as validation errors."""

    candidate = Path(path).expanduser()
    try:
        return candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError(f"cannot resolve {context} path: {candidate}") from exc


class WorkspaceRegistry:
    """Persistent local workspace metadata registry."""

    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        allowed_roots: Optional[Collection[Path]] = None,
    ):
        self.root = Path(root).expanduser() if root is not None else Path.home() / ".dev-copilot"
        self.path_guard = WorkspacePathGuard.from_roots(allowed_roots)
        self.allowed_roots = self.path_guard.allowed_roots
        self.root.mkdir(parents=True, exist_ok=True)
        self._workspaces_root = self.root / "workspaces"
        self._workspaces_root.mkdir(exist_ok=True)
        self._active_file = self.root / "active_workspace"
        self._workspaces = self._load_workspaces()

    def create(self, workspace_id: str, name: str) -> Workspace:
        self._workspace_path(workspace_id)
        if workspace_id in self._workspaces:
            raise ValueError(f"workspace already exists: {workspace_id}")
        workspace = Workspace(id=workspace_id, name=name)
        return self.save(workspace)

    def list(self) -> List[Workspace]:
        return [
            self._copy(workspace)
            for workspace in sorted(
                self._workspaces.values(), key=lambda workspace: (workspace.name, workspace.id)
            )
        ]

    def get(self, workspace_id: str) -> Optional[Workspace]:
        workspace = self._workspaces.get(workspace_id)
        return self._copy(workspace) if workspace is not None else None

    def delete(self, workspace_id: str) -> bool:
        if workspace_id not in self._workspaces:
            return False
        workspace_path = self._workspace_path(workspace_id)
        del self._workspaces[workspace_id]
        rmtree(workspace_path)
        active = self._read_active_id()
        if active == workspace_id:
            self._active_file.unlink(missing_ok=True)
        return True

    def set_active(self, workspace_id: str) -> Workspace:
        workspace = self._require(workspace_id)
        self._active_file.write_text(workspace.id, encoding="utf-8")
        return workspace

    def get_active(self) -> Optional[Workspace]:
        active_id = self._read_active_id()
        return self.get(active_id) if active_id else None

    def add_repository(self, workspace_id: str, name: str, path: Union[str, Path]) -> Workspace:
        workspace = self._require(workspace_id)
        repository_path = self.path_guard.bind(
            Repository(name=name, path=str(path))
        ).root
        if any(repository.name == name for repository in workspace.repositories):
            raise ValueError(f"repository name already exists in workspace: {name}")
        workspace.repositories.append(Repository(name=name, path=str(repository_path)))
        return self.save(workspace)

    def save(self, workspace: Workspace) -> Workspace:
        workspace_path = self._workspace_path(workspace.id)
        stored = self._copy(workspace)
        for repository in stored.repositories:
            repository.path = str(self.path_guard.bind(repository).root)
        existing = self._workspaces.get(stored.id)
        if existing is not None:
            if (
                existing.name != stored.name
                or existing.repositories != stored.repositories
            ):
                stored.generation = uuid4().hex
            else:
                stored.generation = existing.generation
        payload = yaml.safe_dump(
            stored.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )
        workspace_file = workspace_path / "workspace.yaml"
        workspace_file.parent.mkdir(parents=True, exist_ok=True)
        self._write_workspace_atomically(workspace_file, payload)
        self._workspaces[stored.id] = stored
        return self._copy(stored)

    def validate_repository(self, repository: Repository) -> Repository:
        """Return a detached repository with its current canonical path validated."""

        validated = repository.model_copy(deep=True)
        validated.path = str(self.path_guard.bind(validated).root)
        return validated

    def _require(self, workspace_id: str) -> Workspace:
        workspace = self.get(workspace_id)
        if workspace is None:
            raise ValueError(f"workspace does not exist: {workspace_id}")
        return workspace

    def _load_workspaces(self) -> dict[str, Workspace]:
        workspaces = {}
        for workspace_file in sorted(self._workspaces_root.glob("*/workspace.yaml")):
            raw_workspace = yaml.safe_load(
                workspace_file.read_text(encoding="utf-8")
            ) or {}
            workspace = Workspace.model_validate(
                raw_workspace
            )
            for repository in workspace.repositories:
                try:
                    repository.path = str(self.path_guard.bind(repository).root)
                except WorkspacePathError as exc:
                    raise WorkspacePathError(f"persisted {exc}") from exc
            workspaces[workspace.id] = workspace
            if "generation" not in raw_workspace:
                payload = yaml.safe_dump(
                    workspace.model_dump(mode="json"),
                    allow_unicode=True,
                    sort_keys=False,
                )
                self._write_workspace_atomically(workspace_file, payload)
        return workspaces

    @staticmethod
    def _copy(workspace: Workspace) -> Workspace:
        return workspace.model_copy(deep=True)

    @staticmethod
    def _write_workspace_atomically(workspace_file: Path, payload: str) -> None:
        descriptor: Optional[int] = None
        temporary_file: Optional[Path] = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=workspace_file.parent,
                prefix=f".{workspace_file.name}.",
                suffix=".tmp",
            )
            temporary_file = Path(temporary_name)
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
            descriptor = None
            with stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_file, workspace_file)
            temporary_file = None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_file is not None:
                temporary_file.unlink(missing_ok=True)

    def _read_active_id(self) -> Optional[str]:
        if not self._active_file.exists():
            return None
        return self._active_file.read_text(encoding="utf-8").strip() or None

    def _workspace_path(self, workspace_id: str) -> Path:
        root = resolve_path(self._workspaces_root, context="workspace metadata")
        candidate = resolve_path(root / workspace_id, context="workspace metadata")
        if candidate.parent != root:
            raise ValueError(f"workspace id must name a direct metadata directory: {workspace_id}")
        return candidate
