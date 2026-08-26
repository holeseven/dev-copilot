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
                    "input": _safe_serialize(data.get("input")),
                })

            # ── 工具调用结束 ──
            elif kind == "on_tool_end":
                yield _sse_line("tool", {
                    "status": "end",
                    "tool_name": name,
                    "output": _safe_serialize(data.get("output")),
                })

            # ── HITL 中断（interrupt）──
            elif kind == "on_chain_end" and "interrupt" in str(data).lower():
                # 检测 interrupt payload
                output = data.get("output", {})
                if isinstance(output, dict) and output.get("type") == "approval_required":
                    yield _sse_line("approval_required", {
                        "action": output.get("action", ""),
                        "message": output.get("message", ""),
                        "details": output.get("details", ""),
                        "payload": output.get("payload", {}),
                    })

            # ── 检测 __interrupt 信息（LangGraph v2 interrupt 事件）──
            elif kind == "on_chain_stream":
                chunk_data = data.get("chunk")
                if isinstance(chunk_data, dict) and chunk_data.get("type") == "approval_required":
                    yield _sse_line("approval_required", {
                        "action": chunk_data.get("action", ""),
                        "message": chunk_data.get("message", ""),
                        "details": chunk_data.get("details", ""),
                        "payload": chunk_data.get("payload", {}),
                    })

    except Exception as exc:
        logger.opt(exception=True).error("SSE 流异常: {}", str(exc))
        yield _sse_line("error", {"message": str(exc)})

    # ── 流结束标记 ──
    yield _sse_line("done", {"status": "completed"})


def _safe_serialize(obj: Any) -> Any:
    """安全序列化对象，避免不可 JSON 化的内容"""
    if obj is None:
        return None
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        return str(obj)
