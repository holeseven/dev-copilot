from __future__ import annotations

from typing import List
from uuid import uuid4

from pydantic import BaseModel, Field


class Repository(BaseModel):
    """A source repository registered with a workspace."""

    name: str
    path: str


class Workspace(BaseModel):
    """A named collection of source repositories."""

    id: str
    name: str
    generation: str = Field(default_factory=lambda: uuid4().hex)
    repositories: List[Repository] = Field(default_factory=list)


class ScanEvidence(BaseModel):
    """The manifest or configuration entry that produced a scan fact."""

    source: str
    detail: str


class DependencyRelation(BaseModel):
    """An explicit dependency relation found in a supported project file."""

    source: str
    target: str
    evidence: ScanEvidence


class GitInfo(BaseModel):
    """Git metadata read from the repository's local control directory."""

    branch: str = ""
    remote: str = ""


class RepositoryScan(BaseModel):
    """Deterministic facts collected from one registered repository."""

    repository: str
    git: GitInfo = Field(default_factory=GitInfo)
    languages: List[str] = Field(default_factory=list)
    build_systems: List[str] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)
    middleware: List[str] = Field(default_factory=list)
    dependencies: List[DependencyRelation] = Field(default_factory=list)
    evidence: List[ScanEvidence] = Field(default_factory=list)
