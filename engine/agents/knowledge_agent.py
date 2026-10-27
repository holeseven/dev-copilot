# @author lxy
"""
知识检索 Agent

负责通过 workspace 绑定的 RAG search_workspace 工具检索项目文档，
将检索结果整合后返回给用户。适用于知识查询、文档搜索等场景。
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from engine.infra.llm_client import Complexity, get_llm_by_complexity


# ─── Agent 系统提示 ────────────────────────────────────────────────────────────────

KNOWLEDGE_AGENT_SYSTEM_PROMPT = """你是知识检索助手，擅长从知识库中查找相关文档并组织答案。

你的职责：
1. 分析用户的问题，提取关键检索词
2. 调用 workspace 绑定的 search_workspace 工具搜索索引
3. 根据检索到的文档内容，组织一个准确、完整的回答
4. 如果知识库中没有相关信息，诚实告知用户

注意事项：
- 回答时引用文档来源，便于用户追溯
- 如果检索结果不够精确，可以尝试不同的关键词重新检索
- 对检索到的信息进行整合和归纳，而非简单堆砌
"""


# ─── KnowledgeAgent 类 ─────────────────────────────────────────────────────────────

class KnowledgeAgent:
    """
    知识检索 Agent

    通过 RAG search_workspace 工具进行项目知识检索，
    支持多轮检索-整合-回答的 ReAct 流程。
    """

    AGENT_ID = "knowledge_agent"
    DESCRIPTION = "知识检索与文档查询 Agent，通过 RAG 搜索知识库回答问题"

    def __init__(
        self,
        llm_complexity: Complexity = Complexity.L1,
        workspace_search: Optional[Callable[[str, int], Any]] = None,
    ):
        """
        Args:
            llm_complexity: LLM 复杂度级别，知识检索通常 L1 即可
        """
        self._llm = get_llm_by_complexity(llm_complexity)
        self._workspace_search = workspace_search
        self._tools = self._build_tools()
        logger.info("KnowledgeAgent 初始化完成")

    def _build_tools(self) -> List[Dict[str, Any]]:
        """构建知识检索可用的工具列表"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_workspace",
                    "description": "在知识库中搜索相关文档。输入查询关键词，返回匹配的文档片段。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词或查询语句",
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "返回结果数量上限，默认 5",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]

    async def run(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        执行知识检索任务。

        Args:
            query: 用户查询
            history: 对话历史

        Returns:
            Agent 回答文本
        """
        logger.info("KnowledgeAgent.run | query={}", query[:100])

        messages = [SystemMessage(content=KNOWLEDGE_AGENT_SYSTEM_PROMPT)]

        # 注入历史
        if history:
            for turn in history[-5:]:
                messages.append(HumanMessage(content=turn.get("q", "")))
                messages.append(AIMessage(content=turn.get("a", "")))

        messages.append(HumanMessage(content=query))

        # 绑定工具并调用 LLM
        llm_with_tools = self._llm.bind_tools(self._tools)

        try:
            response: AIMessage = await llm_with_tools.ainvoke(messages)

            # 如果 LLM 要求调用工具，执行 RAG 检索
            if getattr(response, "tool_calls", None):
                search_results = await self._execute_search(response.tool_calls)
                # 将搜索结果追加后再请求 LLM 生成最终答案
                messages.append(response)
                for result in search_results:
                    messages.append(result)
                final_response = await self._llm.ainvoke(messages)
                return final_response.content or "未能生成答案"

            return response.content or "未找到相关信息"

        except Exception:
            logger.error("knowledge_agent_run_failed exception_type=internal_error")
            return "知识检索暂时不可用，请稍后重试。"

    async def _execute_search(self, tool_calls: list) -> list:
        """
        执行 workspace 绑定的 RAG search_workspace 调用。

        Args:
            tool_calls: LLM 生成的工具调用列表

        Returns:
            ToolMessage 列表
        """
        from langchain_core.messages import ToolMessage

        results = []
        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {})
            tool_id = call.get("id", "")

            if tool_name == "search_workspace":
                try:
                    query = tool_args.get("query", "")
                    top_k = tool_args.get("top_k", 5)
                    if self._workspace_search is None:
                        raise RuntimeError("workspace-scoped RAG search is unavailable")
                    docs = self._workspace_search(query, top_k)
                    if inspect.isawaitable(docs):
                        docs = await docs
                    content = (
                        json.dumps(docs, ensure_ascii=False, sort_keys=True)
                        if isinstance(docs, dict)
                        else self._format_search_results(docs)
                    )
                except Exception:
                    logger.error("knowledge_agent_search_failed exception_type=internal_error")
                    content = "[ERROR] 知识库检索暂时不可用，请稍后重试。"
            else:
                content = f"[ERROR] 未知工具: {tool_name}"

            results.append(ToolMessage(content=content, tool_call_id=tool_id))

        return results

    @staticmethod
    def _format_search_results(docs: Any) -> str:
        """格式化检索结果为可读文本"""
        if not docs:
            return "未找到相关文档。"

        if isinstance(docs, str):
            return docs

        if isinstance(docs, list):
            lines = []
            for i, doc in enumerate(docs, 1):
                if isinstance(doc, dict):
                    content = doc.get("content", doc.get("page_content", str(doc)))
                    source = doc.get("source", "unknown")
                    lines.append(f"[{i}] (来源: {source})\n{content}")
                else:
                    lines.append(f"[{i}] {str(doc)}")
            return "\n\n".join(lines)

        return str(docs)
