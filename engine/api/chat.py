# @author lxy
"""
Chat API 路由

- POST /chat：接收 {message, session_id}，返回 SSE StreamingResponse
- POST /chat/resume：HITL 恢复接口，接收 {thread_id, approved, reason}
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from engine.core.streaming.sse_engine import sse_event_generator
from engine.orchestrator.graph_builder import build_agent_graph

router = APIRouter()


# ─── 请求模型 ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """聊天请求体"""
    message: str = Field(..., min_length=1, description="用户消息")
    session_id: str = Field(default="default", description="会话 ID")


class ResumeRequest(BaseModel):
    """HITL 恢复请求体"""
    thread_id: str = Field(..., description="中断时的线程/会话 ID")
    approved: bool = Field(..., description="是否批准")
    reason: Optional[str] = Field(default="", description="拒绝原因（可选）")


# ─── POST /chat ───────────────────────────────────────────────────────────────

@router.post("")
async def chat(request: ChatRequest):
    """
    聊天接口：接收用户消息，返回 SSE 流式响应。

    SSE 事件类型：
    - event: message — 流式 token
    - event: tool — 工具调用状态
    - event: approval_required — HITL 审批请求
    - event: done — 流结束
    """
    logger.info("收到聊天请求 | session={} | message={}", request.session_id, request.message[:80])

    # 构建图实例
    graph = build_agent_graph()

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
            "session_id": request.session_id,
        },
    }

    return StreamingResponse(
        sse_event_generator(graph, initial_state, config),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── POST /chat/resume ────────────────────────────────────────────────────────

@router.post("/resume")
async def resume(request: ResumeRequest):
    """
    HITL 恢复接口：外部审批后恢复图执行。

    通过 LangGraph 的 Command(resume=...) 机制继续被 interrupt 暂停的图执行。
    """
    logger.info(
        "收到 HITL 恢复请求 | thread_id={} | approved={} | reason={}",
        request.thread_id,
        request.approved,
        request.reason,
    )

    try:
        from langgraph.types import Command

        # 构建恢复值
        resume_value = {
            "approved": request.approved,
            "reason": request.reason or "",
            "reviewer": "user",
        }

        # 构建图（实际生产中应从 checkpointer 恢复对应 thread 的图实例）
        graph = build_agent_graph()

        config = {
            "recursion_limit": 150,
            "configurable": {
                "thread_id": request.thread_id,
            },
        }

        # 使用 Command(resume=...) 恢复执行
        # 注：完整实现需要持久化 checkpointer，此处为 SSE 流式恢复
        resume_generator = sse_event_generator(
            graph,
            None,  # resume 模式不传 initial_state
            {**config, "__resume_value": resume_value},
        )

        return StreamingResponse(
            _resume_stream(graph, config, resume_value),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except ImportError:
        raise HTTPException(status_code=501, detail="LangGraph Command 模块不可用")
    except Exception as exc:
        logger.opt(exception=True).error("HITL 恢复失败: {}", str(exc))
        raise HTTPException(status_code=500, detail=f"恢复失败: {str(exc)}")


async def _resume_stream(graph, config: dict, resume_value: dict):
    """HITL 恢复后的 SSE 流"""
    import json

    from langgraph.types import Command

    try:
        command = Command(resume=resume_value)
        async for event in graph.astream_events(
            command, config=config, version="v2"
        ):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is not None:
                    content = getattr(chunk, "content", "")
                    if content:
                        yield f"event: message\ndata: {json.dumps({'token': content}, ensure_ascii=False)}\n\n"

            elif kind == "on_tool_end":
                yield f"event: tool\ndata: {json.dumps({'status': 'end', 'tool_name': event.get('name', '')}, ensure_ascii=False)}\n\n"

    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    yield f"event: done\ndata: {json.dumps({'status': 'completed'}, ensure_ascii=False)}\n\n"
