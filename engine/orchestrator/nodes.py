# @author lxy
"""
Graph 节点实现

定义 LangGraph 中每个节点的具体逻辑：
- preprocess_node: 预处理（注入记忆历史、路由分发）
- agent_node: ReAct 推理主循环（35轮上限）
- check_early_termination: 终止条件检测
- route_node: 判定是否需要工具调用
- tool_node: MCP 工具执行（容错包装错误为 ToolMessage）
"""
from __future__ import annotations

import traceback
from typing import Any, Dict

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from loguru import logger

from engine.core.dispatcher.dispatcher import get_agent_dispatcher
from engine.core.hitl.interrupt_gate import require_approval
from engine.core.memory.memory_retriever import MemoryRetriever
from engine.core.mcp.manager import MCPManager
from engine.infra.llm_client import Complexity, get_llm_by_complexity


# ─── 常量 ─────────────────────────────────────────────────────────────────────────

MAX_AGENT_ITERATIONS = 35  # agent_node ReAct 推理最大轮次
SENSITIVE_TOOLS = {"write_file", "git_commit", "run_command", "delete_file", "shell_exec"}

SYSTEM_PROMPT_TEMPLATE = """你是一个智能助手，负责 {agent_role}。
请基于用户的问题和可用工具进行推理和回答。
如果需要使用工具，请通过 function calling 调用。
如果已经获得足够信息可以回答用户，请直接给出最终答案。"""


# ─── preprocess_node ──────────────────────────────────────────────────────────────

async def preprocess_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    预处理节点：
    1. 从 MemoryRetriever 获取历史对话并注入 messages
    2. 调用 Dispatcher 做意图路由，写入 target_agent_id / complexity_level
    3. 将用户 query 构造为 HumanMessage 追加到 messages
    """
    logger.info("─── preprocess_node START ───")

    session_id = state.get("session_id", "")
    user_query = state.get("user_query", "")
    messages = list(state.get("messages", []))

    # ── 1. 注入记忆历史 ──
    config = state.get("__config__", {})
    configurable = config.get("configurable", {}) if config else {}
    memory_retriever: MemoryRetriever | None = configurable.get("memory_retriever")

    if memory_retriever and session_id:
        try:
            history = await memory_retriever.get_recent(session_id, last_n=10)
            for turn in history:
                messages.append(HumanMessage(content=turn.get("q", "")))
                messages.append(AIMessage(content=turn.get("a", "")))
            logger.info("注入 {} 轮历史记忆", len(history))
        except Exception as e:
            logger.warning("记忆检索失败，跳过: {}", str(e))

    # ── 2. 意图路由 ──
    dispatcher = configurable.get("dispatcher") or get_agent_dispatcher()
    try:
        decision = await dispatcher.route(user_query)
        target_agent_id = decision.target_agent_id
        complexity_level = decision.complexity_level or "L2"
        logger.info("路由决策: agent={} complexity={}", target_agent_id, complexity_level)
    except Exception as e:
        logger.warning("路由失败，降级到 qa_agent: {}", str(e))
        target_agent_id = "qa_agent"
        complexity_level = "L2"

    # ── 3. 构造系统提示 + 用户消息 ──
    agent_roles = {
        "knowledge_agent": "知识检索与文档查询",
        "task_agent": "任务执行与工具调用",
        "qa_agent": "通用问答与对话",
    }
    role_desc = agent_roles.get(target_agent_id, "通用问答与对话")
    system_msg = SystemMessage(content=SYSTEM_PROMPT_TEMPLATE.format(agent_role=role_desc))
    messages.insert(0, system_msg)
    messages.append(HumanMessage(content=user_query))

    logger.info("─── preprocess_node END ───")

    return {
        "messages": messages,
        "target_agent_id": target_agent_id,
        "complexity_level": complexity_level,
        "iteration_count": 0,
        "should_terminate": False,
    }


# ─── agent_node ───────────────────────────────────────────────────────────────────

async def agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    ReAct 推理节点：
    - 使用绑定了工具 schema 的 LLM 进行推理
    - 单次调用，由外层 Graph 循环控制轮次
    - 35 轮上限由 check_early_termination 保障
    """
    logger.info("─── agent_node START (iteration={}) ───", state.get("iteration_count", 0))

    messages = list(state.get("messages", []))
    complexity = state.get("complexity_level", "L2")
    iteration_count = state.get("iteration_count", 0)

    # 选择 LLM
    llm_complexity = Complexity.L2 if complexity == "L2" else Complexity.L1
    llm = get_llm_by_complexity(llm_complexity)

    # 绑定工具 schema（如有 MCP 可用工具）
    config = state.get("__config__", {})
    configurable = config.get("configurable", {}) if config else {}
    mcp_manager: MCPManager | None = configurable.get("mcp_manager")

    if mcp_manager:
        tool_schemas = mcp_manager.get_tool_schemas()
        if tool_schemas:
            # 构建 OpenAI function calling 格式
            tools_for_llm = _convert_schemas_to_openai_tools(tool_schemas)
            llm = llm.bind_tools(tools_for_llm)

    # 调用 LLM
    try:
        response: AIMessage = await llm.ainvoke(messages)
        messages.append(response)
        logger.info(
            "agent_node LLM 响应 | has_tool_calls={} | content_len={}",
            bool(getattr(response, "tool_calls", None)),
            len(response.content) if response.content else 0,
        )
    except Exception as e:
        logger.error("agent_node LLM 调用失败: {}", str(e))
        error_msg = AIMessage(content=f"抱歉，推理过程出现错误: {str(e)}")
        messages.append(error_msg)
        return {
            "messages": messages,
            "iteration_count": iteration_count + 1,
            "should_terminate": True,
        }

    logger.info("─── agent_node END ───")
    return {
        "messages": messages,
        "iteration_count": iteration_count + 1,
    }


