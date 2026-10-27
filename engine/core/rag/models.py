# @author lxy
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


RagState = Literal["not_indexed", "queued", "building", "ready", "stale", "failed"]


class RagIndexStatus(BaseModel):
    workspace_id: str
    state: RagState
    index_generation: str | None = None
    job_id: str | None = None
    repository_count: int = 0
    file_count: int = 0
    chunk_count: int = 0
    updated_at: str | None = None
    error_code: str | None = None


class RagCitation(BaseModel):
    citation_id: str
    rank: int
    repository: str
    virtual_path: str
    start_line: int
    end_line: int
    excerpt: str
    content_sha256: str


class RagQueryResult(BaseModel):
    workspace_id: str
    index_generation: str
    citations: list[RagCitation] = Field(default_factory=list)


@dataclass(frozen=True)
class SourceChunk:
    repository: str
    path: str
    ordinal: int
    start_line: int
    end_line: int
    symbol: str
    kind: str
    text: str
    content_sha256: str


@dataclass(frozen=True)
class IndexedFile:
    repository: str
    path: str
    sha256: str
    size: int
    chunks: tuple[SourceChunk, ...]
