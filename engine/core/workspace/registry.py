from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union
from uuid import uuid4

import yaml

from engine.core.workspace.models import Repository, Workspace


class WorkspaceRegistry:
    """Persistent local workspace metadata registry."""

    def __init__(self, root: Optional[Union[str, Path]] = None):
        self.root = Path(root).expanduser() if root is not None else Path.home() / ".dev-copilot"
        self.root.mkdir(parents=True, exist_ok=True)
        self._workspace_file = self.root / "workspace.yaml"
        self._active_file = self.root / "active.yaml"
        self._workspaces = self._load_workspaces()

    def create(self, name: str) -> Workspace:
        workspace = Workspace(id=str(uuid4()), name=name)
        self._workspaces[workspace.id] = workspace
        self.save(workspace)
        return workspace

    def list(self) -> List[Workspace]:
        return sorted(self._workspaces.values(), key=lambda workspace: (workspace.name, workspace.id))

    def get(self, workspace_id: str) -> Optional[Workspace]:
        return self._workspaces.get(workspace_id)

    def delete(self, workspace_id: str) -> bool:
        if workspace_id not in self._workspaces:
            return False
        del self._workspaces[workspace_id]
        self._write_workspaces()
        active = self._read_active_id()
        if active == workspace_id:
            self._active_file.unlink(missing_ok=True)
        return True

    def set_active(self, workspace_id: str) -> Workspace:
        workspace = self._require(workspace_id)
        self._active_file.write_text(yaml.safe_dump({"id": workspace.id}, sort_keys=True), encoding="utf-8")
        return workspace

    def get_active(self) -> Optional[Workspace]:
        active_id = self._read_active_id()
        return self.get(active_id) if active_id else None

    def add_repository(self, workspace_id: str, name: str, path: Union[str, Path]) -> Workspace:
        workspace = self._require(workspace_id)
        repository_path = Path(path).expanduser()
        if not repository_path.is_dir():
            raise ValueError(f"repository directory does not exist: {repository_path}")
        if any(repository.name == name for repository in workspace.repositories):
            raise ValueError(f"repository name already exists in workspace: {name}")
        workspace.repositories.append(Repository(name=name, path=str(repository_path.resolve())))
        self.save(workspace)
        return workspace

    def save(self, workspace: Workspace) -> Workspace:
        self._workspaces[workspace.id] = workspace
        self._write_workspaces()
        return workspace

    def _require(self, workspace_id: str) -> Workspace:
        workspace = self.get(workspace_id)
        if workspace is None:
            raise ValueError(f"workspace does not exist: {workspace_id}")
        return workspace

    def _load_workspaces(self) -> dict[str, Workspace]:
        if not self._workspace_file.exists():
            return {}
        content = yaml.safe_load(self._workspace_file.read_text(encoding="utf-8")) or {}
        return {
            item["id"]: Workspace.model_validate(item)
            for item in content.get("workspaces", [])
        }

    def _write_workspaces(self) -> None:
        payload = {"workspaces": [workspace.model_dump(mode="json") for workspace in self.list()]}
        self._workspace_file.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _read_active_id(self) -> Optional[str]:
        if not self._active_file.exists():
            return None
        content = yaml.safe_load(self._active_file.read_text(encoding="utf-8")) or {}
        return content.get("id")
