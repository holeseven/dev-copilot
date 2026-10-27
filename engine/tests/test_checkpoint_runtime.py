# @author lxy
from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from engine.infra.checkpoint import open_sqlite_checkpointer, strict_checkpoint_serializer


def test_checkpoint_serializer_uses_strict_msgpack_allowlist():
    serializer = strict_checkpoint_serializer()

    assert serializer._allowed_msgpack_modules is None
    assert serializer.pickle_fallback is False


class ApprovalState(TypedDict):
    result: str


def _approval_graph(checkpointer):
    def approve(state: ApprovalState):
        decision = interrupt(
            {
                "type": "approval_required",
                "approval_id": "approval-1",
                "action": "apply_workspace_change",
                "message": "Approve?",
                "payload": {"workspace_id": "workspace-1"},
            }
        )
        return {"result": "approved" if decision["approved"] else "rejected"}

    workflow = StateGraph(ApprovalState)
    workflow.add_node("approve", approve)
    workflow.set_entry_point("approve")
    workflow.set_finish_point("approve")
    return workflow.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_sqlite_checkpoint_resumes_interrupt_after_reopen(tmp_path):
    database = tmp_path / "checkpoints.sqlite3"
    config = {
        "configurable": {
            "thread_id": "workspace-session",
            "runtime_object": object(),
        }
    }

    async with open_sqlite_checkpointer(database) as first_saver:
        first_graph = _approval_graph(first_saver)
        await first_graph.ainvoke({"result": ""}, config=config)

    async with open_sqlite_checkpointer(database) as second_saver:
        second_graph = _approval_graph(second_saver)
        snapshot = await second_graph.aget_state(config)
        assert snapshot.tasks[0].interrupts[0].value["approval_id"] == "approval-1"
        result = await second_graph.ainvoke(
            Command(
                resume={"approval_id": "approval-1", "approved": True}
            ),
            config=config,
        )

    assert result["result"] == "approved"
