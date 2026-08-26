# @author lxy
"""
路由决策数据模型

定义 LLM 路由输出结构与最终路由决策结果，
供 AgentDispatcher 在结构化输出解析中使用。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RoutingLLMOutput(BaseModel):
    """LLM 结构化输出格式 —— Router 模型直接返回此 JSON"""

    target_agent_index: int = Field(
        ...,
        description="目标 Agent 在候选列表中的索引（从 0 开始）",
    )
    complexity_level: Optional[Literal["L1", "L2"]] = Field(
        default=None,
        description="任务复杂度分级：L1=简单, L2=复杂；无法判断时为 None",
    )
    reasoning: str = Field(
        default="",
        description="路由推理过程简述（供 debug / 日志使用）",
    )


class RoutingDecision(BaseModel):
    """最终路由决策 —— Dispatcher 对外返回的标准结构"""

    routing_type: Literal["direct", "coordinate"] = Field(
        default="direct",
        description="路由方式：direct=单 Agent 直接处理, coordinate=需多 Agent 协作",
    )
    target_agent_id: str = Field(
        ...,
        description="目标 Agent 唯一标识",
    )
    target_agent_index: int = Field(
        ...,
        description="目标 Agent 在候选列表中的索引",
    )
    complexity_level: Optional[Literal["L1", "L2"]] = Field(
        default="L2",
        description="任务复杂度，用于下游选择对应的 LLM 模型",
    )
