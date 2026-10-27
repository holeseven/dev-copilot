# @author lxy
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from engine.api.workspaces import resolve_workspace
from engine.core.rag.models import RagIndexStatus, RagQueryResult


router = APIRouter()


class RagQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=4000)
    top_k: Annotated[StrictInt, Field(ge=1, le=20)] = 8


@router.get("/{workspace_id}/rag/index", response_model=RagIndexStatus)
async def rag_index_status(workspace_id: str, request: Request):
    workspace = resolve_workspace(request, workspace_id)
    return request.app.state.rag_service.status(workspace)


@router.post("/{workspace_id}/rag/index/rebuild", response_model=RagIndexStatus, status_code=status.HTTP_202_ACCEPTED)
async def rebuild_rag_index(workspace_id: str, request: Request):
    workspace = resolve_workspace(request, workspace_id)
    try:
        return request.app.state.rag_service.start_rebuild(workspace)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/{workspace_id}/rag/query", response_model=RagQueryResult)
async def query_rag_index(workspace_id: str, payload: RagQueryRequest, request: Request):
    workspace = resolve_workspace(request, workspace_id)
    try:
        return request.app.state.rag_service.query(workspace, payload.query, payload.top_k)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
