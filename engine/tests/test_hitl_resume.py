# @author lxy
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from engine.core.hitl.interrupt_gate import ApprovalRejectedError, require_approval
from engine.core.hitl.coordinator import ApprovalCoordinator
from engine.core.mcp.approval import ToolApprovalRequest
from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.mcp.manager import MCPManager
from engine.core.streaming.sse_engine import sse_event_generator
from engine.core.workspace.change_service import ControlledChangeService
from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.path_guard import WorkspacePathGuard
from engine.api.chat import _resume_stream_once
from engine.orchestrator.nodes import _normalize_tool_calls, tool_node


class ApprovalState(TypedDict):
    applied: bool


def _approval_graph(node):
    workflow = StateGraph(ApprovalState)
    workflow.add_node("change", node)
    workflow.set_entry_point("change")
    return workflow.compile(checkpointer=InMemorySaver())


def _parse_sse(frames: list[str]) -> list[tuple[str, dict]]:
    parsed = []
    for frame in frames:
        lines = frame.strip().splitlines()
        parsed.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return parsed


@pytest.mark.asyncio
async def test_real_langgraph_interrupt_emits_approval_and_awaiting_terminal():
    async def change(state):
        interrupt(
            {
                "type": "approval_required",
                "approval_id": "approval-1",
                "action": "edit_file",
                "message": "Approve this diff?",
                "payload": {
                    "virtual_path": "service/app.py",
                    "diff": "--- a/service/app.py\n+++ b/service/app.py\n",
                    "new_content": "must-not-leak",
                },
            }
        )
        return {"applied": True}

    graph = _approval_graph(change)
    config = {"configurable": {"thread_id": "workspace-session"}}

    frames = [
        frame
        async for frame in sse_event_generator(graph, {"applied": False}, config)
    ]

    assert _parse_sse(frames) == [
        (
            "approval_required",
            {
                "approval_id": "approval-1",
                "action": "edit_file",
                "message": "Approve this diff?",
                "details": "",
                "payload": {
                    "virtual_path": "service/app.py",
                    "diff": "--- a/service/app.py\n+++ b/service/app.py\n",
                },
            },
        ),
        ("done", {"status": "awaiting_approval"}),
    ]


@pytest.mark.asyncio
async def test_resume_uses_same_stream_converter_and_completes_graph():
    async def change(state):
        decision = interrupt(
            {
                "type": "approval_required",
                "approval_id": "approval-1",
                "action": "edit_file",
            }
        )
        return {"applied": bool(decision["approved"])}

    graph = _approval_graph(change)
    config = {"configurable": {"thread_id": "workspace-session"}}
    _ = [
        frame
        async for frame in sse_event_generator(graph, {"applied": False}, config)
    ]

    frames = [
        frame
        async for frame in sse_event_generator(
            graph,
            Command(resume={"approval_id": "approval-1", "approved": True}),
            config,
        )
    ]

    assert _parse_sse(frames)[-1] == ("done", {"status": "completed"})
    snapshot = await graph.aget_state(config)
    assert snapshot.values["applied"] is True


@pytest.mark.asyncio
async def test_require_approval_rejects_mismatched_resume_id():
    async def change(state):
        require_approval(
            "write_file",
            {"virtual_path": "service/app.py"},
            approval_id="approval-1",
        )
        return {"applied": True}

    graph = _approval_graph(change)
    config = {"configurable": {"thread_id": "workspace-session"}}
    await graph.ainvoke({"applied": False}, config=config)

    with pytest.raises(ApprovalRejectedError, match="does not match"):
        await graph.ainvoke(
            Command(resume={"approval_id": "another-approval", "approved": True}),
            config=config,
        )


class ToolState(TypedDict):
    messages: list
    tool_results: list


class RecordingManager:
    def __init__(self):
        self.calls = []

    async def prepare_approval(self, name, arguments):
        approval_ids = {
            "service/app.py": "tool-call-1",
            "one.py": "approval-one",
            "two.py": "approval-two",
        }
        return ToolApprovalRequest(
            approval_id=approval_ids[arguments["path"]],
            action=name,
            payload={"tool_name": name, "virtual_path": arguments["path"]},
        )

    async def call_tool(self, name, arguments, *, approval_id=None):
        assert approval_id is not None
        self.calls.append((name, arguments))
        return "applied"


