# @author lxy
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

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


class FailingManager:
    def __init__(self, calls):
        self.calls = calls

    async def shutdown(self):
        self.calls.append("failing-manager")
        raise RuntimeError("manager cleanup failed")


class RecordingManager:
    def __init__(self, calls):
        self.calls = calls

    async def shutdown(self):
        self.calls.append("recording-manager")


def configure_lifespan_dependencies(tmp_path, monkeypatch, dynamic_config, calls):
    settings = SimpleNamespace(
        app_name="test-engine",
        log_level="INFO",
        workspace_metadata_path=tmp_path / "metadata",
        workspace_allowed_root_paths=(),
        resolved_checkpoint_db_path=tmp_path / "checkpoints.sqlite3",
        resolved_proposal_db_path=tmp_path / "proposals.sqlite3",
        resolved_rag_db_path=tmp_path / "rag.sqlite3",
    )

    @asynccontextmanager
    async def open_checkpointer(path):
        yield object()

    async def init_redis():
        calls.append("init-redis")

    async def close_redis():
        calls.append("close-redis")

    monkeypatch.setattr(main, "init_redis", init_redis)
    monkeypatch.setattr(main, "close_redis", close_redis)
    monkeypatch.setattr(main, "get_llm_by_complexity", lambda _: object())
    monkeypatch.setattr(main, "get_dynamic_config", lambda: dynamic_config)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "open_sqlite_checkpointer", open_checkpointer)
    monkeypatch.setattr(
        main,
        "WorkspaceRegistry",
        lambda **kwargs: WorkspaceRegistry(tmp_path / "metadata"),
    )
    monkeypatch.setattr(main, "build_agent_graph", lambda **kwargs: object())
    monkeypatch.setattr(main, "get_agent_dispatcher", object)


@pytest.mark.asyncio
async def test_lifespan_owns_workspace_runtime_and_closes_all_managers(tmp_path, monkeypatch):
    dynamic_config = FakeDynamicConfig()
    redis_calls = []
    graph = object()
    dispatcher = object()
    metadata_root = tmp_path / "metadata"
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    settings = SimpleNamespace(
        app_name="test-engine",
        log_level="INFO",
        workspace_metadata_path=metadata_root,
        workspace_allowed_root_paths=(allowed_root.resolve(),),
        resolved_checkpoint_db_path=metadata_root / "checkpoints.sqlite3",
        resolved_proposal_db_path=metadata_root / "proposals.sqlite3",
        resolved_rag_db_path=metadata_root / "rag.sqlite3",
    )
    registry_arguments = []

    async def init_redis():
        redis_calls.append("init")

    async def close_redis():
        redis_calls.append("close")

    monkeypatch.setattr(main, "init_redis", init_redis)
    monkeypatch.setattr(main, "close_redis", close_redis)
    monkeypatch.setattr(main, "get_llm_by_complexity", lambda _: object())
    monkeypatch.setattr(main, "get_dynamic_config", lambda: dynamic_config)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main,
        "WorkspaceRegistry",
        lambda **kwargs: registry_arguments.append(kwargs) or WorkspaceRegistry(**kwargs),
    )
    checkpointer = object()
    checkpoint_events = []

    @asynccontextmanager
    async def open_checkpointer(path):
        checkpoint_events.append(("open", path))
        try:
            yield checkpointer
        finally:
            checkpoint_events.append(("close", path))

    monkeypatch.setattr(main, "open_sqlite_checkpointer", open_checkpointer)
    graph_arguments = []
    monkeypatch.setattr(
        main,
        "build_agent_graph",
        lambda **kwargs: graph_arguments.append(kwargs) or graph,
    )
    monkeypatch.setattr(main, "get_agent_dispatcher", lambda: dispatcher)
    app = FastAPI()
    manager = ClosingManager()

    async with main.lifespan(app):
        assert app.state.workspace_registry.list() == []
        assert app.state.workspace_scanner is not None
        assert (
            app.state.workspace_scanner.path_guard
            is app.state.workspace_registry.path_guard
        )
        assert app.state.agent_graph is graph
        assert app.state.proposal_store.path == settings.resolved_proposal_db_path.resolve()
        assert app.state.dispatcher is dispatcher
        assert app.state.mcp_managers == {}
        assert app.state.workspace_manager_lock is not None
        app.state.mcp_managers["workspace"] = manager

    assert manager.closed is True
    assert dynamic_config.loaded is True
    assert dynamic_config.started is True
    assert dynamic_config.stopped is True
    assert redis_calls == ["init", "close"]
    assert registry_arguments == [
        {
            "root": metadata_root,
            "allowed_roots": (allowed_root.resolve(),),
        }
    ]
    assert graph_arguments == [{"checkpointer": checkpointer}]
    assert checkpoint_events == [
        ("open", settings.resolved_checkpoint_db_path),
        ("close", settings.resolved_checkpoint_db_path),
    ]


