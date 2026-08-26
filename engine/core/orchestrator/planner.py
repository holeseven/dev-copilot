# @author lxy
"""
任务规划器（Planner）

核心职责：
- 将 L2 复杂任务拆解为带依赖顺序的子任务列表（write_todos）
- 每个子任务明确指定负责的 Agent、输入描述、依赖关系
- 使用 LLM 结构化输出保证输出格式一致性

输出结构：TaskPlan → List[SubTask]
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field

from engine.infra.llm_client import Complexity, get_llm_by_complexity

# ─── 数据模型 ─────────────────────────────────────────────────────────────────


class SubTask(BaseModel):
    """单个子任务定义"""

    task_id: str = Field(description="子任务唯一标识，如 t1, t2, t3")
    title: str = Field(description="子任务标题（简洁描述）")
    description: str = Field(description="子任务详细描述，包含输入/输出要求")
    assigned_agent: str = Field(description="负责执行的 Agent ID")
    depends_on: List[str] = Field(
        default_factory=list,
        description="依赖的前置任务 ID 列表（空表示无依赖可并行）",
    )
    priority: int = Field(default=0, description="优先级（越小越先执行）")


class TaskPlan(BaseModel):
    """任务拆解计划"""

    goal: str = Field(description="原始任务目标概述")
    tasks: List[SubTask] = Field(description="拆解后的有序子任务列表")
    execution_strategy: str = Field(
        default="sequential",
        description="执行策略: sequential(严格顺序) / parallel_groups(分组并行)",
    )


# ─── Planner Prompt ───────────────────────────────────────────────────────────

PLANNER_SYSTEM_PROMPT = """你是一个任务规划专家，负责将复杂任务拆解为可执行的子任务列表。

规则：
1. 每个子任务必须足够原子化，能被单个 Agent 独立完成
2. 明确指定每个子任务的依赖关系（depends_on），确保执行顺序正确
3. 为每个子任务分配最合适的 Agent（assigned_agent）
4. task_id 使用 t1, t2, t3... 格式

可用的 Agent 及其能力：
- knowledge_agent: 知识检索、文档查询、概念解释
- task_agent: 工单创建、状态变更、部署发布等操作
- code_agent: 代码生成、审查、重构
- qa_agent: 通用问答、简单交互

请以 JSON 格式输出 TaskPlan 结构。
"""


# ─── 核心函数 ─────────────────────────────────────────────────────────────────


async def write_todos(
    query: str,
    context: Optional[Dict[str, Any]] = None,
) -> TaskPlan:
    """
    任务规划入口：将复杂任务拆解为带依赖关系的子任务列表

    :param query: 用户原始复杂请求
    :param context: 附加上下文信息
    :return: TaskPlan 任务计划
    """
    logger.info("Planner 开始规划 | query={}", query[:100])

    # 获取 L2 模型（禁用 streaming 以支持结构化输出）
    llm = get_llm_by_complexity(
        complexity=Complexity.L2,
        disable_streaming=True,
    )

    # 绑定结构化输出
    structured_llm = llm.with_structured_output(TaskPlan, method="json_mode")

    # 构建提示消息
    user_content = f"请将以下复杂任务拆解为子任务计划：\n\n{query}"
    if context:
        user_content += f"\n\n附加上下文：\n{_format_context(context)}"

    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    # 调用 LLM 进行任务拆解
    try:
        task_plan: TaskPlan = await structured_llm.ainvoke(messages)
        logger.info(
            "Planner 规划完成 | 子任务数={} | strategy={}",
            len(task_plan.tasks), task_plan.execution_strategy,
        )
        return task_plan

    except Exception as exc:
        logger.error("Planner 规划失败，使用兜底单任务计划 | error={}", str(exc))
        # 兜底：规划失败则把整个任务作为单一子任务
        return TaskPlan(
            goal=query,
            tasks=[
                SubTask(
                    task_id="t1",
                    title="执行原始任务",
                    description=query,
                    assigned_agent="task_agent",
                    depends_on=[],
                    priority=0,
                )
            ],
            execution_strategy="sequential",
        )


def _format_context(context: Dict[str, Any]) -> str:
    """格式化上下文为可读文本"""
    lines = []
    for key, value in context.items():
        if isinstance(value, list):
            lines.append(f"- {key}: {len(value)} 项")
        else:
            lines.append(f"- {key}: {str(value)[:200]}")
    return "\n".join(lines)


def sort_tasks_by_dependency(tasks: List[SubTask]) -> List[SubTask]:
    """
    拓扑排序：按依赖关系排列子任务执行顺序

    :param tasks: 未排序的子任务列表
    :return: 按依赖关系排序后的列表
    """
    task_map = {t.task_id: t for t in tasks}
    visited: set = set()
    result: List[SubTask] = []

    def _visit(task_id: str) -> None:
        if task_id in visited:
            return
        visited.add(task_id)
        task = task_map.get(task_id)
        if task is None:
            return
        for dep_id in task.depends_on:
            _visit(dep_id)
        result.append(task)

    for task in tasks:
        _visit(task.task_id)

    return result
