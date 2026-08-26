# @author lxy
"""
RAG 检索模块

职责：
- 将用户查询转为向量，从 FAISS 向量库中召回最相关的 top-k 文档块
- 将召回结果格式化注入 prompt 上下文
- 封装为 LangChain Tool，供 Supervisor / Agent 直接调用

适用范围：
- 非结构化知识检索：编码规范、历史设计文档、业务概念、团队约定
- 不用于代码检索（代码检索由 agentic search 专项能力负责）
"""
from __future__ import annotations

from typing import Optional

from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from engine.core.rag.vector_store import FAISSVectorStore, get_vector_store


# ---------------------------------------------------------------------------
# 检索器类
# ---------------------------------------------------------------------------

class DocumentRetriever:
    """
    文档检索器

    封装向量检索 + 结果格式化逻辑，支持：
    - top-k 可配
    - 距离阈值过滤（可选）
    - 输出格式化为上下文字符串
    """

    def __init__(
        self,
        vector_store: Optional[FAISSVectorStore] = None,
        top_k: int = 5,
        max_distance: Optional[float] = None,
    ):
        """
        :param vector_store: 向量存储实例，为 None 则使用全局默认
        :param top_k: 默认召回数量
        :param max_distance: L2 距离阈值，超过则丢弃（None 表示不过滤）
        """
        self._store = vector_store or get_vector_store()
        self._top_k = top_k
        self._max_distance = max_distance

    def retrieve(self, query: str, top_k: Optional[int] = None) -> list[Document]:
        """
        执行检索

        :param query: 用户自然语言查询
        :param top_k: 本次召回数量（覆盖默认值）
        :return: 召回的 Document 列表（已按相似度排序）
        """
        k = top_k or self._top_k
        results = self._store.search(query, top_k=k)

        # 距离阈值过滤
        if self._max_distance is not None:
            results = [
                doc for doc in results
                if doc.metadata.get("score", float("inf")) <= self._max_distance
            ]

        logger.debug(
            "RAG 检索完成: query='{}' 召回 {} 个文档块",
            query[:50],
            len(results),
        )
        return results

    def retrieve_as_context(self, query: str, top_k: Optional[int] = None) -> str:
        """
        检索并格式化为可直接注入 prompt 的上下文字符串

        :param query: 用户查询
        :param top_k: 召回数量
        :return: 格式化后的参考文档字符串（为空则返回空串）
        """
        docs = self.retrieve(query, top_k=top_k)
        if not docs:
            return ""

        return format_docs_as_context(docs)


# ---------------------------------------------------------------------------
# 格式化工具
# ---------------------------------------------------------------------------

def format_docs_as_context(docs: list[Document]) -> str:
    """
    将 Document 列表格式化为结构化上下文字符串

    格式：
    ---
    [来源: xxx] (相似度距离: 0.xx)
    <文档内容>
    ---
    """
    if not docs:
        return ""

    parts: list[str] = ["以下是从知识库中检索到的相关参考资料：\n"]
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "未知来源")
        score = doc.metadata.get("score", None)
        score_str = f" (距离: {score:.4f})" if score is not None else ""

        parts.append(f"--- 参考 {i} [来源: {source}]{score_str} ---")
        parts.append(doc.page_content.strip())
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LangChain Tool（供 Supervisor / Agent 调用）
# ---------------------------------------------------------------------------

@tool
def search_docs(query: str, top_k: int = 5) -> str:
    """
    从知识库中检索与查询最相关的文档片段。

    用途：检索编码规范、历史设计文档、业务概念等非结构化知识。
    不要用于代码搜索（代码搜索请使用专门的代码检索工具）。

    Args:
        query: 自然语言查询，描述你想了解的知识点或问题
        top_k: 返回最相关的文档数量，默认 5

    Returns:
        格式化后的相关文档内容，可直接作为上下文参考
    """
    retriever = DocumentRetriever(top_k=top_k)
    context = retriever.retrieve_as_context(query, top_k=top_k)

    if not context:
        return "知识库中未找到与该查询相关的文档。"

    return context


# ---------------------------------------------------------------------------
# 批量文档导入便捷函数
# ---------------------------------------------------------------------------

def ingest_documents(
    documents: list[Document],
    persist_dir: Optional[str] = None,
) -> int:
    """
    将文档批量导入向量库（切分 → embedding → 入库）

    :param documents: LangChain Document 列表
    :param persist_dir: 持久化目录（首次调用时生效）
    :return: 新增的文档块数
    """
    store = get_vector_store(persist_dir=persist_dir)
    count = store.add_documents(documents)

    # 入库后自动持久化
    store.save()
    logger.info("文档导入完成: 新增 {} 个块，已持久化", count)
    return count


def ingest_text_files(
    file_paths: list[str],
    persist_dir: Optional[str] = None,
) -> int:
    """
    从文件路径列表读取文本并导入向量库

    :param file_paths: 文本文件路径列表
    :param persist_dir: 持久化目录
    :return: 新增的文档块数
    """
    documents: list[Document] = []
    for path in file_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            documents.append(Document(
                page_content=content,
                metadata={"source": path},
            ))
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("读取文件失败，跳过: {} - {}", path, e)

    if not documents:
        logger.warning("无有效文档可导入")
        return 0

    return ingest_documents(documents, persist_dir=persist_dir)
