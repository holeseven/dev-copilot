# @author lxy
"""
RAG 向量存储模块

职责：
- 非结构化知识文档（编码规范 / 历史设计文档 / 业务概念）的切分、向量化与持久存储
- 基于 FAISS 实现高效近似最近邻检索
- 不处理代码检索（代码检索由 agentic search 负责）

设计要点：
1. 文档切分使用 RecursiveCharacterTextSplitter，对中英文混合文档友好
2. Embedding 复用 OpenAI 兼容协议（与 LLM 共用 base_url / api_key）
3. 向量库支持增量添加文档 & 本地磁盘持久化
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import faiss
import numpy as np
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from engine.config.settings import get_settings


# ---------------------------------------------------------------------------
# Embedding 工厂
# ---------------------------------------------------------------------------

_embeddings_instance: Optional[OpenAIEmbeddings] = None


def get_embeddings() -> OpenAIEmbeddings:
    """
    获取全局 Embedding 实例（单例）

    优先读取 EMBEDDING_* 环境变量，未配置则回落到 LLM 配置。
    """
    global _embeddings_instance
    if _embeddings_instance is not None:
        return _embeddings_instance

    settings = get_settings()
    # 允许 embedding 使用独立端点（如 text-embedding-3-small），否则复用 LLM 配置
    api_key = os.getenv("EMBEDDING_API_KEY", settings.llm_api_key)
    base_url = os.getenv("EMBEDDING_BASE_URL", settings.llm_base_url)
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    _embeddings_instance = OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=settings.llm_timeout,
    )
    logger.info("Embedding 实例已创建: model={} base_url={}", model, base_url)
    return _embeddings_instance


# ---------------------------------------------------------------------------
# 文档切分器
# ---------------------------------------------------------------------------

def build_text_splitter(
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> RecursiveCharacterTextSplitter:
    """
    构建递归字符文本切分器

    :param chunk_size: 每个文本块的最大字符数
    :param chunk_overlap: 相邻块之间的重叠字符数（保证语义连续）
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ".", " ", ""],
        length_function=len,
    )


# ---------------------------------------------------------------------------
# FAISS 向量存储类
# ---------------------------------------------------------------------------

class FAISSVectorStore:
    """
    基于 FAISS 的内存向量存储

    功能：
    - add_documents: 切分 → embedding → 写入索引
    - search: query embedding → L2 距离召回 top-k
    - save / load: 本地磁盘持久化 & 恢复
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ):
        """
        :param persist_dir: 索引持久化目录，为 None 则仅内存
        :param chunk_size: 文本切分块大小
        :param chunk_overlap: 文本切分重叠大小
        """
        self._embeddings = get_embeddings()
        self._splitter = build_text_splitter(chunk_size, chunk_overlap)
        self._persist_dir = Path(persist_dir) if persist_dir else None

        # FAISS 索引（延迟初始化，首次 add 时根据维度构建）
        self._index: Optional[faiss.IndexFlatL2] = None
        # 存储与向量一一对应的文档块
        self._documents: list[Document] = []
        # embedding 维度（首次 embed 后确定）
        self._dimension: Optional[int] = None

        # 尝试从磁盘恢复
        if self._persist_dir:
            self._try_load()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def add_documents(self, documents: list[Document]) -> int:
        """
        添加文档到向量库

        流程：文档 → 切分为 chunks → 批量 embedding → 写入 FAISS 索引
        :param documents: 原始 LangChain Document 列表
        :return: 实际新增的 chunk 数量
        """
        # 1. 切分
        chunks = self._splitter.split_documents(documents)
        if not chunks:
            logger.warning("文档切分后无有效块，跳过")
            return 0

        # 2. 批量 embedding
        texts = [chunk.page_content for chunk in chunks]
        vectors = self._embeddings.embed_documents(texts)
        vectors_np = np.array(vectors, dtype=np.float32)

        # 3. 初始化索引（首次）
        if self._index is None:
            self._dimension = vectors_np.shape[1]
            self._index = faiss.IndexFlatL2(self._dimension)
            logger.info("FAISS 索引已初始化: dimension={}", self._dimension)

        # 4. 写入索引
        self._index.add(vectors_np)
        self._documents.extend(chunks)

        logger.info(
            "已添加 {} 个文档块到向量库（总计 {} 块）",
            len(chunks),
            len(self._documents),
        )
        return len(chunks)

    def add_texts(self, texts: list[str], metadatas: Optional[list[dict]] = None) -> int:
        """
        便捷方法：直接添加文本列表

        :param texts: 纯文本列表
        :param metadatas: 每条文本对应的元数据
        :return: 新增 chunk 数
        """
        metadatas = metadatas or [{}] * len(texts)
        docs = [
            Document(page_content=text, metadata=meta)
            for text, meta in zip(texts, metadatas)
        ]
        return self.add_documents(docs)

    def search(self, query: str, top_k: int = 5) -> list[Document]:
        """
        语义检索

        :param query: 用户查询文本
        :param top_k: 返回最相似的前 k 个文档块
        :return: 按相似度降序排列的 Document 列表（metadata 中附加 score）
        """
        if self._index is None or self._index.ntotal == 0:
            logger.warning("向量库为空，无法检索")
            return []

        # query embedding
        query_vector = self._embeddings.embed_query(query)
        query_np = np.array([query_vector], dtype=np.float32)

        # FAISS 检索
        k = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(query_np, k)

        results: list[Document] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            doc = self._documents[idx]
            # 将距离转换附加到 metadata（距离越小越相似）
            doc_copy = Document(
                page_content=doc.page_content,
                metadata={**doc.metadata, "score": float(dist)},
            )
            results.append(doc_copy)

        return results

    @property
    def total_chunks(self) -> int:
        """当前索引中的文档块总数"""
        return self._index.ntotal if self._index else 0

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self) -> None:
        """将索引与文档数据持久化到磁盘"""
        if not self._persist_dir or self._index is None:
            return

        self._persist_dir.mkdir(parents=True, exist_ok=True)
        index_path = self._persist_dir / "index.faiss"
        docs_path = self._persist_dir / "documents.npy"

        faiss.write_index(self._index, str(index_path))
        # 序列化文档（使用 numpy 对象数组简化存储）
        np.save(str(docs_path), np.array(self._documents, dtype=object))
        logger.info("向量库已持久化: {}", self._persist_dir)

    def _try_load(self) -> None:
        """尝试从磁盘恢复索引"""
        if not self._persist_dir:
            return

        index_path = self._persist_dir / "index.faiss"
        docs_path = self._persist_dir / "documents.npy"

        if index_path.exists() and docs_path.exists():
            self._index = faiss.read_index(str(index_path))
            self._documents = list(np.load(str(docs_path), allow_pickle=True))
            self._dimension = self._index.d
            logger.info(
                "从磁盘恢复向量库: {} 个文档块, dimension={}",
                self._index.ntotal,
                self._dimension,
            )


# ---------------------------------------------------------------------------
# 模块级便捷实例
# ---------------------------------------------------------------------------

_default_store: Optional[FAISSVectorStore] = None


def get_vector_store(persist_dir: Optional[str] = None) -> FAISSVectorStore:
    """
    获取默认向量存储实例（单例）

    :param persist_dir: 首次调用时设置持久化目录
    """
    global _default_store
    if _default_store is None:
        _default_store = FAISSVectorStore(persist_dir=persist_dir)
    return _default_store
