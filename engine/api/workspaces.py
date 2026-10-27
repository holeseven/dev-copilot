from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.mcp.manager import MCPManager
from engine.core.workspace.models import Repository, RepositoryScan, Workspace


router = APIRouter()


class WorkspaceCreate(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class RepositoryCreate(BaseModel):
    name: str = Field(..., min_length=1)
    local_path: str = Field(..., min_length=1)


class WorkspaceResponse(BaseModel):
    """Public workspace shape; checkpoint generation remains server-internal."""

    id: str
    name: str
    repositories: List[Repository] = Field(default_factory=list)


def resolve_workspace(request: Request, workspace_id: Optional[str]) -> Workspace:
    registry = request.app.state.workspace_registry
    if workspace_id:
        workspace = registry.get(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
        return workspace
    workspace = registry.get_active()
    if workspace is None:
        raise HTTPException(status_code=409, detail="no active workspace")
    return workspace


async def get_workspace_manager(request: Request, workspace: Workspace) -> MCPManager:
    managers = request.app.state.mcp_managers
    manager = managers.get(workspace.id)
    if manager is not None:
        return manager
    async with request.app.state.workspace_manager_lock:
        manager = managers.get(workspace.id)
        if manager is not None:
            return manager
        provider = LocalDevToolProvider(
            "local",
            workspace,
            path_guard=request.app.state.workspace_registry.path_guard,
        )
        manager = MCPManager(
            [{"name": "local", "type": "local", "provider": provider}]
        )
        await manager.init_all()
        managers[workspace.id] = manager
        return manager


async def invalidate_workspace_manager(request: Request, workspace_id: str) -> None:
    async with request.app.state.workspace_manager_lock:
        manager = request.app.state.mcp_managers.pop(workspace_id, None)
    if manager is not None:
        await manager.shutdown()


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(payload: WorkspaceCreate, request: Request) -> Workspace:
    try:
        return request.app.state.workspace_registry.create(payload.id, payload.name)
    except ValueError as exc:
        detail = str(exc)
        code = 409 if "already exists" in detail else 422
        raise HTTPException(status_code=code, detail=detail) from None


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(request: Request) -> List[Workspace]:
    return request.app.state.workspace_registry.list()


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(workspace_id: str, request: Request) -> Workspace:
    workspace = request.app.state.workspace_registry.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    return workspace


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: str, request: Request) -> Response:
    registry = request.app.state.workspace_registry
    if registry.get(workspace_id) is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    registry.delete(workspace_id)
    await invalidate_workspace_manager(request, workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{workspace_id}/repositories",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_repository(
    workspace_id: str, payload: RepositoryCreate, request: Request
) -> Workspace:
    registry = request.app.state.workspace_registry
    if registry.get(workspace_id) is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    try:
        workspace = registry.add_repository(
            workspace_id, payload.name, payload.local_path
        )
    except ValueError as exc:
        detail = str(exc)
        code = 409 if "name already exists" in detail else 422
        raise HTTPException(status_code=code, detail=detail) from None
    await invalidate_workspace_manager(request, workspace_id)
    return workspace


@router.post("/{workspace_id}/scan", response_model=List[RepositoryScan])
async def scan_workspace(
    workspace_id: str, request: Request
) -> List[RepositoryScan]:
    registry = request.app.state.workspace_registry
    workspace = registry.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    scanner = request.app.state.workspace_scanner
    try:
        return [
            scanner.scan(repository)
            for repository in workspace.repositories
        ]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/{workspace_id}/activate", response_model=WorkspaceResponse)
async def activate_workspace(workspace_id: str, request: Request) -> Workspace:
    registry = request.app.state.workspace_registry
    if registry.get(workspace_id) is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    return registry.set_active(workspace_id)
