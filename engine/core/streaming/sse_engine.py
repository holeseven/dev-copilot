# @author lxy
"""
SSE 引擎 —— 将 LangGraph astream_events 事件流转换为 Server-Sent Events 数据行

事件类型映射：
- on_chat_model_stream → event: message（流式 token）
- on_tool_start / on_tool_end → event: tool（工具调用状态）
- 图执行中遇到 interrupt → event: approval_required（HITL 审批请求）
- 图执行结束 → event: done

零 sleep 设计：纯 async generator，依赖 astream_events 的背压机制。
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, Optional

from loguru import logger


def _sse_line(event: str, data: Any) -> str:
    """格式化单条 SSE 数据行"""
    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_event_generator(
    graph: Any,
    initial_state: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    核心 SSE 生成器：消费 graph.astream_events 并逐条 yield SSE 格式数据。

    Args:
        graph: 编译好的 LangGraph StateGraph 实例（支持 astream_events）
        initial_state: 初始图状态
        config: LangGraph 运行配置（含 recursion_limit / configurable）

    Yields:
        SSE 格式字符串：event: <type>\ndata: <json>\n\n
    """
    run_config = config or {"recursion_limit": 150}

    failed = False
    awaiting_approval = False
    emitted_approval_ids: set[str] = set()
    try:
        async for event in graph.astream_events(
            initial_state, config=run_config, version="v2"
        ):
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})

            # ── 流式 token 输出 ──
            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is not None:
                    content = getattr(chunk, "content", "")
                    if content:
                        yield _sse_line("message", {"token": content})

            # ── 工具调用开始 ──
            elif kind == "on_tool_start":
                yield _sse_line("tool", {
                    "status": "start",
                    "tool_name": name,
                })

            # ── 工具调用结束 ──
            elif kind == "on_tool_end":
                yield _sse_line("tool", {
                    "status": "end",
                    "tool_name": name,
                })

            elif kind == "on_custom_event" and name == "tool_status":
                yield _sse_line("tool", _safe_serialize(data))

            # ── 检测 __interrupt 信息（LangGraph v2 interrupt 事件）──
            elif kind == "on_chain_stream":
                chunk_data = data.get("chunk")
                for approval in _interrupt_approvals(chunk_data):
                    approval_id = approval["approval_id"]
                    if approval_id in emitted_approval_ids:
                        continue
                    emitted_approval_ids.add(approval_id)
                    awaiting_approval = True
                    yield _sse_line("approval_required", approval)

    except Exception as exc:
        failed = True
        logger.opt(exception=True).error("SSE 流异常: {}", str(exc))
        yield _sse_line("error", {
            "code": "agent_execution_failed",
            "message": "Agent 执行失败，请重试",
        })

    # ── 流结束标记 ──
    status = "failed" if failed else (
        "awaiting_approval" if awaiting_approval else "completed"
    )
    yield _sse_line("done", {"status": status})


_APPROVAL_PAYLOAD_FIELDS = {
    "approval_id",
    "operation",
    "workspace_id",
    "repository",
    "virtual_path",
    "diff",
    "before_sha256",
    "after_sha256",
    "patch_sha256",
    "validation_profile",
    "tool_name",
}


def _interrupt_approvals(chunk: Any) -> list[Dict[str, Any]]:
    if not isinstance(chunk, dict):
        return []
    raw_interrupts = chunk.get("__interrupt__", ())
    if not isinstance(raw_interrupts, (list, tuple)):
        raw_interrupts = (raw_interrupts,)
    approvals = []
    for entry in raw_interrupts:
        value = getattr(entry, "value", entry)
        if not isinstance(value, dict) or value.get("type") != "approval_required":
            continue
        approval_id = str(value.get("approval_id", ""))
        if not approval_id:
            continue
        payload = value.get("payload", {})
        safe_payload = {
            key: _safe_serialize(item)
            for key, item in payload.items()
            if key in _APPROVAL_PAYLOAD_FIELDS
        } if isinstance(payload, dict) else {}
        approvals.append(
            {
                "approval_id": approval_id,
                "action": str(value.get("action", "")),
                "message": str(value.get("message", "")),
                "details": "",
                "payload": safe_payload,
            }
        )
    return approvals


def _safe_serialize(obj: Any) -> Any:
    """安全序列化对象，避免不可 JSON 化的内容"""
    if obj is None:
        return None
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        return str(obj)
