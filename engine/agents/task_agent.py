# @author lxy
"""
任务执行 Agent

负责调用 MCP dev_tools 执行具体的开发任务操作，
如文件读写、Git 操作、命令执行等。支持 HITL 门控对敏感操作进行人工审批。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from engine.core.hitl.interrupt_gate import require_approval, SENSITIVE_ACTIONS
from engine.core.mcp.manager import MCPManager
from engine.infra.llm_client import Complexity, get_llm_by_complexity


# ─── Agent 系统提示 ────────────────────────────────────────────────────────────────

TASK_AGENT_SYSTEM_PROMPT = """你是任务执行助手，擅长通过工具完成具体的开发任务。

你的职责：
1. 理解用户的任务需求，将其拆分为可执行的步骤
2. 调用 dev_tools 中的工具（如文件操作、Git 操作、命令执行等）完成任务
3. 对执行结果进行验证，确保任务正确完成
4. 如果某步骤失败，尝试分析原因并重试或给出解决建议

可用工具类别：
- read_file / write_file: 文件读写
- list_directory: 目录浏览
- run_command / shell_exec: 命令执行
- git_commit / git_diff: Git 操作
- search_code: 代码搜索

安全规范：
- 涉及文件修改、Git 提交、命令执行等高风险操作会触发人工审批
- 操作范围严格限制在工作区目录内
- 执行前先确认操作意图，避免误操作
"""


# ─── TaskAgent 类 ──────────────────────────────────────────────────────────────────

class TaskAgent:
    """
    任务执行 Agent

    通过 MCP dev_tools 执行开发任务，
    敏感操作自动触发 HITL 审批门控。
    """

    AGENT_ID = "task_agent"
    DESCRIPTION = "任务执行 Agent，通过 MCP dev_tools 完成文件操作、Git、命令执行等开发任务"

    def __init__(
        self,
        mcp_manager: Optional[MCPManager] = None,
        llm_complexity: Complexity = Complexity.L2,
    ):
        """
        Args:
            mcp_manager: MCP 工具管理器，提供 dev_tools 调用能力
            llm_complexity: LLM 复杂度，任务执行通常需要 L2
        """
        self._mcp = mcp_manager
        self._llm = get_llm_by_complexity(llm_complexity)
        self._max_iterations = 15  # 单次任务最大工具调用轮次
        logger.info("TaskAgent 初始化完成 | mcp={}", "ready" if mcp_manager else "none")

    async def run(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        执行任务。

        Args:
            query: 用户任务描述
            history: 对话历史

        Returns:
            任务执行结果文本
        """
        logger.info("TaskAgent.run | query={}", query[:100])

        messages = [SystemMessage(content=TASK_AGENT_SYSTEM_PROMPT)]

        # 注入历史
        if history:
            for turn in history[-5:]:
                messages.append(HumanMessage(content=turn.get("q", "")))
                messages.append(AIMessage(content=turn.get("a", "")))

        messages.append(HumanMessage(content=query))

        # 获取可用工具并绑定
        tools_for_llm = self._get_available_tools()
        llm_with_tools = self._llm.bind_tools(tools_for_llm) if tools_for_llm else self._llm

        # ReAct 循环
        for iteration in range(self._max_iterations):
            try:
                response: AIMessage = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                # 无工具调用 → 任务完成
                if not getattr(response, "tool_calls", None):
                    logger.info("TaskAgent 完成，共 {} 轮工具调用", iteration)
                    return response.content or "任务已完成"

                # 执行工具调用
                tool_messages = await self._execute_tool_calls(response.tool_calls)
                messages.extend(tool_messages)

            except Exception as e:
                logger.error("TaskAgent 第 {} 轮执行异常: {}", iteration, str(e))
                return f"任务执行过程中出现错误（第 {iteration + 1} 轮）: {str(e)}"

        return "任务执行达到最大轮次限制，已强制停止。请检查任务复杂度或拆分子任务。"

    async def _execute_tool_calls(self, tool_calls: list) -> List[ToolMessage]:
        """
        执行工具调用列表，包含 HITL 门控。

        Args:
            tool_calls: LLM 生成的工具调用

        Returns:
            ToolMessage 结果列表
        """
        results = []

        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {})
            tool_id = call.get("id", "")

            # HITL 门控检查
            if tool_name in SENSITIVE_ACTIONS:
                try:
                    require_approval(
                        action=tool_name,
                        payload={"tool_name": tool_name, "arguments": tool_args},
                    )
                except Exception as e:
                    results.append(ToolMessage(
                        content=f"[BLOCKED] 操作 '{tool_name}' 需要人工审批: {str(e)}",
                        tool_call_id=tool_id,
                    ))
                    continue

            # 执行工具
            if self._mcp is None:
                results.append(ToolMessage(
                    content=f"[ERROR] MCP 管理器未就绪，无法执行 '{tool_name}'",
                    tool_call_id=tool_id,
                ))
                continue

            try:
                result = await self._mcp.call_tool(tool_name, tool_args)
                content = str(result) if result is not None else "执行成功"
                results.append(ToolMessage(content=content, tool_call_id=tool_id))
            except Exception as e:
                results.append(ToolMessage(
                    content=f"[ERROR] 工具 '{tool_name}' 执行失败: {str(e)}",
                    tool_call_id=tool_id,
                ))

        return results

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """获取 MCP 中所有可用的 dev_tools schema"""
        if self._mcp is None:
            return self._default_dev_tools()

        schemas = self._mcp.get_tool_schemas()
        if not schemas:
            return self._default_dev_tools()

        # 转为 OpenAI 工具格式
        tools = []
        for schema in schemas:
            tools.append({
                "type": "function",
                "function": {
                    "name": schema.get("name", ""),
                    "description": schema.get("description", ""),
                    "parameters": schema.get("parameters", schema.get("inputSchema", {"type": "object", "properties": {}})),
                },
            })
        return tools

    @staticmethod
    def _default_dev_tools() -> List[Dict[str, Any]]:
        """当 MCP 不可用时的默认工具定义（用于 LLM 理解能力边界）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取指定路径的文件内容",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件绝对路径"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入内容到指定文件（高风险操作，需审批）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件绝对路径"},
                            "content": {"type": "string", "description": "文件内容"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "在工作区内执行 Shell 命令（高风险操作，需审批）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "要执行的命令"},
                            "cwd": {"type": "string", "description": "工作目录，默认工作区根目录"},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]
