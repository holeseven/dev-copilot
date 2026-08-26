# @author lxy
"""
Agent 路由分发器

核心职责：
1. 接收用户 query + 历史对话，调用 Router LLM 结构化输出判定目标 Agent 及复杂度
2. 800ms 超时保护 —— 超时自动降级到基于关键词的规则兜底
3. 对外暴露 `route()` 异步方法，返回标准化 RoutingDecision
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from engine.infra.llm_client import Complexity, get_llm_by_complexity

from .config import AgentCandidate, DispatcherConfig, get_dispatcher_config
from .models import RoutingDecision, RoutingLLMOutput
from .prompts import ROUTER_SYSTEM_PROMPT


class AgentDispatcher:
    """
    智能路由分发器

    使用 Flash LLM 结构化输出做意图识别 + 复杂度判定，
    附带 800ms 超时兜底，确保端到端路由延迟可控。
    """

    def __init__(self, config: Optional[DispatcherConfig] = None) -> None:
        self._config = config or get_dispatcher_config()

        # 获取 L1（轻量）模型用于路由决策，禁用 streaming 以支持结构化输出
        self._router_llm = get_llm_by_complexity(
            complexity=Complexity.L1,
            disable_streaming=True,
        )
        # 绑定结构化输出（JSON mode）
        self._structured_llm = self._router_llm.with_structured_output(
            RoutingLLMOutput,
            method="json_mode",
        )

        logger.info(
            "AgentDispatcher 初始化完成 | candidates={} | timeout={}ms",
            len(self._config.candidates),
            self._config.timeout_ms,
        )

    # ─── 公开接口 ────────────────────────────────────────────────────────────────

    async def route(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> RoutingDecision:
        """
        路由入口：判定用户请求应分派到哪个 Agent

        :param query: 用户当前输入
        :param history: 对话历史（可选）
        :return: RoutingDecision 路由决策
        """
        timeout_sec = self._config.timeout_ms / 1000.0

        try:
            decision = await asyncio.wait_for(
                self._llm_route(query, history),
                timeout=timeout_sec,
            )
            logger.info(
                "LLM 路由成功 | agent={} | complexity={} | reasoning={}",
                decision.target_agent_id,
                decision.complexity_level,
                decision.reasoning if hasattr(decision, "reasoning") else "",
            )
            return decision

        except asyncio.TimeoutError:
            logger.warning(
                "LLM 路由超时（{}ms），降级到规则兜底 | query={}",
                self._config.timeout_ms,
                query[:80],
            )
            return self._rule_based_fallback(query)

        except Exception as exc:
            logger.opt(exception=True).error(
                "LLM 路由异常，降级到规则兜底 | error={}",
                str(exc),
            )
            return self._rule_based_fallback(query)

    # ─── 内部方法 ────────────────────────────────────────────────────────────────

    async def _llm_route(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> RoutingDecision:
        """调用 LLM 结构化输出获取路由结果"""
        # 组装 agent_list 描述
        agent_list_text = self._format_agent_list()

        # 填充 system prompt
        system_prompt = ROUTER_SYSTEM_PROMPT.format(agent_list=agent_list_text)

        # 构建消息
        messages = [SystemMessage(content=system_prompt)]

        # 附加历史上下文（取最近 5 轮）
        if history:
            for msg in history[-5:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))

        # 当前用户输入
        messages.append(HumanMessage(content=query))

        # 调用结构化 LLM
        llm_output: RoutingLLMOutput = await self._structured_llm.ainvoke(messages)

        # 校验 index 范围
        idx = llm_output.target_agent_index
        if idx < 0 or idx >= len(self._config.candidates):
            logger.warning(
                "LLM 返回索引越界: {} (总数 {})，使用 fallback",
                idx,
                len(self._config.candidates),
            )
            return self._rule_based_fallback(query)

        target = self._config.candidates[idx]
        return RoutingDecision(
            routing_type="direct",
            target_agent_id=target.agent_id,
            target_agent_index=idx,
            complexity_level=llm_output.complexity_level or "L2",
        )

    def _rule_based_fallback(self, query: str) -> RoutingDecision:
        """
        基于关键词的规则兜底路由

        当 LLM 超时或异常时调用，根据简单关键词匹配路由到对应 Agent；
        若无法匹配则路由到 fallback agent，复杂度默认 L2。
        """
        query_lower = query.lower()

        # 关键词 → agent_id 映射规则
        keyword_rules: List[tuple[List[str], str]] = [
            (["查询", "查一下", "单号", "运单", "物流", "快递", "搜索", "知识", "什么是", "解释"], "knowledge_agent"),
            (["执行", "创建", "修改", "删除", "部署", "发布", "操作", "任务", "工单"], "task_agent"),
            (["你好", "hi", "hello", "闲聊", "聊天", "谢谢", "再见"], "qa_agent"),
        ]

        for keywords, agent_id in keyword_rules:
            if any(kw in query_lower for kw in keywords):
                # 找到对应 candidate 的 index
                idx = self._find_agent_index(agent_id)
                return RoutingDecision(
                    routing_type="direct",
                    target_agent_id=agent_id,
                    target_agent_index=idx,
                    complexity_level="L2",
                )

        # 完全无法匹配 → fallback
        fallback_id = self._config.fallback_agent_id
        fallback_idx = self._find_agent_index(fallback_id)
        return RoutingDecision(
            routing_type="direct",
            target_agent_id=fallback_id,
            target_agent_index=fallback_idx,
            complexity_level="L2",
        )

    def _find_agent_index(self, agent_id: str) -> int:
        """根据 agent_id 查找其在候选列表中的索引"""
        for i, candidate in enumerate(self._config.candidates):
            if candidate.agent_id == agent_id:
                return i
        return 0  # 找不到则默认第一个

    def _format_agent_list(self) -> str:
        """将候选 Agent 列表格式化为 prompt 中可阅读的文本"""
        lines = []
        for i, agent in enumerate(self._config.candidates):
            lines.append(f"[{i}] {agent.agent_id} — {agent.name}: {agent.description}")
        return "\n".join(lines)


# ── 模块级单例工厂 ────────────────────────────────────────────────────────────────

_dispatcher_instance: AgentDispatcher | None = None


def get_agent_dispatcher(config: Optional[DispatcherConfig] = None) -> AgentDispatcher:
    """获取全局 AgentDispatcher 单例"""
    global _dispatcher_instance
    if _dispatcher_instance is None:
        _dispatcher_instance = AgentDispatcher(config=config)
    return _dispatcher_instance
