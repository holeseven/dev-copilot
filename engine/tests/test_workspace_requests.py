import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessageChunk

from engine.api import chat, tools, workspaces
from engine.core.workspace.models import Workspace
from engine.core.streaming.sse_engine import sse_event_generator
from engine.core.workspace.registry import WorkspaceRegistry


class FakeDispatcher:
    async def route(self, query):
        raise AssertionError("fake graph does not run nodes")


class CapturingGraph:
    def __init__(self):
        self.initial_state = None
        self.config = None

    async def astream_events(self, initial_state, *, config, version):
        self.initial_state = initial_state
        self.config = config
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": AIMessageChunk(content="answer")},
        }


def _app(tmp_path):
    app = FastAPI()
    app.state.workspace_registry = WorkspaceRegistry(tmp_path / "metadata")
    app.state.mcp_managers = {}
    app.state.workspace_manager_lock = asyncio.Lock()
    app.state.agent_graph = CapturingGraph()
    app.state.dispatcher = FakeDispatcher()
    app.include_router(chat.router, prefix="/chat")
    app.include_router(tools.router, prefix="/tools")
    return app


async def _close_managers(app):
    for manager in app.state.mcp_managers.values():
        await manager.shutdown()


@pytest.mark.asyncio
async def test_concurrent_manager_creation_reuses_one_initialized_instance(monkeypatch):
    created = []

    class Manager:
        init_calls = 0

        def __init__(self, configs):
            created.append(self)

        async def init_all(self):
            type(self).init_calls += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(workspaces, "MCPManager", Manager)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                mcp_managers={}, workspace_manager_lock=asyncio.Lock()
            )
        )
    )
    workspace = Workspace(id="shared", name="Shared")

    first, second = await asyncio.gather(
        workspaces.get_workspace_manager(request, workspace),
        workspaces.get_workspace_manager(request, workspace),
    )

    assert first is second
    assert created == [first]
    assert Manager.init_calls == 1


def _controlled_manager(monkeypatch):
    init_started = asyncio.Event()
    release_init = asyncio.Event()

    class Manager:
        instances = []

        def __init__(self, configs):
            self.provider = configs[0]["provider"]
            self.closed = False
            type(self).instances.append(self)

        async def init_all(self):
            if len(type(self).instances) == 1:
                init_started.set()
                await release_init.wait()

        async def shutdown(self):
            self.closed = True

    monkeypatch.setattr(workspaces, "MCPManager", Manager)
    return Manager, init_started, release_init


def _runtime_request(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "metadata")
    state = SimpleNamespace(
        workspace_registry=registry,
        mcp_managers={},
        workspace_manager_lock=asyncio.Lock(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state)), registry


@pytest.mark.asyncio
async def test_repository_add_invalidates_manager_created_during_initialization(
    tmp_path, monkeypatch
):
    Manager, init_started, release_init = _controlled_manager(monkeypatch)
    request, registry = _runtime_request(tmp_path)
    workspace = registry.create("shared", "Shared")
    repository = tmp_path / "new-repo"
    repository.mkdir()

    create_task = asyncio.create_task(
        workspaces.get_workspace_manager(request, workspace)
    )
    await init_started.wait()
    add_task = asyncio.create_task(
        workspaces.add_repository(
            "shared",
            workspaces.RepositoryCreate(
                name="new-repo", local_path=str(repository)
            ),
            request,
        )
    )
    await asyncio.sleep(0)
    release_init.set()
    old_manager, updated_workspace = await asyncio.gather(create_task, add_task)

    cached_after_invalidation = request.app.state.mcp_managers.get("shared")
    assert cached_after_invalidation is None
    assert old_manager.closed is True

    new_manager = await workspaces.get_workspace_manager(request, updated_workspace)
    assert new_manager is not old_manager
    assert await new_manager.provider.call_tool("list_dir", {"path": "."}) == [
        "new-repo"
    ]
    assert len(Manager.instances) == 2


@pytest.mark.asyncio
async def test_workspace_delete_closes_manager_created_during_initialization(
    tmp_path, monkeypatch
):
    _, init_started, release_init = _controlled_manager(monkeypatch)
    request, registry = _runtime_request(tmp_path)
    workspace = registry.create("doomed", "Doomed")

    create_task = asyncio.create_task(
        workspaces.get_workspace_manager(request, workspace)
    )
    await init_started.wait()
    delete_task = asyncio.create_task(
        workspaces.delete_workspace("doomed", request)
    )
    await asyncio.sleep(0)
    release_init.set()
    old_manager, _ = await asyncio.gather(create_task, delete_task)

    assert registry.get("doomed") is None
    assert request.app.state.mcp_managers.get("doomed") is None
    assert old_manager.closed is True


@pytest.mark.asyncio
async def test_tools_resolves_active_and_explicit_workspaces(tmp_path):
    app = _app(tmp_path)
    registry = app.state.workspace_registry
    registry.create("active", "Active")
    registry.create("explicit", "Explicit")
    registry.set_active("active")
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            active = await client.get("/tools")
            explicit = await client.get("/tools", params={"workspace_id": "explicit"})
    finally:
        await _close_managers(app)

    expected_names = ["list_dir", "find_files", "grep_code", "read_file", "code_search"]
    assert active.status_code == 200
    assert [tool["name"] for tool in active.json()["tools"]] == expected_names
    assert [tool["name"] for tool in explicit.json()["tools"]] == expected_names
    assert set(app.state.mcp_managers) == {"active", "explicit"}


@pytest.mark.asyncio
async def test_chat_passes_explicit_workspace_runtime_dependencies(tmp_path):
    app = _app(tmp_path)
    registry = app.state.workspace_registry
    registry.create("active", "Active")
    registry.create("explicit", "Explicit")
    registry.set_active("active")
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat",
                json={
                    "message": "hello",
                    "session_id": "session-explicit",
                    "workspace_id": "explicit",
                },
            )
    finally:
        await _close_managers(app)

    graph = app.state.agent_graph
    configurable = graph.config["configurable"]
    assert response.status_code == 200
    assert "event: message" in response.text
    assert configurable["workspace_id"] == "explicit"
    assert configurable["session_id"] == "session-explicit"
    assert configurable["mcp_manager"] is app.state.mcp_managers["explicit"]
    assert configurable["dispatcher"] is app.state.dispatcher


@pytest.mark.asyncio
async def test_chat_and_tools_return_workspace_resolution_errors(tmp_path):
    app = _app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        no_active_chat = await client.post("/chat", json={"message": "hello"})
        no_active_tools = await client.get("/tools")
        unknown_chat = await client.post(
            "/chat", json={"message": "hello", "workspace_id": "missing"}
        )
        unknown_resume = await client.post(
            "/chat/resume",
            json={"thread_id": "session", "approved": True, "workspace_id": "missing"},
        )
        unknown_tools = await client.get("/tools", params={"workspace_id": "missing"})

    assert no_active_chat.status_code == 409
    assert no_active_tools.status_code == 409
    assert unknown_chat.status_code == 404
    assert unknown_resume.status_code == 404
    assert unknown_tools.status_code == 404


@pytest.mark.asyncio
async def test_sse_maps_tool_status_custom_event_to_tool_event():
    class Graph:
        async def astream_events(self, initial_state, *, config, version):
            yield {
                "event": "on_custom_event",
                "name": "tool_status",
                "data": {"status": "start", "tool_name": "read_file"},
            }

    events = [event async for event in sse_event_generator(Graph(), {}, {})]

    assert events[0].startswith("event: tool\n")
    assert json.loads(events[0].split("data: ", 1)[1]) == {
        "status": "start",
        "tool_name": "read_file",
    }
