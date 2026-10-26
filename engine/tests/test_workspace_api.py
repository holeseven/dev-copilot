import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from engine.api import workspaces
from engine.core.workspace.registry import WorkspaceRegistry
from engine.core.workspace.scanner import WorkspaceScanner


class ClosingManager:
    def __init__(self):
        self.closed = False

    async def shutdown(self):
        self.closed = True


def _client(tmp_path, allowed_roots=None, *, raise_server_exceptions=True):
    app = FastAPI()
    app.state.workspace_registry = WorkspaceRegistry(
        tmp_path / "metadata", allowed_roots=allowed_roots
    )
    app.state.workspace_scanner = WorkspaceScanner(
        path_guard=app.state.workspace_registry.path_guard
    )
    app.state.mcp_managers = {}
    app.state.workspace_manager_lock = asyncio.Lock()
    app.include_router(workspaces.router, prefix="/workspaces")
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), app


def test_workspace_crud_and_activation(tmp_path):
    client, _ = _client(tmp_path)

    created = client.post("/workspaces", json={"id": "checkout", "name": "Checkout"})
    listed = client.get("/workspaces")
    fetched = client.get("/workspaces/checkout")
    activated = client.post("/workspaces/checkout/activate")

    assert created.status_code == 201
    assert created.json() == {"id": "checkout", "name": "Checkout", "repositories": []}
    assert listed.json() == [created.json()]
    assert fetched.json() == created.json()
    assert activated.json() == created.json()


def test_repository_registration_scan_and_manager_invalidation(tmp_path):
    client, app = _client(tmp_path)
    repository = tmp_path / "order-service"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "order-service"\ndependencies = ["redis>=5"]\n',
        encoding="utf-8",
    )
    client.post("/workspaces", json={"id": "services", "name": "Services"})
    old_manager = ClosingManager()
    app.state.mcp_managers["services"] = old_manager

    registered = client.post(
        "/workspaces/services/repositories",
        json={"name": "order-service", "local_path": str(repository)},
    )
    scanned = client.post("/workspaces/services/scan")

    assert registered.status_code == 201
    assert registered.json()["repositories"] == [
        {"name": "order-service", "path": str(repository.resolve())}
    ]
    assert old_manager.closed is True
    assert "services" not in app.state.mcp_managers
    assert scanned.json() == [
        {
            "repository": "order-service",
            "git": {"branch": "", "remote": ""},
            "languages": ["Python"],
            "build_systems": ["pyproject"],
            "services": ["order-service"],
            "middleware": ["Redis"],
            "dependencies": [
                {
                    "source": "order-service",
                    "target": "redis",
                    "evidence": {
                        "source": "pyproject.toml",
                        "detail": "dependency:redis",
                    },
                }
            ],
            "evidence": [
                {"source": "pyproject.toml", "detail": "dependency:redis"},
                {"source": "pyproject.toml", "detail": "project.name"},
            ],
        }
    ]


def test_delete_closes_only_target_workspace_manager(tmp_path):
    client, app = _client(tmp_path)
    for workspace_id in ("one", "two"):
        client.post("/workspaces", json={"id": workspace_id, "name": workspace_id})
    first = ClosingManager()
    second = ClosingManager()
    app.state.mcp_managers.update({"one": first, "two": second})

    response = client.delete("/workspaces/one")

    assert response.status_code == 204
    assert first.closed is True
    assert second.closed is False
    assert app.state.mcp_managers == {"two": second}


def test_workspace_api_uses_explicit_client_errors(tmp_path):
    client, _ = _client(tmp_path)
    client.post("/workspaces", json={"id": "one", "name": "One"})

    duplicate = client.post("/workspaces", json={"id": "one", "name": "Duplicate"})
    missing = client.get("/workspaces/missing")
    missing_repository = client.post(
        "/workspaces/one/repositories",
        json={"name": "missing", "local_path": str(tmp_path / "missing")},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "workspace already exists: one"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "workspace not found: missing"
    assert missing_repository.status_code == 422
    assert "repository path" in missing_repository.json()["detail"]
    assert "does not exist" in missing_repository.json()["detail"]


def test_scan_revalidates_repository_after_registered_root_changes(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    repository_root = allowed_root / "service"
    repository_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    client, _ = _client(tmp_path, allowed_roots=(allowed_root,))
    client.post("/workspaces", json={"id": "services", "name": "Services"})
    client.post(
        "/workspaces/services/repositories",
        json={"name": "service", "local_path": str(repository_root)},
    )
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def replaced_root(path, *args, **kwargs):
        if path == repository_root:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", replaced_root)

    response = client.post("/workspaces/services/scan")

    assert response.status_code == 422
    assert "outside allowed roots" in response.json()["detail"]


def test_scan_uses_one_validated_root_without_switching_to_outside(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    repository_root = allowed_root / "service"
    repository_root.mkdir(parents=True)
    (repository_root / "pyproject.toml").write_text(
        '[project]\nname = "inside-service"\n', encoding="utf-8"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pyproject.toml").write_text(
        '[project]\nname = "outside-service"\n', encoding="utf-8"
    )
    client, _ = _client(
        tmp_path,
        allowed_roots=(allowed_root,),
    )
    client.post("/workspaces", json={"id": "services", "name": "Services"})
    client.post(
        "/workspaces/services/repositories",
        json={"name": "service", "local_path": str(repository_root)},
    )
    original_resolve = Path.resolve
    canonical_root = repository_root.resolve()
    outside_root = outside.resolve()
    root_resolves = 0

    def switching_resolve(path, *args, **kwargs):
        nonlocal root_resolves
        if path == repository_root or path == canonical_root:
            root_resolves += 1
            return canonical_root if root_resolves == 1 else outside_root
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", switching_resolve)

    response = client.post("/workspaces/services/scan")

    assert response.status_code == 200
    assert response.json()[0]["services"] == ["inside-service"]
    assert root_resolves == 1


@pytest.mark.asyncio
async def test_workspace_manager_passes_registry_path_guard_to_local_provider(
    tmp_path, monkeypatch
):
    allowed_root = (tmp_path / "allowed").resolve()
    allowed_root.mkdir()
    registry = WorkspaceRegistry(tmp_path / "metadata", allowed_roots=(allowed_root,))
    workspace = registry.create("demo", "Demo")
    captured = {}

    class CapturingProvider:
        def __init__(self, name, received_workspace, path_guard):
            captured.update(
                name=name,
                workspace=received_workspace,
                path_guard=path_guard,
            )

    class FakeManager:
        def __init__(self, configs):
            self.configs = configs

        async def init_all(self):
            return None

    monkeypatch.setattr(workspaces, "LocalDevToolProvider", CapturingProvider)
    monkeypatch.setattr(workspaces, "MCPManager", FakeManager)
    app = FastAPI()
    app.state.workspace_registry = registry
    app.state.mcp_managers = {}
    app.state.workspace_manager_lock = asyncio.Lock()

    await workspaces.get_workspace_manager(SimpleNamespace(app=app), workspace)

    assert captured == {
        "name": "local",
        "workspace": workspace,
        "path_guard": registry.path_guard,
    }