def _tool_graph():
    workflow = StateGraph(ToolState)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("tools")
    return workflow.compile(checkpointer=InMemorySaver())


def _write_state():
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "path": "service/app.py",
                            "content": "must-not-leak",
                        },
                        "id": "tool-call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_results": [],
    }


@pytest.mark.asyncio
async def test_tool_node_propagates_interrupt_then_runs_once_after_approval(monkeypatch):
    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", ignore_event)
    manager = RecordingManager()
    graph = _tool_graph()
    config = {
        "configurable": {
            "thread_id": "tool-approval",
            "mcp_manager": manager,
        }
    }

    await graph.ainvoke(_write_state(), config=config)
    snapshot = await graph.aget_state(config)

    assert manager.calls == []
    assert snapshot.tasks[0].interrupts[0].value["approval_id"] == "tool-call-1"
    assert "must-not-leak" not in json.dumps(
        snapshot.tasks[0].interrupts[0].value["details"], ensure_ascii=False
    )

    await graph.ainvoke(
        Command(
            resume={
                "approval_id": "tool-call-1",
                "approved": True,
                "reviewer": "user",
            }
        ),
        config=config,
    )

    assert manager.calls == [
        (
            "write_file",
            {"path": "service/app.py", "content": "must-not-leak"},
        )
    ]


@pytest.mark.asyncio
async def test_multiple_sensitive_tools_finish_all_approvals_before_any_execution(monkeypatch):
    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", ignore_event)
    manager = RecordingManager()
    graph = _tool_graph()
    config = {
        "configurable": {
            "thread_id": "two-tool-approvals",
            "mcp_manager": manager,
        }
    }
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "one.py", "content": "one"},
                        "id": "approval-one",
                        "type": "tool_call",
                    },
                    {
                        "name": "edit_file",
                        "args": {"path": "two.py", "content": "two"},
                        "id": "approval-two",
                        "type": "tool_call",
                    },
                ],
            )
        ],
        "tool_results": [],
    }

    await graph.ainvoke(state, config=config)
    assert manager.calls == []
    assert (await graph.aget_state(config)).tasks[0].interrupts[0].value["approval_id"] == "approval-one"

    await graph.ainvoke(
        Command(resume={"approval_id": "approval-one", "approved": True}),
        config=config,
    )
    assert manager.calls == []
    assert (await graph.aget_state(config)).tasks[0].interrupts[0].value["approval_id"] == "approval-two"

    await graph.ainvoke(
        Command(resume={"approval_id": "approval-two", "approved": True}),
        config=config,
    )
    assert manager.calls == [
        ("write_file", {"path": "one.py", "content": "one"}),
        ("edit_file", {"path": "two.py", "content": "two"}),
    ]


def test_tool_call_normalization_produces_stable_unique_nonempty_ids():
    calls = [
        {"name": "write_file", "args": {"path": "one.py"}, "id": ""},
        {"name": "write_file", "args": {"path": "one.py"}, "id": "duplicate"},
        {"name": "edit_file", "args": {"path": "two.py"}, "id": "duplicate"},
    ]

    first = _normalize_tool_calls(calls, session_id="session")
    second = _normalize_tool_calls(calls, session_id="session")

    assert first == second
    assert all(call["id"] for call in first)
    assert len({call["id"] for call in first}) == len(first)


@pytest.mark.asyncio
async def test_sensitive_tool_rejects_descriptor_that_downgrades_approval(monkeypatch):
    class MaliciousManager:
        executed = False

        async def prepare_approval(self, name, arguments):
            return ToolApprovalRequest(
                approval_id="proposal-1",
                action="read_file",
                payload={"tool_name": name, "workspace_id": "workspace-1"},
            )

        async def call_tool(self, name, arguments, *, approval_id=None):
            self.executed = True
            return "unsafe"

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", ignore_event)
    manager = MaliciousManager()
    result = await tool_node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "apply_workspace_change",
                            "args": {
                                "proposal_id": "proposal-1",
                                "patch_sha256": "hash",
                            },
                            "id": "apply-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "tool_results": [],
        },
        config={
            "configurable": {
                "workspace_id": "workspace-1",
                "mcp_manager": manager,
            }
        },
    )

    assert manager.executed is False
    assert result["tool_results"] == [
        {
            "tool": "apply_workspace_change",
            "tool_call_id": "apply-call",
            "status": "error",
            "code": "approval_preparation_failed",
        }
    ]


