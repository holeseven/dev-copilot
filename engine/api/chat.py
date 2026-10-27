# @author lxy
"""
Chat API 路由

- POST /chat：接收 {message, session_id}，返回 SSE StreamingResponse
- POST /chat/resume：HITL 恢复接口，接收 {thread_id, approved, reason}
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from engine.core.hitl.coordinator import ApprovalCoordinator
from engine.core.streaming.sse_engine import (
    _interrupt_approvals,
    _sse_line,
    sse_event_generator,
)
from engine.api.workspaces import get_workspace_manager, resolve_workspace

router = APIRouter()


def _thread_id(workspace_id: str, generation: str, session_id: str) -> str:
    value = f"{workspace_id}\0{generation}\0{session_id}".encode("utf-8")
    return f"run-{hashlib.sha256(value).hexdigest()}"


def _approval_coordinator(request: Request) -> ApprovalCoordinator:
    coordinator = getattr(request.app.state, "approval_coordinator", None)
    if coordinator is None:
        coordinator = ApprovalCoordinator()
        request.app.state.approval_coordinator = coordinator
    return coordinator


@dataclass
class _DurableResumeRun:
    frames: list[str] = field(default_factory=list)
    frame_bytes: int = 0
    base_sequence: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: bool = False
    task: asyncio.Task | None = None

    async def publish(self, frame: str) -> None:
        async with self.condition:
            self.frames.append(frame)
            self.frame_bytes += len(frame.encode("utf-8"))
            while len(self.frames) > 256 or self.frame_bytes > 1024 * 1024:
                removed = self.frames.pop(0)
                self.frame_bytes -= len(removed.encode("utf-8"))
                self.base_sequence += 1
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done = True
            self.condition.notify_all()

    async def subscribe(self):
        cursor = self.base_sequence
        while True:
            async with self.condition:
                await self.condition.wait_for(
                    lambda: cursor < self.base_sequence + len(self.frames)
                    or self.done
                )
                if cursor < self.base_sequence:
                    cursor = self.base_sequence
                if cursor < self.base_sequence + len(self.frames):
                    frame = self.frames[cursor - self.base_sequence]
                    cursor += 1
                elif self.done:
                    return
                else:  # pragma: no cover
                    continue
            yield frame


def _resume_registry(app) -> tuple[dict, asyncio.Lock]:
    if not hasattr(app.state, "approval_runs"):
        app.state.approval_runs = {}
    if not hasattr(app.state, "approval_runs_lock"):
        app.state.approval_runs_lock = asyncio.Lock()
    return app.state.approval_runs, app.state.approval_runs_lock


def _run_key(workspace_id: str, generation: str, approval_id: str, session_id: str):
    return (
        workspace_id,
        generation,
        approval_id,
        hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
    )


# ─── 请求模型 ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """聊天请求体"""
    message: str = Field(..., min_length=1, max_length=20_000, description="用户消息")
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    workspace_id: str = Field(..., min_length=1, max_length=128, description="Workspace ID")


class PendingRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)


class OutcomeRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)
    approval_id: str = Field(..., min_length=1, max_length=256)


class CheckpointUnavailableError(RuntimeError):
    """The durable graph state could not be inspected safely."""


class ResumeRequest(BaseModel):
    """HITL 恢复请求体"""
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    workspace_id: str = Field(..., min_length=1, max_length=128, description="Workspace ID")
    approval_id: str = Field(..., min_length=1, max_length=256, description="待审批变更 ID")
    approved: bool = Field(..., description="是否批准")
    reason: Optional[str] = Field(default="", max_length=1000, description="拒绝原因（可选）")


# ─── POST /chat ───────────────────────────────────────────────────────────────

@router.post("")
async def chat(request: ChatRequest, http_request: Request):
    """
    聊天接口：接收用户消息，返回 SSE 流式响应。

    SSE 事件类型：
    - event: message — 流式 token
    - event: tool — 工具调用状态
    - event: approval_required — HITL 审批请求
    - event: done — 流结束
    """
    logger.info(
        "收到聊天请求 | session_hash={} | message_length={}",
        hashlib.sha256(request.session_id.encode("utf-8")).hexdigest()[:12],
        len(request.message),
    )

    workspace = resolve_workspace(http_request, request.workspace_id)
    manager = await get_workspace_manager(http_request, workspace)
    graph = http_request.app.state.agent_graph

    # 初始状态
    initial_state = {
        "messages": [],
        "session_id": request.session_id,
        "user_query": request.message,
        "target_agent_id": "",
        "complexity_level": "L2",
        "iteration_count": 0,
        "max_iterations": 35,
        "should_terminate": False,
        "tool_results": [],
    }

    config = {
        "recursion_limit": 150,
        "configurable": {
            "thread_id": _thread_id(workspace.id, workspace.generation, request.session_id),
            "workspace_id": workspace.id,
            "session_id": request.session_id,
            "mcp_manager": manager,
            "dispatcher": http_request.app.state.dispatcher,
        },
    }

    try:
        pending_ids = await _pending_approval_ids(graph, config)
    except CheckpointUnavailableError:
        raise _checkpoint_unavailable_http_error() from None
    if pending_ids:
        raise HTTPException(
            status_code=409,
            detail="this workspace session is awaiting approval",
        )

    return StreamingResponse(
        _chat_stream_once(
            graph,
            initial_state,
            config,
            _approval_coordinator(http_request),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── POST /chat/resume ────────────────────────────────────────────────────────

@router.post("/outcome")
async def outcome(request: OutcomeRequest, http_request: Request):
    """Return one durable, sanitized approval outcome in its exact scope."""

    workspace = resolve_workspace(http_request, request.workspace_id)
    stored = http_request.app.state.proposal_store.get_outcome(
        workspace.id,
        workspace.generation,
        request.approval_id,
        request.session_id,
    )
    if stored is None:
        raise HTTPException(status_code=404, detail="approval outcome not found")
    return _outcome_response(workspace.id, request.session_id, stored)


def _outcome_response(workspace_id: str, session_id: str, stored) -> dict:
    result = stored.result if isinstance(stored.result, dict) else {}
    response = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "approval_id": stored.approval_id,
        "status": stored.state,
        "terminal": stored.state != "applying",
    }
    if isinstance(result.get("mutation"), dict):
        response["mutation"] = result["mutation"]
    if isinstance(result.get("validation"), dict):
        response["validation"] = result["validation"]
    return response

@router.post("/pending")
async def pending(request: PendingRequest, http_request: Request):
    """Return sanitized approvals stored for one external workspace session."""

    workspace = resolve_workspace(http_request, request.workspace_id)
    config = {
        "configurable": {
            "thread_id": _thread_id(
                workspace.id, workspace.generation, request.session_id
            ),
            "workspace_id": workspace.id,
            "session_id": request.session_id,
        }
    }
    lock = await _approval_coordinator(http_request).lock_for(
        config["configurable"]["thread_id"]
    )
    async with lock:
        try:
            approvals = await _pending_approvals(
                http_request.app.state.agent_graph, config
            )
        except CheckpointUnavailableError:
            raise _checkpoint_unavailable_http_error() from None
    response = {
        "workspace_id": request.workspace_id,
        "session_id": request.session_id,
        "status": "awaiting_approval" if approvals else "idle",
        "approvals": approvals,
    }
    if hasattr(http_request.app.state, "proposal_store"):
        outcomes = http_request.app.state.proposal_store.list_outcomes(
            workspace.id, workspace.generation, request.session_id
        )
        response["outcomes"] = [
            _outcome_response(workspace.id, request.session_id, item)
            for item in outcomes
        ]
    return response


@router.post("/resume")
async def resume(request: ResumeRequest, http_request: Request):
    """
    HITL 恢复接口：外部审批后恢复图执行。

    通过 LangGraph 的 Command(resume=...) 机制继续被 interrupt 暂停的图执行。
    """
    logger.info(
        "收到 HITL 恢复请求 | session_hash={} | approved={}",
        hashlib.sha256(request.session_id.encode("utf-8")).hexdigest()[:12],
        request.approved,
    )

    try:
        workspace = resolve_workspace(http_request, request.workspace_id)
        store = getattr(http_request.app.state, "proposal_store", None)
        if store is not None:
            existing = store.get_outcome(
                workspace.id,
                workspace.generation,
                request.approval_id,
                request.session_id,
            )
            if existing is not None and existing.state != "applying":
                return _outcome_streaming_response(
                    workspace.id, request.session_id, existing
                )
        manager = await get_workspace_manager(http_request, workspace)

        # 构建恢复值
        resume_value = {
            "approval_id": request.approval_id,
            "approved": request.approved,
            "reason": request.reason or "",
            "reviewer": "user",
        }

        graph = http_request.app.state.agent_graph

        config = {
            "recursion_limit": 150,
            "configurable": {
                "thread_id": _thread_id(
                    workspace.id, workspace.generation, request.session_id
                ),
                "workspace_id": workspace.id,
                "session_id": request.session_id,
                "mcp_manager": manager,
                "dispatcher": http_request.app.state.dispatcher,
            },
        }

        if store is None:
            pending_ids = await _pending_approval_ids(graph, config)
            if request.approval_id not in pending_ids:
                raise HTTPException(
                    status_code=409,
                    detail="approval is not pending for this workspace session",
                )
            return _streaming_response(
                _resume_stream_once(
                    graph,
                    config,
                    resume_value,
                    request.approval_id,
                    _approval_coordinator(http_request),
                )
            )

        registry, registry_lock = _resume_registry(http_request.app)
        key = _run_key(
            workspace.id, workspace.generation, request.approval_id, request.session_id
        )
        async with registry_lock:
            current_workspace = http_request.app.state.workspace_registry.get(
                workspace.id
            )
            if (
                current_workspace is None
                or current_workspace.generation != workspace.generation
            ):
                raise HTTPException(
                    status_code=409,
                    detail="workspace changed while approval was starting",
                )
            run = registry.get(key)
            if run is None:
                existing = store.get_outcome(
                    workspace.id,
                    workspace.generation,
                    request.approval_id,
                    request.session_id,
                )
                if existing is not None and existing.state == "applying":
                    store.reconcile_applying(
                        workspace,
                        http_request.app.state.workspace_registry.path_guard,
                    )
                    existing = store.get_outcome(
                        workspace.id,
                        workspace.generation,
                        request.approval_id,
                        request.session_id,
                    )
                    return _outcome_streaming_response(
                        workspace.id, request.session_id, existing
                    )

                pending_ids = await _pending_approval_ids(graph, config)
                if request.approval_id not in pending_ids:
                    raise HTTPException(
                        status_code=409,
                        detail="approval is not pending for this workspace session",
                    )
                outcome, claimed = store.claim_outcome(
                    workspace.id,
                    workspace.generation,
                    request.approval_id,
                    request.session_id,
                )
                if outcome is None:
                    raise HTTPException(status_code=404, detail="approval not found")
                if not claimed:
                    raise HTTPException(status_code=409, detail="approval already claimed")
                run = _DurableResumeRun()
                registry[key] = run
                run.task = asyncio.create_task(
                    _execute_resume_run(
                        run=run,
                        graph=graph,
                        config=config,
                        resume_value=resume_value,
                        approval_id=request.approval_id,
                        coordinator=_approval_coordinator(http_request),
                        store=store,
                        workspace_id=workspace.id,
                        generation=workspace.generation,
                        session_id=request.session_id,
                        registry=registry,
                        registry_lock=registry_lock,
                        key=key,
                    ),
                    name=f"approval-{request.approval_id[:12]}",
                )

        # 使用 Command(resume=...) 恢复执行
        return _streaming_response(run.subscribe())

    except HTTPException:
        raise
    except CheckpointUnavailableError:
        raise _checkpoint_unavailable_http_error() from None
    except ImportError:
        raise HTTPException(status_code=501, detail="LangGraph Command 模块不可用")
    except Exception:
        logger.error("HITL resume failed | code=resume_failed")
        raise HTTPException(status_code=500, detail="unable to resume agent run")


def _streaming_response(stream) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _outcome_streaming_response(workspace_id: str, session_id: str, stored):
    async def replay():
        yield _sse_line(
            "outcome", _outcome_response(workspace_id, session_id, stored)
        )
        yield _sse_line("done", {"status": "completed"})

    return _streaming_response(replay())


async def _pending_approval_ids(graph, config: dict) -> set[str]:
    """Read approval ids from the checkpoint instead of trusting the client."""
    return {
        approval["approval_id"]
        for approval in await _pending_approvals(graph, config)
    }


async def _pending_approvals(graph, config: dict) -> list[dict]:
    """Read sanitized checkpoint approvals and fail closed on storage errors."""

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.warning("Unable to inspect pending approvals | code=checkpoint_unavailable")
        raise CheckpointUnavailableError("checkpoint state is unavailable") from exc
    pending: list[dict] = []
    seen: set[str] = set()
    for task in getattr(snapshot, "tasks", ()):
        for item in getattr(task, "interrupts", ()):
            for approval in _interrupt_approvals({"__interrupt__": (item,)}):
                approval_id = approval["approval_id"]
                if approval_id not in seen:
                    seen.add(approval_id)
                    pending.append(approval)
    return pending


def _checkpoint_unavailable_http_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "checkpoint_unavailable",
            "message": "Agent checkpoint storage is temporarily unavailable.",
        },
    )


async def _chat_stream_once(
    graph,
    initial_state: dict,
    config: dict,
    coordinator: ApprovalCoordinator,
):
    thread_id = config["configurable"]["thread_id"]
    lock = await coordinator.lock_for(thread_id)
    async with lock:
        try:
            pending_ids = await _pending_approval_ids(graph, config)
        except CheckpointUnavailableError:
            yield _sse_line(
                "error",
                {
                    "code": "checkpoint_unavailable",
                    "message": "Agent checkpoint storage is temporarily unavailable.",
                },
            )
            yield _sse_line("done", {"status": "failed"})
            return
        if pending_ids:
            yield _sse_line(
                "error",
                {
                    "code": "approval_pending",
                    "message": "This session is awaiting an approval decision.",
                },
            )
            yield _sse_line("done", {"status": "failed"})
            return
        async for frame in sse_event_generator(graph, initial_state, config):
            yield frame


async def _resume_stream_once(
    graph,
    config: dict,
    resume_value: dict,
    approval_id: str,
    coordinator: ApprovalCoordinator,
):
    from langgraph.types import Command

    thread_id = config["configurable"]["thread_id"]
    lock = await coordinator.lock_for(thread_id)
    async with lock:
        try:
            pending_ids = await _pending_approval_ids(graph, config)
        except CheckpointUnavailableError:
            yield _sse_line(
                "error",
                {
                    "code": "checkpoint_unavailable",
                    "message": "Agent checkpoint storage is temporarily unavailable.",
                },
            )
            yield _sse_line("done", {"status": "failed"})
            return
        if approval_id not in pending_ids:
            yield _sse_line(
                "error",
                {
                    "code": "approval_not_pending",
                    "message": "This approval is no longer pending.",
                },
            )
            yield _sse_line("done", {"status": "failed"})
            return
        async for frame in sse_event_generator(
            graph, Command(resume=resume_value), config
        ):
            yield frame


def _decode_sse(frame: str) -> tuple[str, dict]:
    event = ""
    payload: dict = {}
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            try:
                decoded = json.loads(line[6:])
                payload = decoded if isinstance(decoded, dict) else {}
            except (TypeError, ValueError):
                payload = {}
    return event, payload


async def _execute_resume_run(
    *,
    run: _DurableResumeRun,
    graph,
    config: dict,
    resume_value: dict,
    approval_id: str,
    coordinator: ApprovalCoordinator,
    store,
    workspace_id: str,
    generation: str,
    session_id: str,
    registry: dict,
    key: tuple,
    registry_lock: asyncio.Lock | None = None,
) -> None:
    mutation = None
    verifications: dict[str, dict] = {}
    done_frame = _sse_line("done", {"status": "failed"})
    try:
        async for frame in _resume_stream_once(
            graph, config, resume_value, approval_id, coordinator
        ):
            event, payload = _decode_sse(frame)
            if event == "file_change" and payload.get("proposal_id") == approval_id:
                mutation = payload
            elif event == "verification":
                tool_call_id = payload.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    verifications[tool_call_id] = payload
            if event == "done":
                done_frame = frame
            else:
                await run.publish(frame)

        state, result = _resume_outcome_result(
            bool(resume_value.get("approved")), mutation, verifications
        )
        stored = store.complete_outcome(
            workspace_id,
            generation,
            approval_id,
            session_id,
            state,
            result,
        )
        await run.publish(
            _sse_line("outcome", _outcome_response(workspace_id, session_id, stored))
        )
        await run.publish(done_frame)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("Approval background run failed | code=approval_run_failed")
        try:
            state, result = _resume_outcome_result(
                bool(resume_value.get("approved")), mutation, verifications
            )
            store.complete_outcome(
                workspace_id,
                generation,
                approval_id,
                session_id,
                state,
                result,
            )
        except Exception:
            logger.error("Approval outcome persistence failed | code=outcome_store_failed")
        await run.publish(
            _sse_line(
                "error",
                {
                    "code": "agent_execution_failed",
                    "message": "Agent execution failed.",
                },
            )
        )
        await run.publish(_sse_line("done", {"status": "failed"}))
    finally:
        await run.finish()
        if registry_lock is None:
            if registry.get(key) is run:
                registry.pop(key, None)
        else:
            async with registry_lock:
                if registry.get(key) is run:
                    registry.pop(key, None)


def _resume_outcome_result(
    approved: bool,
    mutation: dict | None,
    verifications: dict[str, dict],
) -> tuple[str, dict]:
    if not approved:
        return "rejected", {"status": "rejected"}
    if mutation is None:
        return "failed", {"status": "failed"}

    mutation_tool_call_id = mutation.get("tool_call_id")
    validation = (
        verifications.get(mutation_tool_call_id)
        if isinstance(mutation_tool_call_id, str)
        else None
    )
    if validation is None:
        # A file_change event proves the write completed. Preserve that fact
        # using the protocol's applied-without-validation terminal state.
        state = "applied_recovered"
        return state, {"status": state, "mutation": mutation}

    state = {
        "passed": "applied_and_tests_passed",
        "failed": "applied_tests_failed",
        "timed_out": "applied_tests_timed_out",
        "termination_failed": "applied_test_termination_failed",
    }.get(validation.get("status"), "applied_test_error")
    return state, {
        "status": state,
        "mutation": mutation,
        "validation": validation,
    }
