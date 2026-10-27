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

import hashlib
import json
import re
from typing import Any, Dict

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from loguru import logger

from engine.core.dispatcher.dispatcher import get_agent_dispatcher
from engine.core.hitl.interrupt_gate import (
    ApprovalRejectedError,
    is_sensitive,
    require_approval,
)
from engine.core.memory.memory_retriever import MemoryRetriever
from engine.core.mcp.approval import ToolApprovalRequest
from engine.core.mcp.manager import MCPManager
from engine.infra.llm_client import Complexity, get_llm_by_complexity


# ─── 常量 ─────────────────────────────────────────────────────────────────────────

MAX_AGENT_ITERATIONS = 35  # agent_node ReAct 推理最大轮次
SYSTEM_PROMPT_TEMPLATE = """你是一个智能助手，负责 {agent_role}。
请基于用户的问题和可用工具进行推理和回答。
如果需要使用工具，请通过 function calling 调用。
如果已经获得足够信息可以回答用户，请直接给出最终答案。"""


# ─── preprocess_node ──────────────────────────────────────────────────────────────

async def preprocess_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Dict[str, Any]:
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
    configurable = config.get("configurable", {})
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

async def agent_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Dict[str, Any]:
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
    configurable = config.get("configurable", {})
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
        if getattr(response, "tool_calls", None):
            normalized_calls = _normalize_tool_calls(
                response.tool_calls,
                session_id=f"{state.get('session_id', '')}:{iteration_count}",
            )
            response = response.model_copy(update={"tool_calls": normalized_calls})
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

async def check_early_termination(
    state: Dict[str, Any], config: RunnableConfig
) -> Dict[str, Any]:
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

async def route_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Dict[str, Any]:
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

async def tool_node(
    state: Dict[str, Any], config: RunnableConfig
) -> Dict[str, Any]:
    """
    工具执行节点（ToolNode 容错实现）：
    - 从最新 AIMessage.tool_calls 提取工具调用
    - 对敏感工具触发 HITL interrupt 审核门控
    - 通过 MCPManager.call_tool() 执行
    - 执行失败时将错误包装为 ToolMessage 回传 LLM（而非抛异常）
    """
    logger.info("─── tool_node START ───")

    messages = list(state.get("messages", []))
    configurable = config.get("configurable", {})
    mcp_manager: MCPManager | None = configurable.get("mcp_manager")

    if not messages:
        return {"messages": messages}

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage) or not getattr(last_msg, "tool_calls", None):
        return {"messages": messages}

    tool_results = []
    normalized_calls = _validated_tool_calls(last_msg.tool_calls)
    rejected_ids: set[str] = set()
    approval_requests: dict[str, ToolApprovalRequest] = {}
    preparation_errors: set[str] = set()

    # Prepare every sensitive operation without side effects. Sensitive providers
    # must issue the descriptor; caller-supplied paths/diffs are never trusted.
    for tool_call in normalized_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_id = tool_call["id"]
        if not is_sensitive(tool_name):
            continue
        try:
            descriptor = (
                await mcp_manager.prepare_approval(tool_name, tool_args)
                if mcp_manager is not None
                else None
            )
            if descriptor is None:
                preparation_errors.add(tool_id)
                continue
            descriptor_payload = dict(descriptor.payload)
            expected_workspace = configurable.get("workspace_id")
            if (
                descriptor.action != tool_name
                or not is_sensitive(descriptor.action)
                or descriptor_payload.get("tool_name") != tool_name
                or (
                    expected_workspace is not None
                    and descriptor_payload.get("workspace_id") != expected_workspace
                )
            ):
                preparation_errors.add(tool_id)
                continue
            approval_requests[tool_id] = descriptor
        except Exception:
            preparation_errors.add(tool_id)
            logger.opt(exception=True).warning(
                "敏感工具准备失败 | tool={} | call_id={}", tool_name, tool_id
            )

    # Complete every approval before starting any tool. A later interrupt replays
    # this node, so executing earlier calls here would duplicate their side effects.
    for tool_call in normalized_calls:
        tool_name = tool_call["name"]
        tool_id = tool_call["id"]
        descriptor = approval_requests.get(tool_id)
        if descriptor is None:
            continue
        try:
            require_approval(
                action=descriptor.action,
                payload=dict(descriptor.payload),
                approval_id=descriptor.approval_id,
            )
        except ApprovalRejectedError:
            rejected_ids.add(tool_id)

    for tool_call in normalized_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_id = tool_call["id"]

        logger.info("执行工具 | tool={} | call_id={}", tool_name, tool_id)

        if tool_id in preparation_errors:
            messages.append(
                ToolMessage(
                    content="[ERROR] Sensitive tool preparation failed.",
                    tool_call_id=tool_id,
                )
            )
            tool_results.append(
                {
                    "tool": tool_name,
                    "tool_call_id": tool_id,
                    "status": "error",
                    "code": "approval_preparation_failed",
                }
            )
            continue

        if tool_id in rejected_ids:
            messages.append(
                ToolMessage(
                    content="[REJECTED] Approval was declined.",
                    tool_call_id=tool_id,
                )
            )
            tool_results.append(
                {"tool": tool_name, "tool_call_id": tool_id, "status": "rejected"}
            )
            continue

        # ── 执行工具 ──
        if mcp_manager is None:
            error_result = ToolMessage(
                content="[ERROR] Tool execution environment is unavailable.",
                tool_call_id=tool_id,
            )
            messages.append(error_result)
            tool_results.append(
                {"tool": tool_name, "tool_call_id": tool_id, "status": "error", "code": "manager_unavailable"}
            )
            continue

        try:
            await adispatch_custom_event(
                "tool_status",
                {"status": "start", "tool_name": tool_name, "tool_call_id": tool_id},
                config=config,
            )
            descriptor = approval_requests.get(tool_id)
            if descriptor is None:
                result = await mcp_manager.call_tool(tool_name, tool_args)
            else:
                result = await mcp_manager.call_tool(
                    tool_name,
                    tool_args,
                    approval_id=descriptor.approval_id,
                )
            result_content = _tool_result_content(result)
            tool_msg = ToolMessage(content=result_content, tool_call_id=tool_id)
            messages.append(tool_msg)
            await _dispatch_controlled_change_events(result, tool_id, config)
            await _dispatch_citation_events(result, tool_id, config)
            if isinstance(result, str) and result.startswith("[ERROR]"):
                await adispatch_custom_event(
                    "tool_status",
                    {"status": "error", "tool_name": tool_name, "tool_call_id": tool_id},
                    config=config,
                )
                tool_results.append(
                    {"tool": tool_name, "tool_call_id": tool_id, "status": "error", "code": "tool_reported_error"}
                )
            else:
                await adispatch_custom_event(
                    "tool_status",
                    {"status": "end", "tool_name": tool_name, "tool_call_id": tool_id},
                    config=config,
                )
                tool_results.append({"tool": tool_name, "tool_call_id": tool_id, "status": "ok"})
                logger.info("工具 {} 执行成功", tool_name)

        except Exception:
            await adispatch_custom_event(
                "tool_status",
                {"status": "error", "tool_name": tool_name, "tool_call_id": tool_id},
                config=config,
            )
            # 容错：将错误包装为 ToolMessage 回传 LLM
            error_content = "[ERROR] Tool execution failed."
            error_msg = ToolMessage(content=error_content, tool_call_id=tool_id)
            messages.append(error_msg)
            tool_results.append(
                {"tool": tool_name, "tool_call_id": tool_id, "status": "error", "code": "execution_failed"}
            )
            logger.opt(exception=True).warning(
                "工具执行失败 | tool={} | call_id={}", tool_name, tool_id
            )

    logger.info("─── tool_node END | {} tools executed ───", len(tool_results))
    return {
        "messages": messages,
        "tool_results": tool_results,
    }


