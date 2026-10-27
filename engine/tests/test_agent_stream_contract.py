# @author lxy
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from engine.api import chat
from engine.core.workspace.registry import WorkspaceRegistry


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        events.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return events


def _app(tmp_path, graph) -> FastAPI:
    app = FastAPI()
    registry = WorkspaceRegistry(tmp_path / "metadata")
    registry.create("workspace-1", "Workspace 1")
    app.state.workspace_registry = registry
    app.state.mcp_managers = {}
    app.state.workspace_manager_lock = asyncio.Lock()
    app.state.agent_graph = graph
    app.state.dispatcher = object()
    app.include_router(chat.router, prefix="/chat")
    return app


def test_internal_thread_id_changes_with_workspace_generation():
    first = chat._thread_id("workspace-1", "generation-1", "session-1")
    second = chat._thread_id("workspace-1", "generation-2", "session-1")

    assert first != second
    assert first == chat._thread_id("workspace-1", "generation-1", "session-1")


@pytest.mark.asyncio
async def test_chat_requires_explicit_session_and_workspace_ids(tmp_path):
    class Graph:
        async def astream_events(self, initial_state, *, config, version):
            if False:
                yield

    app = _app(tmp_path, Graph())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        without_session = await client.post(
            "/chat", json={"message": "hello", "workspace_id": "workspace-1"}
        )
        without_workspace = await client.post(
            "/chat", json={"message": "hello", "session_id": "session-1"}
        )

    assert without_session.status_code == 422
    assert without_workspace.status_code == 422


@pytest.mark.asyncio
async def test_chat_stream_contract_and_graph_scope(tmp_path, monkeypatch):
    class Graph:
        initial_state = None
        config = None

        async def astream_events(self, initial_state, *, config, version):
            self.initial_state = initial_state
            self.config = config
            assert version == "v2"
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="hello")},
            }
            yield {
                "event": "on_tool_start",
                "name": "read_file",
                "data": {"input": {"path": "README.md"}},
            }
            yield {
                "event": "on_tool_end",
                "name": "read_file",
                "data": {"output": {"content": "ok"}},
            }
            yield {
                "event": "on_custom_event",
                "name": "file_change",
                "data": {
                    "tool_call_id": "apply-1",
                    "virtual_path": "service/app.py",
                    "operation": "replace",
                    "after_sha256": "after",
                    "repository_root": "C:/secret/root",
                    "new_content": "must-not-leak",
                },
            }
            yield {
                "event": "on_custom_event",
                "name": "verification",
                "data": {
                    "tool_call_id": "apply-1",
                    "profile": "python_pytest",
                    "status": "passed",
                    "exit_code": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "truncated": False,
                    "environment": {"SECRET": "must-not-leak"},
                },
            }
            yield {
                "event": "on_chain_stream",
                "data": {
                    "chunk": {
                        "__interrupt__": (
                            SimpleNamespace(
                                value={
                                    "type": "approval_required",
                                    "approval_id": "approval-1",
                                    "action": "write_file",
                                    "message": "Approve change?",
                                    "details": "README.md",
                                    "payload": {"path": "README.md"},
                                }
                            ),
                        ),
                    }
                },
            }

    manager = object()

    async def get_manager(request, workspace):
        return manager

    monkeypatch.setattr(chat, "get_workspace_manager", get_manager)
    graph = Graph()
    app = _app(tmp_path, graph)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={
                "message": "inspect the repository",
                "session_id": "session-1",
                "workspace_id": "workspace-1",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _parse_sse(response.text) == [
        ("message", {"token": "hello"}),
        (
            "tool",
            {
                "status": "start",
                "tool_name": "read_file",
            },
        ),
        (
            "tool",
            {
                "status": "end",
                "tool_name": "read_file",
            },
        ),
        (
            "file_change",
            {
                "tool_call_id": "apply-1",
                "virtual_path": "service/app.py",
                "operation": "replace",
                "after_sha256": "after",
            },
        ),
        (
            "verification",
            {
                "tool_call_id": "apply-1",
                "profile": "python_pytest",
                "status": "passed",
                "exit_code": 0,
                "truncated": False,
            },
        ),
        (
            "approval_required",
            {
                "approval_id": "approval-1",
                "action": "write_file",
                "message": "Approve change?",
                "details": "",
                "payload": {},
            },
        ),
        ("done", {"status": "awaiting_approval"}),
    ]
    assert graph.initial_state["session_id"] == "session-1"
    assert graph.config["configurable"]["session_id"] == "session-1"
    assert graph.config["configurable"]["thread_id"]
    assert graph.config["configurable"]["thread_id"] != "session-1"
    assert graph.config["configurable"]["workspace_id"] == "workspace-1"
    assert graph.config["configurable"]["mcp_manager"] is manager
    assert graph.config["configurable"]["dispatcher"] is app.state.dispatcher


@pytest.mark.asyncio
async def test_chat_stream_emits_error_before_done(tmp_path, monkeypatch):
    class Graph:
        async def astream_events(self, initial_state, *, config, version):
            raise RuntimeError("graph failed")
            yield

    async def get_manager(request, workspace):
        return object()

    monkeypatch.setattr(chat, "get_workspace_manager", get_manager)
    app = _app(tmp_path, Graph())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={
                "message": "inspect the repository",
                "session_id": "session-1",
                "workspace_id": "workspace-1",
            },
        )

    assert response.status_code == 200
    assert _parse_sse(response.text) == [
        (
            "error",
            {
                "code": "agent_execution_failed",
                "message": "Agent 执行失败，请重试",
            },
        ),
        ("done", {"status": "failed"}),
    ]
    assert "graph failed" not in response.text


@pytest.mark.asyncio
async def test_resume_validates_checkpoint_approval_and_reuses_internal_thread(
    tmp_path, monkeypatch
):
    class Graph:
        config = None
        command = None

        async def aget_state(self, config):
            self.config = config
            approval = SimpleNamespace(
                value={
                    "type": "approval_required",
                    "approval_id": "approval-1",
                }
            )
            return SimpleNamespace(
                tasks=(SimpleNamespace(interrupts=(approval,)),)
            )

        async def astream_events(self, command, *, config, version):
            self.command = command
            self.config = config
            if False:
                yield

    async def get_manager(request, workspace):
        return object()

    monkeypatch.setattr(chat, "get_workspace_manager", get_manager)
    graph = Graph()
    app = _app(tmp_path, graph)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat/resume",
            json={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "approval_id": "approval-1",
                "approved": True,
            },
        )

    assert response.status_code == 200
    assert _parse_sse(response.text) == [("done", {"status": "completed"})]
    workspace = app.state.workspace_registry.get("workspace-1")
    assert graph.config["configurable"]["thread_id"] == chat._thread_id(
        "workspace-1", workspace.generation, "session-1"
    )
    assert graph.command.resume["approval_id"] == "approval-1"


@pytest.mark.asyncio
async def test_resume_rejects_non_pending_approval_without_running_graph(
    tmp_path, monkeypatch
):
    class Graph:
        started = False

        async def aget_state(self, config):
            return SimpleNamespace(tasks=())

        async def astream_events(self, command, *, config, version):
            self.started = True
            if False:
                yield

    async def get_manager(request, workspace):
        return object()

    monkeypatch.setattr(chat, "get_workspace_manager", get_manager)
    graph = Graph()
    app = _app(tmp_path, graph)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat/resume",
            json={
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "approval_id": "stale-approval",
                "approved": True,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "approval is not pending for this workspace session"
    )
    assert graph.started is False
