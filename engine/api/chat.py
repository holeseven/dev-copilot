# @author lxy
"""
Chat API 路由

- POST /chat：接收 {message, session_id}，返回 SSE StreamingResponse
- POST /chat/resume：HITL 恢复接口，接收 {thread_id, approved, reason}
"""
from __future__ import annotations

import hashlib
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


# ─── 请求模型 ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """聊天请求体"""
    message: str = Field(..., min_length=1, max_length=20_000, description="用户消息")
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    workspace_id: str = Field(..., min_length=1, max_length=128, description="Workspace ID")


class PendingRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=128)


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
    return {
        "workspace_id": request.workspace_id,
        "session_id": request.session_id,
        "status": "awaiting_approval" if approvals else "idle",
        "approvals": approvals,
    }


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

        pending_ids = await _pending_approval_ids(graph, config)
        if request.approval_id not in pending_ids:
            raise HTTPException(
                status_code=409,
                detail="approval is not pending for this workspace session",
            )

        # 使用 Command(resume=...) 恢复执行
        return StreamingResponse(
            _resume_stream_once(
                graph,
                config,
                resume_value,
                request.approval_id,
                _approval_coordinator(http_request),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except CheckpointUnavailableError:
        raise _checkpoint_unavailable_http_error() from None
    except ImportError:
        raise HTTPException(status_code=501, detail="LangGraph Command 模块不可用")
    except Exception as exc:
        logger.opt(exception=True).error("HITL 恢复失败: {}", str(exc))
        raise HTTPException(status_code=500, detail="unable to resume agent run")


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
        logger.opt(exception=True).warning("Unable to inspect pending approvals")
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