def _tool_result_content(result: Any) -> str:
    if result is None:
        return "执行成功（无返回值）"
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    return str(result)


async def _dispatch_controlled_change_events(
    result: Any, tool_call_id: str, config: RunnableConfig
) -> None:
    if not isinstance(result, dict):
        return
    mutation = result.get("mutation")
    if isinstance(mutation, dict):
        await adispatch_custom_event(
            "file_change",
            {"tool_call_id": tool_call_id, **mutation},
            config=config,
        )
    validation = result.get("validation")
    if isinstance(validation, dict):
        await adispatch_custom_event(
            "verification",
            {"tool_call_id": tool_call_id, **validation},
            config=config,
        )


_CITATION_FIELDS = {
    "citation_id", "rank", "repository", "virtual_path", "start_line",
    "end_line", "excerpt", "content_sha256",
}


async def _dispatch_citation_events(
    result: Any, tool_call_id: str, config: RunnableConfig
) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("citations"), list):
        return
    for citation in result["citations"]:
        if not isinstance(citation, dict):
            continue
        safe = {key: citation[key] for key in _CITATION_FIELDS if key in citation}
        if safe.get("citation_id") and safe.get("virtual_path"):
            await adispatch_custom_event(
                "citation",
                {
                    "tool_call_id": tool_call_id,
                    "workspace_id": str(result.get("workspace_id", "")),
                    "index_generation": str(result.get("index_generation", "")),
                    "citation": safe,
                },
                config=config,
            )


def _normalize_tool_calls(tool_calls: list, *, session_id: str) -> list[dict[str, Any]]:
    """Return stable, unique call ids without trusting malformed model output."""

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, tool_call in enumerate(tool_calls):
        tool_name = str(tool_call.get("name", ""))
        raw_args = tool_call.get("args", {})
        tool_args = raw_args if isinstance(raw_args, dict) else {}
        candidate = tool_call.get("id")
        tool_id = candidate.strip() if isinstance(candidate, str) else ""
        if not _VALID_TOOL_CALL_ID.fullmatch(tool_id) or tool_id in seen:
            canonical = json.dumps(
                {
                    "session_id": session_id,
                    "index": index,
                    "name": tool_name,
                    "arguments": tool_args,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            tool_id = f"tool-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"
            while tool_id in seen:
                tool_id = f"{tool_id}-{index}"
        seen.add(tool_id)
        normalized.append({"name": tool_name, "args": tool_args, "id": tool_id})
    return normalized


_VALID_TOOL_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _validated_tool_calls(tool_calls: list) -> list[dict[str, Any]]:
    """Reject unnormalized persisted calls before any approval or side effect."""

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool_call in tool_calls:
        tool_name = str(tool_call.get("name", ""))
        tool_args = tool_call.get("args", {})
        tool_id = tool_call.get("id")
        if not isinstance(tool_args, dict):
            raise ValueError("tool arguments must be an object")
        if not isinstance(tool_id, str) or not _VALID_TOOL_CALL_ID.fullmatch(tool_id):
            raise ValueError("tool call id is not normalized")
        if tool_id in seen:
            raise ValueError("tool call ids must be unique")
        seen.add(tool_id)
        validated.append({"name": tool_name, "args": tool_args, "id": tool_id})
    return validated


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
