# @author lxy
"""
OpenSpec 四阶段处理函数

每个阶段独立的处理逻辑 + prompt 模板引用：
1. proposal  — 方案提议：分析需求，生成解决方案概述
2. design    — 架构设计：详细设计模块划分、接口定义、数据流
3. tasks     — 任务拆解：将设计转化为具体可执行的任务清单
4. apply     — 执行落地：调用 Supervisor 按计划执行所有任务
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from engine.infra.llm_client import Complexity, get_llm_by_complexity

if TYPE_CHECKING:
    from .workflow import WorkflowState

# ─── 模板路径 ──────────────────────────────────────────────────────────────────

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _load_template(name: str) -> str:
    """加载 prompt 模板文件"""
    template_path = os.path.join(_TEMPLATES_DIR, name)
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("模板文件未找到: {}，使用内联默认", template_path)
        return ""


# ─── 阶段 Prompt 模板 ─────────────────────────────────────────────────────────

PROPOSAL_SYSTEM_PROMPT = """你是方案提议专家。基于用户需求，生成一份解决方案提议。

提议须包含：
1. 需求理解 — 复述核心需求与约束
2. 方案概述 — 整体解决思路（1-3 段话）
3. 预期成果 — 最终交付物描述
4. 风险与假设 — 已知风险和前提假设

{template_content}
"""

DESIGN_SYSTEM_PROMPT = """你是架构设计专家。基于已确认的方案提议，输出详细的架构设计。

设计须包含：
1. 模块划分 — 系统/功能模块清单及职责
2. 接口定义 — 模块间关键接口描述
3. 数据流 — 核心数据流转路径
4. 技术选型 — 关键技术决策及理由

方案提议（已确认）：
{proposal}

{template_content}
"""

TASKS_SYSTEM_PROMPT = """你是任务拆解专家。基于已确认的架构设计，将其拆解为具体可执行的任务清单。

要求：
1. 每个任务足够原子化，单人可在 1-4 小时内完成
2. 任务间有明确的依赖关系和执行顺序
3. 每个任务包含：标题、描述、验收标准、预估耗时

架构设计（已确认）：
{design}

{template_content}
"""

APPLY_SYSTEM_PROMPT = """你是执行协调专家。基于已确认的任务清单，协调执行并汇报进度。

当前执行的任务清单：
{tasks}

请逐个确认每个任务的执行状态，整合为最终执行报告。
"""


# ─── 阶段处理函数 ──────────────────────────────────────────────────────────────


async def run_proposal_stage(state: "WorkflowState") -> str:
    """
    阶段一：方案提议

    分析用户需求，生成解决方案概述。
    如有用户修改反馈（modify），结合反馈重新生成。
    """
    logger.info(">>> 进入 Proposal 阶段")

    template_content = _load_template("proposal.md")
    llm = get_llm_by_complexity(Complexity.L2, disable_streaming=False)

    system_prompt = PROPOSAL_SYSTEM_PROMPT.format(template_content=template_content)

    user_content = f"用户需求：\n{state.original_query}"
    if state.user_feedback:
        user_content += f"\n\n用户修改意见：\n{state.user_feedback}"
    if state.context:
        user_content += f"\n\n附加上下文：\n{state.context}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    response = await llm.ainvoke(messages)
    output = response.content
    logger.info("Proposal 阶段完成 | output_len={}", len(output))
    return output


async def run_design_stage(state: "WorkflowState") -> str:
    """
    阶段二：架构设计

    基于已确认的 proposal，输出详细设计。
    """
    logger.info(">>> 进入 Design 阶段")

    template_content = _load_template("design.md")
    llm = get_llm_by_complexity(Complexity.L2, disable_streaming=False)

    system_prompt = DESIGN_SYSTEM_PROMPT.format(
        proposal=state.proposal_output or "（未提供）",
        template_content=template_content,
    )

    user_content = f"原始需求：\n{state.original_query}"
    if state.user_feedback:
        user_content += f"\n\n用户修改意见：\n{state.user_feedback}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    response = await llm.ainvoke(messages)
    output = response.content
    logger.info("Design 阶段完成 | output_len={}", len(output))
    return output


async def run_tasks_stage(state: "WorkflowState") -> str:
    """
    阶段三：任务拆解

    基于已确认的 design，生成可执行的任务清单。
    """
    logger.info(">>> 进入 Tasks 阶段")

    template_content = _load_template("tasks.md")
    llm = get_llm_by_complexity(Complexity.L2, disable_streaming=False)

    system_prompt = TASKS_SYSTEM_PROMPT.format(
        design=state.design_output or "（未提供）",
        template_content=template_content,
    )

    user_content = f"原始需求：\n{state.original_query}"
    if state.user_feedback:
        user_content += f"\n\n用户修改意见：\n{state.user_feedback}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    response = await llm.ainvoke(messages)
    output = response.content
    logger.info("Tasks 阶段完成 | output_len={}", len(output))
    return output


async def run_apply_stage(state: "WorkflowState") -> str:
    """
    阶段四：执行落地

    调用 Supervisor 编排器，按任务清单逐个执行。
    整合执行结果为最终报告。
    """
    logger.info(">>> 进入 Apply 阶段")

    from engine.core.orchestrator.supervisor import get_supervisor

    supervisor = get_supervisor()

    # 将 tasks 阶段的输出作为 Supervisor 的输入
    task_description = state.tasks_output or state.original_query
    context = {
        "proposal": state.proposal_output,
        "design": state.design_output,
        "tasks": state.tasks_output,
        **state.context,
    }

    try:
        result = await supervisor.coordinate(
            query=task_description,
            context=context,
        )
        output = result.get("final_output", str(result))
        logger.info("Apply 阶段完成 | output_len={}", len(output))
        return output

    except Exception as exc:
        error_msg = f"Apply 阶段执行失败: {str(exc)}"
        logger.error(error_msg)
        return error_msg
