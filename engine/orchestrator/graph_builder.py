# @author lxy
"""
LangGraph 编排核心 —— StateGraph 构建器

构建工作流：preprocess → agent → check → route → tools 循环
集成：
- Dispatcher（入口路由，决定目标 Agent）
- Memory（注入历史消息到 state）
- MCP（ToolNode 工具执行，容错包装错误为 ToolMessage）

recursion_limit=150 避免复杂任务因递归深度不足而中断。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence, TypedDict, Annotated

from langchain_core.messages import BaseMessage, ToolMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from loguru import logger

from engine.core.dispatcher.dispatcher import get_agent_dispatcher
from engine.core.memory.memory_retriever import MemoryRetriever
from engine.core.mcp.manager import MCPManager
from engine.orchestrator.nodes import (
    preprocess_node,
    agent_node,
    check_early_termination,
    route_node,
    tool_node,
)


# ─── Graph 状态定义 ─────────────────────────────────────────────────────────────

class GraphState(TypedDict):
    """LangGraph 全局状态"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    session_id: str
    user_query: str
    target_agent_id: str
    complexity_level: str
    iteration_count: int
    max_iterations: int
    should_terminate: bool
    tool_results: List[Dict[str, Any]]


# ─── 路由判定函数 ─────────────────────────────────────────────────────────────────

def _should_continue(state: GraphState) -> Literal["tools", "end"]:
    """
    route_node 之后的条件路由：
    - 有工具调用请求 → 进入 tools 节点
    - 无工具调用或应终止 → 结束
    """
    if state.get("should_terminate", False):
        return "end"

    messages = state.get("messages", [])
    if not messages:
        return "end"

    last_msg = messages[-1]
    # AIMessage 含 tool_calls → 进入工具执行
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "end"


def _after_check(state: GraphState) -> Literal["route", "end"]:
    """
    check 节点之后的条件路由：
    - 超过轮次上限或标记终止 → 结束
    - 否则 → 进入 route 正常决策
    """
    if state.get("should_terminate", False):
        return "end"
    if state.get("iteration_count", 0) >= state.get("max_iterations", 35):
        return "end"
    return "route"


# ─── Graph 构建器 ─────────────────────────────────────────────────────────────────

def build_agent_graph(
    mcp_manager: Optional[MCPManager] = None,
    memory_retriever: Optional[MemoryRetriever] = None,
    recursion_limit: int = 150,
) -> StateGraph:
    """
    构建完整的 Agent 编排 StateGraph。

    流程：preprocess → agent → check → route →(tools → agent → check → route)* → END

    Args:
        mcp_manager: MCP 工具管理器实例（为 None 则 ToolNode 无可用工具）
        memory_retriever: 记忆检索器实例（为 None 则不注入历史）
        recursion_limit: 递归上限，默认 150

    Returns:
        编译好的 StateGraph 可执行实例
    """
    # 将依赖注入到 config 中，节点通过 config 获取
    config_data = {
        "mcp_manager": mcp_manager,
        "memory_retriever": memory_retriever,
        "dispatcher": get_agent_dispatcher(),
    }

    workflow = StateGraph(GraphState)

    # ── 注册节点 ──
    workflow.add_node("preprocess", preprocess_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("check", check_early_termination)
    workflow.add_node("route", route_node)
    workflow.add_node("tools", tool_node)

    # ── 定义边 ──
    workflow.set_entry_point("preprocess")
    workflow.add_edge("preprocess", "agent")
    workflow.add_edge("agent", "check")
    workflow.add_conditional_edges("check", _after_check, {"route": "route", "end": END})
    workflow.add_conditional_edges("route", _should_continue, {"tools": "tools", "end": END})
    workflow.add_edge("tools", "agent")  # 工具执行完毕回到 agent 继续推理

    # ── 编译 ──
    compiled = workflow.compile(
        checkpointer=None,  # 由上层按需注入 checkpointer
    )

    logger.info(
        "Agent Graph 构建完成 | recursion_limit={} | nodes={}",
        recursion_limit,
        ["preprocess", "agent", "check", "route", "tools"],
    )

    return compiled


# ─── 便捷运行入口 ─────────────────────────────────────────────────────────────────

async def run_graph(
    session_id: str,
    user_query: str,
    mcp_manager: Optional[MCPManager] = None,
    memory_retriever: Optional[MemoryRetriever] = None,
) -> Dict[str, Any]:
    """
    一键运行 Agent Graph 的便捷方法。

    Args:
        session_id: 会话 ID
        user_query: 用户输入
        mcp_manager: MCP 管理器
        memory_retriever: 记忆检索器

    Returns:
        最终 state 字典
    """
    graph = build_agent_graph(
        mcp_manager=mcp_manager,
        memory_retriever=memory_retriever,
    )

    initial_state: GraphState = {
        "messages": [],
        "session_id": session_id,
        "user_query": user_query,
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
            "mcp_manager": mcp_manager,
            "memory_retriever": memory_retriever,
            "dispatcher": get_agent_dispatcher(),
        },
    }

    result = await graph.ainvoke(initial_state, config=config)
    return result
