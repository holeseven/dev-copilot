import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from engine import main
from engine.api import workspaces
from engine.core.workspace.models import Workspace
from engine.core.workspace.registry import WorkspaceRegistry


class FakeDynamicConfig:
    def __init__(self):
        self.loaded = False
        self.started = False
        self.stopped = False

    async def load_all(self):
        self.loaded = True

    def start_refresh(self):
        self.started = True

    async def stop_refresh(self):
        self.stopped = True


class ClosingManager:
    def __init__(self):
        self.closed = False

    async def shutdown(self):
        self.closed = True


@pytest.mark.asyncio
async def test_lifespan_owns_workspace_runtime_and_closes_all_managers(tmp_path, monkeypatch):
    dynamic_config = FakeDynamicConfig()
    redis_calls = []
    graph = object()
    dispatcher = object()

    async def init_redis():
        redis_calls.append("init")

    async def close_redis():
        redis_calls.append("close")

    monkeypatch.setattr(main, "init_redis", init_redis)
    monkeypatch.setattr(main, "close_redis", close_redis)
    monkeypatch.setattr(main, "get_llm_by_complexity", lambda _: object())
    monkeypatch.setattr(main, "get_dynamic_config", lambda: dynamic_config)
    monkeypatch.setattr(main, "WorkspaceRegistry", lambda: WorkspaceRegistry(tmp_path / "metadata"))
    monkeypatch.setattr(main, "build_agent_graph", lambda: graph)
    monkeypatch.setattr(main, "get_agent_dispatcher", lambda: dispatcher)
    app = FastAPI()
    manager = ClosingManager()

    async with main.lifespan(app):
        assert app.state.workspace_registry.list() == []
        assert app.state.workspace_scanner is not None
        assert app.state.agent_graph is graph
        assert app.state.dispatcher is dispatcher
        assert app.state.mcp_managers == {}
        assert app.state.workspace_manager_lock is not None
        app.state.mcp_managers["workspace"] = manager

    assert manager.closed is True
    assert dynamic_config.loaded is True
    assert dynamic_config.started is True
    assert dynamic_config.stopped is True
    assert redis_calls == ["init", "close"]


@pytest.mark.asyncio
async def test_lifespan_closes_manager_initialized_during_teardown(tmp_path, monkeypatch):
    init_started = asyncio.Event()
    release_init = asyncio.Event()

    class BlockingManager:
        instances = []

        def __init__(self, configs):
            self.closed = False
            type(self).instances.append(self)

        async def init_all(self):
            init_started.set()
            await release_init.wait()

        async def shutdown(self):
            self.closed = True

    dynamic_config = FakeDynamicConfig()

    async def noop():
        pass

    monkeypatch.setattr(main, "init_redis", noop)
    monkeypatch.setattr(main, "close_redis", noop)
    monkeypatch.setattr(main, "get_llm_by_complexity", lambda _: object())
    monkeypatch.setattr(main, "get_dynamic_config", lambda: dynamic_config)
    monkeypatch.setattr(main, "WorkspaceRegistry", lambda: WorkspaceRegistry(tmp_path / "metadata"))
    monkeypatch.setattr(main, "build_agent_graph", object)
    monkeypatch.setattr(main, "get_agent_dispatcher", object)
    monkeypatch.setattr(workspaces, "MCPManager", BlockingManager)
    app = FastAPI()
    lifespan = main.lifespan(app)
    await lifespan.__aenter__()
    request = SimpleNamespace(app=app)

    manager_task = asyncio.create_task(
        workspaces.get_workspace_manager(
            request, Workspace(id="racing", name="Racing")
        )
    )
    await init_started.wait()
    teardown_task = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await asyncio.sleep(0)
    release_init.set()
    manager, _ = await asyncio.gather(manager_task, teardown_task)

    assert manager.closed is True
    assert app.state.mcp_managers == {}


def test_application_mounts_workspace_routes():
    paths = TestClient(main.create_app()).get("/openapi.json").json()["paths"]

    assert "/workspaces" in paths
    assert "/workspaces/{workspace_id}/repositories" in paths
    assert "/workspaces/{workspace_id}/scan" in paths
    assert "/workspaces/{workspace_id}/activate" in paths
