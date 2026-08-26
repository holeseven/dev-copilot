# @author lxy
"""
Dispatcher 配置模块

定义路由分发器的静态配置：候选 Agent 列表、fallback 策略、
复杂度到模型的映射关系、Router 模型名称及超时阈值等。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AgentCandidate:
    """候选 Agent 描述"""

    agent_id: str
    name: str
    description: str


@dataclass
class DispatcherConfig:
    """
    路由分发器配置

    - candidates: 候选 Agent 列表（顺序即 index）
    - fallback_agent_id: 超时/异常时兜底 Agent
    - complexity_model_map: 复杂度 → LLM 模型名映射
    - router_model: 执行路由决策的模型名（轻量 Flash 模型）
    - timeout_ms: 路由超时阈值（毫秒），超时后走规则兜底
    """

    candidates: List[AgentCandidate] = field(default_factory=lambda: [
        AgentCandidate(
            agent_id="knowledge_agent",
            name="知识问答 Agent",
            description="处理知识查询、术语解释、文档检索等问答类任务",
        ),
        AgentCandidate(
            agent_id="task_agent",
            name="任务执行 Agent",
            description="处理需要多步操作、工具调用、流程编排的执行类任务",
        ),
        AgentCandidate(
            agent_id="qa_agent",
            name="通用对话 Agent",
            description="处理日常问候、闲聊、简单单轮对话",
        ),
    ])

    fallback_agent_id: str = "qa_agent"

    complexity_model_map: Dict[str, str] = field(default_factory=lambda: {
        "L1": "deepseek-chat",        # 轻量快速模型（路由/简单问答）
        "L2": "deepseek-reasoner",    # 强推理模型（复杂多步任务）
    })

    router_model: str = "deepseek-chat"  # Router 自身使用的模型（需快速响应）

    timeout_ms: int = 800  # 路由超时阈值（毫秒），超过则降级为规则兜底


# ── 模块级单例 ──────────────────────────────────────────────────────────────────

_default_config: DispatcherConfig | None = None


def get_dispatcher_config() -> DispatcherConfig:
    """获取全局 Dispatcher 配置单例"""
    global _default_config
    if _default_config is None:
        _default_config = DispatcherConfig()
    return _default_config
