# @author lxy
"""
LLM-as-Supervisor 编排器

核心职责：
1. 接收 L2 复杂任务（由 Dispatcher 的 coordinate 路径转入）
2. 调用 Planner 将复杂任务拆解为带依赖关系的子任务列表
3. 通过 SubAgentTool 逐个委派子 Agent 执行
4. 整合所有子 Agent 结果，返回最终输出

设计原则：
- Supervisor 本身是一个 LLM Agent，拥有 subagent_tool 作为可调用工具
- 使用 L2 模型以获取更强的推理与编排能力
- 支持父子会话追踪（parent_step_id）
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from engine.infra.llm_client import Complexity, get_llm_by_complexity

from .planner import TaskPlan, write_todos
from .subagent_tool import build_subagent_tools

# ─── Supervisor System Prompt ─────────────────────────────────────────────────

SUPERVISOR_SYSTEM_PROMPT = """你是一个 Supervisor Agent，负责编排和协调多个子 Agent 完成复杂任务。

你的工作流程：
1. 分析用户的复杂任务，理解其目标和约束
2. 查看已拆解的子任务计划（todos），按依赖顺序逐个执行
3. 对每个子任务，调用对应的子 Agent 工具完成
4. 收集所有子 Agent 的返回结果，整合为最终输出

当前可用的子 Agent 工具：
{available_tools}

子任务计划：
{task_plan}

注意事项：
- 严格按照依赖顺序执行子任务
- 如果某个子任务失败，评估是否可以跳过或需要中止整个流程
- 每个子任务执行完毕后记录结果，供后续任务参考
"""


class SupervisorOrchestrator:
    """
    LLM-as-Supervisor 编排器

    接收 L2 复杂任务 → Planner 拆解 → 逐个委派子 Agent → 整合结果
    """

    def __init__(self) -> None:
        # 使用 L2 模型作为 Supervisor 的大脑
        self._llm = get_llm_by_complexity(
            complexity=Complexity.L2,
            disable_streaming=False,
        )
        logger.info("SupervisorOrchestrator 初始化完成")

    async def coordinate(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        编排入口：协调多 Agent 完成复杂任务

        :param query: 用户原始请求
        :param context: 附加上下文（对话历史、元数据等）
        :param session_id: 会话 ID（用于追踪）
        :return: 整合后的最终结果
        """
        parent_step_id = str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())

        logger.info(
            "Supervisor 开始编排 | session={} | step={} | query={}",
            session_id, parent_step_id, query[:100],
        )

        # ─── Step 1: Planner 拆解子任务 ───────────────────────────────────────
        task_plan: TaskPlan = await write_todos(query, context)
        logger.info(
            "任务拆解完成 | 子任务数={} | tasks={}",
            len(task_plan.tasks),
            [t.title for t in task_plan.tasks],
        )

        # ─── Step 2: 构建子 Agent 工具集 ──────────────────────────────────────
        subagent_tools = build_subagent_tools()
        tool_descriptions = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in subagent_tools
        )

        # ─── Step 3: 按顺序执行子任务 ────────────────────────────────────────
        results: List[Dict[str, Any]] = []
        for task in task_plan.tasks:
            logger.info("执行子任务: {} (agent={})", task.title, task.assigned_agent)
            try:
                result = await self._execute_subtask(
                    task=task,
                    tools=subagent_tools,
                    parent_step_id=parent_step_id,
                    previous_results=results,
                )
                results.append({
                    "task_id": task.task_id,
                    "title": task.title,
                    "status": "completed",
                    "output": result,
                })
            except Exception as exc:
                logger.error("子任务执行失败: {} | error={}", task.title, str(exc))
                results.append({
                    "task_id": task.task_id,
                    "title": task.title,
                    "status": "failed",
                    "error": str(exc),
                })

        # ─── Step 4: 整合结果 ─────────────────────────────────────────────────
        final_output = await self._synthesize_results(query, results)

        logger.info("Supervisor 编排完成 | session={} | 成功={}/{}",
                    session_id,
                    sum(1 for r in results if r["status"] == "completed"),
                    len(results))

        return {
            "session_id": session_id,
            "parent_step_id": parent_step_id,
            "task_plan": task_plan.model_dump(),
            "subtask_results": results,
            "final_output": final_output,
        }

    async def _execute_subtask(
        self,
        task: Any,
        tools: List[Any],
        parent_step_id: str,
        previous_results: List[Dict[str, Any]],
    ) -> str:
        """委派单个子任务给对应的子 Agent"""
        # 找到匹配的 tool
        target_tool = None
        for tool in tools:
            if task.assigned_agent in tool.name:
                target_tool = tool
                break

        if target_tool is None:
            # 回落到第一个可用工具
            target_tool = tools[0] if tools else None
            if target_tool is None:
                raise RuntimeError(f"无可用子 Agent 工具执行任务: {task.title}")

        # 构造子任务输入（包含父步骤追踪信息）
        subtask_input = (
            f"[parent_step_id={parent_step_id}]\n"
            f"任务: {task.title}\n"
            f"描述: {task.description}\n"
            f"上下文（前序任务结果）: {previous_results[-3:] if previous_results else '无'}"
        )

        result = await target_tool.ainvoke(subtask_input)
        return str(result)

    async def _synthesize_results(
        self,
        original_query: str,
        results: List[Dict[str, Any]],
    ) -> str:
        """整合所有子任务结果为最终输出"""
        synthesis_prompt = (
            f"用户原始请求: {original_query}\n\n"
            f"各子任务执行结果:\n"
        )
        for r in results:
            status_icon = "✅" if r["status"] == "completed" else "❌"
            synthesis_prompt += f"{status_icon} {r['title']}: {r.get('output', r.get('error', ''))}\n"

        synthesis_prompt += "\n请整合以上结果，给出完整、连贯的最终回复。"

        messages = [
            SystemMessage(content="你是结果整合专家，将多个子任务的输出合并为统一、连贯的回复。"),
            HumanMessage(content=synthesis_prompt),
        ]

        response = await self._llm.ainvoke(messages)
        return response.content


# ── 模块级单例工厂 ─────────────────────────────────────────────────────────────

_supervisor_instance: SupervisorOrchestrator | None = None


def get_supervisor() -> SupervisorOrchestrator:
    """获取全局 SupervisorOrchestrator 单例"""
    global _supervisor_instance
    if _supervisor_instance is None:
        _supervisor_instance = SupervisorOrchestrator()
    return _supervisor_instance