# ─── check_early_termination ─────────────────────────────────────────────────────

async def check_early_termination(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    终止条件检测：
    1. 迭代次数 >= max_iterations (默认 35) → 强制终止
    2. 最新 AIMessage 无 tool_calls 且有 content → 自然终止（已有最终答案）
    """
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", MAX_AGENT_ITERATIONS)
    messages = state.get("messages", [])

    # 检查轮次上限
    if iteration_count >= max_iterations:
        logger.warning("达到最大迭代轮次 {}，强制终止", max_iterations)
        return {"should_terminate": True}

    # 检查是否已有最终答案（无 tool_calls 的 AIMessage）
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage):
            has_tool_calls = bool(getattr(last_msg, "tool_calls", None))
            has_content = bool(last_msg.content and last_msg.content.strip())
            if not has_tool_calls and has_content:
                logger.info("Agent 已给出最终答案，自然终止")
                return {"should_terminate": True}

    return {"should_terminate": False}


# ─── route_node ───────────────────────────────────────────────────────────────────

async def route_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    路由决策节点：
    - 检查最新 AIMessage 是否包含 tool_calls
    - 如果有 → 保持 should_terminate=False，让条件边把流程引入 tools
    - 如果无 → 标记终止
    此节点本身不改变 messages，仅做状态标记。
    """
    messages = state.get("messages", [])

    if not messages:
        return {"should_terminate": True}

    last_msg = messages[-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        logger.info("route_node: 检测到 {} 个工具调用", len(last_msg.tool_calls))
        return {"should_terminate": False}

    return {"should_terminate": True}


# ─── tool_node ────────────────────────────────────────────────────────────────────

async def tool_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    工具执行节点（ToolNode 容错实现）：
    - 从最新 AIMessage.tool_calls 提取工具调用
    - 对敏感工具触发 HITL interrupt 审核门控
    - 通过 MCPManager.call_tool() 执行
    - 执行失败时将错误包装为 ToolMessage 回传 LLM（而非抛异常）
    """
    logger.info("─── tool_node START ───")

    messages = list(state.get("messages", []))
    config = state.get("__config__", {})
    configurable = config.get("configurable", {}) if config else {}
    mcp_manager: MCPManager | None = configurable.get("mcp_manager")

    if not messages:
        return {"messages": messages}

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage) or not getattr(last_msg, "tool_calls", None):
        return {"messages": messages}

    tool_results = []

    for tool_call in last_msg.tool_calls:
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})
        tool_id = tool_call.get("id", "")

        logger.info("执行工具: {} | args={}", tool_name, tool_args)

        # ── HITL 门控：敏感工具需人工审批 ──
        if tool_name in SENSITIVE_TOOLS:
            try:
                require_approval(
                    action=tool_name,
                    payload={"tool_name": tool_name, "arguments": tool_args},
                )
            except Exception as e:
                # interrupt 会暂停执行流，这里处理被拒绝的情况
                reject_msg = ToolMessage(
                    content=f"[REJECTED] 操作 '{tool_name}' 被拒绝: {str(e)}",
                    tool_call_id=tool_id,
                )
                messages.append(reject_msg)
                tool_results.append({"tool": tool_name, "status": "rejected"})
                continue

        # ── 执行工具 ──
        if mcp_manager is None:
            error_result = ToolMessage(
                content=f"[ERROR] 工具执行环境未就绪，无法执行 '{tool_name}'",
                tool_call_id=tool_id,
            )
            messages.append(error_result)
            tool_results.append({"tool": tool_name, "status": "error", "msg": "no mcp_manager"})
            continue

        try:
            result = await mcp_manager.call_tool(tool_name, tool_args)
            # 正常结果
            result_content = str(result) if result is not None else "执行成功（无返回值）"
            tool_msg = ToolMessage(content=result_content, tool_call_id=tool_id)
            messages.append(tool_msg)
            tool_results.append({"tool": tool_name, "status": "ok"})
            logger.info("工具 {} 执行成功", tool_name)

        except Exception as e:
            # 容错：将错误包装为 ToolMessage 回传 LLM
            error_content = (
                f"[ERROR] 工具 '{tool_name}' 执行失败:\n"
                f"{type(e).__name__}: {str(e)}\n"
                f"{traceback.format_exc()[-500:]}"
            )
            error_msg = ToolMessage(content=error_content, tool_call_id=tool_id)
            messages.append(error_msg)
            tool_results.append({"tool": tool_name, "status": "error", "msg": str(e)})
            logger.warning("工具 {} 执行失败: {}", tool_name, str(e))

    logger.info("─── tool_node END | {} tools executed ───", len(tool_results))
    return {
        "messages": messages,
        "tool_results": tool_results,
    }


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────────

def _convert_schemas_to_openai_tools(schemas: list) -> list:
    """
    将 MCP 工具 schema 列表转换为 OpenAI function calling 格式。

    Args:
        schemas: MCP registry 返回的 schema 列表
    Returns:
        OpenAI tools 格式列表
    """
    tools = []
    for schema in schemas:
        name = schema.get("name", "")
        description = schema.get("description", f"Tool: {name}")
        parameters = schema.get("parameters", schema.get("inputSchema", {"type": "object", "properties": {}}))

        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
    return tools
