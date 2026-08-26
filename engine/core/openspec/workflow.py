# @author lxy
"""
OpenSpec 四阶段工作流编排

核心流程：proposal → design → tasks → apply
每阶段结束后设置 HITL（Human-In-The-Loop）断点，等待用户确认或修改后才进入下一阶段。

设计原则：
- 每个阶段独立可回退，用户可在任意断点修改方向
- 阶段间通过 WorkflowState 传递上下文
- 支持 SSE 流式输出中间过程
- apply 阶段调用 Supervisor 执行实际任务
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, AsyncGenerator, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from .stages import (
    run_apply_stage,
    run_design_stage,
    run_proposal_stage,
    run_tasks_stage,
)


class OpenSpecStage(str, Enum):
    """OpenSpec 工作流阶段"""

    PROPOSAL = "proposal"
    DESIGN = "design"
    TASKS = "tasks"
    APPLY = "apply"
    COMPLETED = "completed"


class HITLBreakpoint(BaseModel):
    """HITL 断点信息"""

    stage: OpenSpecStage = Field(description="当前阶段")
    output: str = Field(description="当前阶段输出内容")
    requires_approval: bool = Field(default=True, description="是否需要用户确认")
    options: Dict[str, str] = Field(
        default_factory=lambda: {
            "approve": "确认并继续下一阶段",
            "modify": "修改当前阶段输出",
            "reject": "拒绝并终止流程",
        },
        description="用户可选的操作",
    )


class WorkflowState(BaseModel):
    """工作流状态（跨阶段传递）"""

    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    current_stage: OpenSpecStage = Field(default=OpenSpecStage.PROPOSAL)
    original_query: str = Field(description="用户原始请求")
    context: Dict[str, Any] = Field(default_factory=dict, description="附加上下文")

    # 各阶段输出存储
    proposal_output: Optional[str] = None
    design_output: Optional[str] = None
    tasks_output: Optional[str] = None
    apply_output: Optional[str] = None

    # HITL 状态
    awaiting_user_input: bool = Field(default=False)
    user_feedback: Optional[str] = None


class OpenSpecWorkflow:
    """
    OpenSpec 四阶段工作流编排器

    流程：
    1. proposal  — 生成方案提议，描述整体解决思路
    2. design    — 详细架构设计，包含模块划分与接口
    3. tasks     — 将设计拆解为可执行的具体任务清单
    4. apply     — 调用 Supervisor 逐个执行任务

    每阶段结束产生 HITL 断点，用户可 approve / modify / reject。
    """

    def __init__(self) -> None:
        logger.info("OpenSpecWorkflow 初始化完成")

    async def start(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowState:
        """
        启动 OpenSpec 工作流

        :param query: 用户原始需求描述
        :param context: 附加上下文
        :return: 初始工作流状态
        """
        state = WorkflowState(
            original_query=query,
            context=context or {},
            current_stage=OpenSpecStage.PROPOSAL,
        )
        logger.info("OpenSpec 工作流启动 | id={} | query={}", state.workflow_id, query[:80])
        return state

    async def run_stage(
        self,
        state: WorkflowState,
    ) -> tuple[WorkflowState, HITLBreakpoint]:
        """
        执行当前阶段并返回 HITL 断点

        :param state: 当前工作流状态
        :return: (更新后的状态, HITL 断点信息)
        """
        stage = state.current_stage

        logger.info(
            "执行 OpenSpec 阶段: {} | workflow={}",
            stage.value, state.workflow_id,
        )

        if stage == OpenSpecStage.PROPOSAL:
            output = await run_proposal_stage(state)
            state.proposal_output = output

        elif stage == OpenSpecStage.DESIGN:
            output = await run_design_stage(state)
            state.design_output = output

        elif stage == OpenSpecStage.TASKS:
            output = await run_tasks_stage(state)
            state.tasks_output = output

        elif stage == OpenSpecStage.APPLY:
            output = await run_apply_stage(state)
            state.apply_output = output
            # apply 完成后流程结束
            state.current_stage = OpenSpecStage.COMPLETED
            return state, HITLBreakpoint(
                stage=OpenSpecStage.APPLY,
                output=output,
                requires_approval=False,
                options={"done": "流程已完成"},
            )

        else:
            raise ValueError(f"未知阶段: {stage}")

        # 设置 HITL 断点
        state.awaiting_user_input = True
        breakpoint_info = HITLBreakpoint(
            stage=stage,
            output=output,
            requires_approval=True,
        )

        logger.info(
            "HITL 断点 | stage={} | workflow={} | output_len={}",
            stage.value, state.workflow_id, len(output),
        )

        return state, breakpoint_info

    async def advance(
        self,
        state: WorkflowState,
        user_decision: str = "approve",
        user_feedback: Optional[str] = None,
    ) -> WorkflowState:
        """
        处理用户在 HITL 断点的决策，推进工作流

        :param state: 当前工作流状态
        :param user_decision: approve / modify / reject
        :param user_feedback: 用户反馈（modify 时提供）
        :return: 更新后的工作流状态
        """
        state.awaiting_user_input = False
        state.user_feedback = user_feedback

        if user_decision == "reject":
            logger.info("用户拒绝，终止工作流 | workflow={}", state.workflow_id)
            state.current_stage = OpenSpecStage.COMPLETED
            return state

        if user_decision == "modify":
            # 保持当前阶段不变，下次 run_stage 会结合 user_feedback 重新生成
            logger.info("用户要求修改，重新执行当前阶段 | stage={}", state.current_stage.value)
            return state

        # approve → 推进到下一阶段
        stage_order = [
            OpenSpecStage.PROPOSAL,
            OpenSpecStage.DESIGN,
            OpenSpecStage.TASKS,
            OpenSpecStage.APPLY,
        ]
        current_idx = stage_order.index(state.current_stage)
        if current_idx < len(stage_order) - 1:
            state.current_stage = stage_order[current_idx + 1]
            logger.info(
                "推进到下一阶段: {} | workflow={}",
                state.current_stage.value, state.workflow_id,
            )
        else:
            state.current_stage = OpenSpecStage.COMPLETED

        return state

    async def run_full_workflow(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], str]:
        """
        完整工作流生成器（SSE 兼容）

        yield 每个阶段的断点信息，通过 send() 接收用户决策。
        适配 SSE 流式场景。

        :param query: 用户需求
        :param context: 上下文
        :yields: 各阶段 HITL 断点信息
        """
        state = await self.start(query, context)

        while state.current_stage != OpenSpecStage.COMPLETED:
            state, breakpoint_info = await self.run_stage(state)

            # yield 断点让外层收集用户决策
            user_decision = yield {
                "type": "hitl_breakpoint",
                "workflow_id": state.workflow_id,
                "stage": breakpoint_info.stage.value,
                "output": breakpoint_info.output,
                "options": breakpoint_info.options,
                "requires_approval": breakpoint_info.requires_approval,
            }

            if not breakpoint_info.requires_approval:
                break

            # 处理用户决策
            decision = user_decision if user_decision else "approve"
            state = await self.advance(state, user_decision=decision)

        # 最终输出
        yield {
            "type": "workflow_complete",
            "workflow_id": state.workflow_id,
            "final_output": state.apply_output or state.tasks_output or "流程已完成",
        }


# ── 模块级工厂 ─────────────────────────────────────────────────────────────────

_workflow_instance: OpenSpecWorkflow | None = None


def get_openspec_workflow() -> OpenSpecWorkflow:
    """获取全局 OpenSpecWorkflow 单例"""
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = OpenSpecWorkflow()
    return _workflow_instance
