# @author lxy
"""
RAG 知识检索模块

对外暴露：
- get_vector_store: 获取 FAISS 向量存储实例
- search_docs: LangChain Tool，可被 Supervisor/Agent 直接调用
- ingest_documents / ingest_text_files: 批量文档导入
- DocumentRetriever: 检索器类（需要更精细控制时使用）
"""
from engine.core.rag.vector_store import get_vector_store, FAISSVectorStore  # noqa: F401
from engine.core.rag.retriever import (  # noqa: F401
    search_docs,
    ingest_documents,
    ingest_text_files,
    DocumentRetriever,
)

__all__ = [
    "get_vector_store",
    "FAISSVectorStore",
    "search_docs",
    "ingest_documents",
    "ingest_text_files",
    "DocumentRetriever",
]