@pytest.mark.asyncio
async def test_lifespan_closes_checkpointer_when_startup_fails(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        app_name="test-engine",
        log_level="INFO",
        resolved_checkpoint_db_path=tmp_path / "checkpoints.sqlite3",
    )
    events = []

    @asynccontextmanager
    async def open_checkpointer(path):
        events.append(("open", path))
        try:
            yield object()
        finally:
            events.append(("close", path))

    async def fail_redis():
        raise RuntimeError("redis startup failed")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "open_sqlite_checkpointer", open_checkpointer)
    monkeypatch.setattr(main, "init_redis", fail_redis)

    with pytest.raises(RuntimeError, match="redis startup failed"):
        async with main.lifespan(FastAPI()):
            pass

    assert events == [
        ("open", settings.resolved_checkpoint_db_path),
        ("close", settings.resolved_checkpoint_db_path),
    ]


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
    settings = SimpleNamespace(
        app_name="test-engine",
        log_level="INFO",
        workspace_metadata_path=tmp_path / "metadata",
        workspace_allowed_root_paths=(),
        resolved_checkpoint_db_path=tmp_path / "checkpoints.sqlite3",
        resolved_proposal_db_path=tmp_path / "proposals.sqlite3",
        resolved_rag_db_path=tmp_path / "rag.sqlite3",
    )

    @asynccontextmanager
    async def open_checkpointer(path):
        yield object()

    async def noop():
        pass

    monkeypatch.setattr(main, "init_redis", noop)
    monkeypatch.setattr(main, "close_redis", noop)
    monkeypatch.setattr(main, "get_llm_by_complexity", lambda _: object())
    monkeypatch.setattr(main, "get_dynamic_config", lambda: dynamic_config)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "open_sqlite_checkpointer", open_checkpointer)
    monkeypatch.setattr(
        main,
        "WorkspaceRegistry",
        lambda **kwargs: WorkspaceRegistry(tmp_path / "metadata"),
    )
    monkeypatch.setattr(main, "build_agent_graph", lambda **kwargs: object())
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


@pytest.mark.asyncio
async def test_lifespan_attempts_every_cleanup_and_reraises_first_error(
    tmp_path, monkeypatch
):
    calls = []

    class RecordingDynamicConfig(FakeDynamicConfig):
        async def stop_refresh(self):
            calls.append("stop-refresh")
            self.stopped = True

    dynamic_config = RecordingDynamicConfig()
    configure_lifespan_dependencies(tmp_path, monkeypatch, dynamic_config, calls)
    app = FastAPI()
    context = main.lifespan(app)
    await context.__aenter__()
    app.state.mcp_managers.update(
        {
            "fails": FailingManager(calls),
            "closes": RecordingManager(calls),
        }
    )

    with pytest.raises(RuntimeError, match="manager cleanup failed"):
        await context.__aexit__(None, None, None)

    assert calls == [
        "init-redis",
        "failing-manager",
        "recording-manager",
        "stop-refresh",
        "close-redis",
    ]
    assert app.state.mcp_managers == {}
    assert dynamic_config.stopped is True


@pytest.mark.asyncio
async def test_lifespan_closes_redis_when_dynamic_config_cleanup_fails(
    tmp_path, monkeypatch
):
    calls = []

    class FailingDynamicConfig(FakeDynamicConfig):
        async def stop_refresh(self):
            calls.append("stop-refresh")
            raise ValueError("dynamic config cleanup failed")

    dynamic_config = FailingDynamicConfig()
    configure_lifespan_dependencies(tmp_path, monkeypatch, dynamic_config, calls)
    app = FastAPI()
    context = main.lifespan(app)
    await context.__aenter__()

    with pytest.raises(ValueError, match="dynamic config cleanup failed"):
        await context.__aexit__(None, None, None)

    assert calls == ["init-redis", "stop-refresh", "close-redis"]


@pytest.mark.asyncio
async def test_lifespan_preserves_body_error_when_cleanup_also_fails(
    tmp_path, monkeypatch
):
    calls = []
    body_error = ValueError("request handling failed")
    dynamic_config = FakeDynamicConfig()
    configure_lifespan_dependencies(tmp_path, monkeypatch, dynamic_config, calls)
    app = FastAPI()

    with pytest.raises(ValueError, match="request handling failed") as raised:
        async with main.lifespan(app):
            app.state.mcp_managers["fails"] = FailingManager(calls)
            raise body_error

    assert raised.value is body_error
    assert any(
        "manager cleanup failed" in note
        for note in getattr(raised.value, "__notes__", [])
    )
    assert calls == ["init-redis", "failing-manager", "close-redis"]


def test_application_mounts_workspace_routes():
    paths = TestClient(main.create_app()).get("/openapi.json").json()["paths"]

    assert "/workspaces" in paths
    assert "/workspaces/{workspace_id}/repositories" in paths
    assert "/workspaces/{workspace_id}/scan" in paths
    assert "/workspaces/{workspace_id}/activate" in paths
    assert "/workspaces/{workspace_id}/rag/index" in paths
    assert "/workspaces/{workspace_id}/rag/index/rebuild" in paths
    assert "/workspaces/{workspace_id}/rag/query" in paths


def test_http_requests_receive_safe_correlation_ids():
    client = TestClient(main.create_app())

    generated = client.get("/health")
    generated_id = generated.headers["x-request-id"]
    assert str(UUID(generated_id)) == generated_id

    preserved = client.get("/health", headers={"x-request-id": "client-request_42"})
    assert preserved.headers["x-request-id"] == "client-request_42"

    replaced = client.get("/health", headers={"x-request-id": "unsafe request id"})
    replaced_id = replaced.headers["x-request-id"]
    assert replaced_id != "unsafe request id"
    assert str(UUID(replaced_id)) == replaced_id
