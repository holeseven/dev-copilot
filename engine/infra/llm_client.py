# @author lxy
"""
LLM 客户端封装（OpenAI 兼容）

- 统一通过工厂方法获取 ChatOpenAI 实例
- 支持按任务复杂度 L1/L2 选择不同模型：
    L1（简单/路由类任务）→ 轻量快速模型
    L2（复杂/多步推理任务）→ 更强模型
- 实例按 (complexity, streaming) 缓存，避免重复初始化开销
"""
from __future__ import annotations

from enum import Enum

from langchain_openai import ChatOpenAI
from loguru import logger

from engine.config.settings import get_settings

# 全局实例缓存：键为 (complexity_level, disable_streaming)
_llm_cache: dict[tuple[str, bool], ChatOpenAI] = {}


class Complexity(str, Enum):
    """任务复杂度分级"""

    L1 = "L1"  # 简单任务：路由判定、意图识别、简单问答
    L2 = "L2"  # 复杂任务：多步推理、工具编排、多 Agent 协作


def _build_llm(model: str, disable_streaming: bool) -> ChatOpenAI:
    """根据模型名构建一个 ChatOpenAI 实例"""
    settings = get_settings()
    return ChatOpenAI(
        model=model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout,
        disable_streaming=disable_streaming,
    )


def get_llm_by_complexity(
    complexity: Complexity = Complexity.L1,
    disable_streaming: bool = False,
) -> ChatOpenAI:
    """
    按复杂度获取 LLM 实例（带缓存）

    :param complexity: L1（轻量）/ L2（增强）
    :param disable_streaming: 是否禁用流式（结构化输出场景常需禁用）
    """
    cache_key = (complexity.value, disable_streaming)
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    settings = get_settings()
    # L2 未单独配置时回落到默认模型
    if complexity == Complexity.L2 and settings.llm_model_l2:
        model = settings.llm_model_l2
    else:
        model = settings.llm_model

    llm = _build_llm(model, disable_streaming)
    _llm_cache[cache_key] = llm
    logger.info("创建 LLM 实例: complexity={} model={} streaming={}", complexity.value, model, not disable_streaming)
    return llm


def clear_llm_cache() -> None:
    """清空 LLM 缓存（配置热更新后调用，使新模型配置生效）"""
    _llm_cache.clear()
    logger.info("LLM 实例缓存已清空")
