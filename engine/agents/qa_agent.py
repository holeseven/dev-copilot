# @author lxy
"""
简单问答 Agent

负责处理通用问答、闲聊、打招呼等无需工具调用的简单对话场景。
直接使用 LLM 生成回答，不依赖外部工具。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from engine.infra.llm_client import Complexity, get_llm_by_complexity


# ─── Agent 系统提示 ────────────────────────────────────────────────────────────────

QA_AGENT_SYSTEM_PROMPT = """你是一个友善、专业的 AI 问答助手。

你的职责：
1. 回答用户的通用问题，提供准确、有帮助的信息
2. 进行自然、友好的对话
3. 对于超出知识范围的问题，诚实告知并建议用户寻求其他帮助

对话风格：
- 简洁明了，避免冗余
- 必要时用列表或结构化方式组织答案
- 保持专业但不失亲和力
- 如果问题涉及代码/技术，给出具体且可操作的建议

注意：
- 你不具备工具调用能力，如果用户需要执行操作（如文件读写、搜索），
  请告知他们可以明确提出执行需求，系统会将其路由到合适的 Agent
"""


# ─── QAAgent 类 ────────────────────────────────────────────────────────────────────

class QAAgent:
    """
    简单问答 Agent

    使用轻量 LLM (L1) 进行通用对话，无工具依赖。
    适用场景：闲聊、简单问答、打招呼、知识解释等。
    """

    AGENT_ID = "qa_agent"
    DESCRIPTION = "通用问答 Agent，处理闲聊、简单问答等无需工具调用的对话"

    def __init__(self, llm_complexity: Complexity = Complexity.L1):
        """
        Args:
            llm_complexity: LLM 复杂度，简单问答用 L1 即可（快速响应）
        """
        self._llm = get_llm_by_complexity(llm_complexity)
        logger.info("QAAgent 初始化完成 | complexity={}", llm_complexity.value)

    async def run(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        执行问答。

        Args:
            query: 用户问题
            history: 对话历史

        Returns:
            Agent 回答文本
        """
        logger.info("QAAgent.run | query={}", query[:100])

        messages = [SystemMessage(content=QA_AGENT_SYSTEM_PROMPT)]

        # 注入对话历史（最近 5 轮）
        if history:
            for turn in history[-5:]:
                q = turn.get("q", "")
                a = turn.get("a", "")
                if q:
                    messages.append(HumanMessage(content=q))
                if a:
                    messages.append(AIMessage(content=a))

        messages.append(HumanMessage(content=query))

        try:
            response: AIMessage = await self._llm.ainvoke(messages)
            answer = response.content or "抱歉，我暂时无法回答这个问题。"
            logger.info("QAAgent 回答完成 | len={}", len(answer))
            return answer

        except Exception as e:
            logger.error("QAAgent 执行异常: {}", str(e))
            return f"回答过程中出现错误: {str(e)}"

    async def stream_run(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        流式执行问答（生成器，逐 token 输出）。

        Args:
            query: 用户问题
            history: 对话历史

        Yields:
            str: 逐 token 输出的文本片段
        """
        logger.info("QAAgent.stream_run | query={}", query[:100])

        messages = [SystemMessage(content=QA_AGENT_SYSTEM_PROMPT)]

        if history:
            for turn in history[-5:]:
                q = turn.get("q", "")
                a = turn.get("a", "")
                if q:
                    messages.append(HumanMessage(content=q))
                if a:
                    messages.append(AIMessage(content=a))

        messages.append(HumanMessage(content=query))

        try:
            async for chunk in self._llm.astream(messages):
                if chunk.content:
                    yield chunk.content
        except Exception as e:
            logger.error("QAAgent 流式执行异常: {}", str(e))
            yield f"回答过程中出现错误: {str(e)}"