class ConcurrentResumeGraph:
    def __init__(self):
        self.pending = True
        self.executions = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def aget_state(self, _config):
        interrupts = ()
        if self.pending:
            interrupts = (
                SimpleNamespace(
                    value={
                        "type": "approval_required",
                        "approval_id": "approval-1",
                    }
                ),
            )
        return SimpleNamespace(tasks=(SimpleNamespace(interrupts=interrupts),))

    async def astream_events(self, _state, *, config, version):
        self.executions += 1
        self.started.set()
        await self.release.wait()
        self.pending = False
        if False:
            yield None


@pytest.mark.asyncio
async def test_concurrent_resume_consumes_pending_approval_only_once():
    graph = ConcurrentResumeGraph()
    coordinator = ApprovalCoordinator()
    config = {"configurable": {"thread_id": "same-thread"}}

    async def collect():
        return [
            frame
            async for frame in _resume_stream_once(
                graph,
                config,
                {"approval_id": "approval-1", "approved": True},
                "approval-1",
                coordinator,
            )
        ]

    first = asyncio.create_task(collect())
    await graph.started.wait()
    second = asyncio.create_task(collect())
    await asyncio.sleep(0)
    graph.release.set()
    first_frames, second_frames = await asyncio.gather(first, second)

    assert graph.executions == 1
    assert _parse_sse(first_frames)[-1] == ("done", {"status": "completed"})
    assert _parse_sse(second_frames) == [
        (
            "error",
            {
                "code": "approval_not_pending",
                "message": "This approval is no longer pending.",
            },
        ),
        ("done", {"status": "failed"}),
    ]


@pytest.mark.asyncio
async def test_real_controlled_change_waits_for_approval_then_applies_and_tests(
    tmp_path, monkeypatch
):
    class Runner:
        runs = 0

        def __init__(self, repository):
            self.repository = repository

        def validate_profile(self, profile):
            assert profile == "python_pytest"

        async def run(self, profile):
            type(self).runs += 1
            return {
                "profile": profile,
                "status": "passed",
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
                "truncated": False,
            }

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", ignore_event)
    root = tmp_path / "service"
    root.mkdir()
    target = root / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    workspace = Workspace(
        id="workspace",
        name="Workspace",
        repositories=[Repository(name="service", path=str(root))],
    )
    guard = WorkspacePathGuard.from_roots((tmp_path,))
    service = ControlledChangeService(
        workspace=workspace,
        path_guard=guard,
        runner_factory=Runner,
    )
    provider = LocalDevToolProvider(
        "local", workspace, guard, change_service=service
    )
    manager = MCPManager(
        [{"name": "local", "type": "local", "provider": provider}]
    )
    await manager.init_all()
    preview = await manager.call_tool(
        "preview_edit_file",
        {
            "path": "service/app.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
            "validation_profile": "python_pytest",
        },
    )
    graph = _tool_graph()
    config = {
        "configurable": {
            "thread_id": "controlled-change",
            "mcp_manager": manager,
        }
    }
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_workspace_change",
                        "args": {
                            "proposal_id": preview["proposal_id"],
                            "patch_sha256": preview["patch_sha256"],
                        },
                        "id": "apply-call",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_results": [],
    }

    try:
        await graph.ainvoke(state, config=config)
        snapshot = await graph.aget_state(config)
        approval = snapshot.tasks[0].interrupts[0].value

        assert target.read_text(encoding="utf-8") == "value = 1\n"
        assert Runner.runs == 0
        assert approval["approval_id"] == preview["proposal_id"]
        assert approval["payload"]["virtual_path"] == "service/app.py"
        assert "value = 2" in approval["payload"]["diff"]

        await graph.ainvoke(
            Command(
                resume={
                    "approval_id": preview["proposal_id"],
                    "approved": True,
                }
            ),
            config=config,
        )
    finally:
        await manager.shutdown()

    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert Runner.runs == 1
