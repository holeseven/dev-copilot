# @author lxy
"""
Tools API 路由 —— MCP 工具管理接口

- GET /tools：列出当前已注册的所有 MCP 工具
- POST /tools/refresh：触发 MCP 自愈刷新（重连失败 Server + 增量更新工具列表）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from engine.core.mcp.manager import MCPManager

router = APIRouter()

# 模块级 MCPManager 引用（由 lifespan 初始化后注入）
_mcp_manager: Optional[MCPManager] = None


def set_mcp_manager(manager: MCPManager) -> None:
    """由 main.py lifespan 调用，注入初始化好的 MCPManager 实例"""
    global _mcp_manager
    _mcp_manager = manager


def _get_manager() -> MCPManager:
    """获取 MCPManager 实例，未初始化则返回 503"""
    if _mcp_manager is None:
        raise HTTPException(
            status_code=503,
            detail="MCP Manager 尚未初始化，请稍后重试",
        )
    return _mcp_manager


# ─── 响应模型 ─────────────────────────────────────────────────────────────────

class ToolInfo(BaseModel):
    """单个工具信息"""
    name: str
    description: str = ""
    parameters: Dict[str, Any] = {}


class ToolListResponse(BaseModel):
    """工具列表响应"""
    total: int
    tools: List[ToolInfo]


class RefreshResponse(BaseModel):
    """刷新响应"""
    success: bool
    message: str
    reconnected: List[str] = []
    still_failed: List[str] = []


# ─── GET /tools ───────────────────────────────────────────────────────────────

@router.get("", response_model=ToolListResponse)
async def list_tools():
    """
    列出当前 MCP 已注册的所有工具。

    返回工具名称、描述及参数 schema。
    """
    manager = _get_manager()
    schemas = manager.get_tool_schemas()

    tools = []
    for schema in schemas:
        tools.append(ToolInfo(
            name=schema.get("name", "unknown"),
            description=schema.get("description", ""),
            parameters=schema.get("parameters", schema.get("input_schema", {})),
        ))

    logger.debug("列出工具 | total={}", len(tools))

    return ToolListResponse(total=len(tools), tools=tools)


# ─── POST /tools/refresh ──────────────────────────────────────────────────────

@router.post("/refresh", response_model=RefreshResponse)
async def refresh_tools():
    """
    触发 MCP 自愈刷新：重连失败的 Server 并增量更新工具注册表。

    该操作是幂等的，可以安全重复调用。
    """
    manager = _get_manager()

    failed_before = list(manager._failed_servers)
    logger.info("触发 MCP 刷新 | 当前失败 Server: {}", failed_before)

    try:
        # 重新初始化所有 Server（init_all 是幂等的）
        results = await manager.init_all()

        reconnected = [
            name for name, status in results.items()
            if status == "ok" and name in failed_before
        ]
        still_failed = list(manager._failed_servers)

        message = f"刷新完成：重连 {len(reconnected)} 个，仍失败 {len(still_failed)} 个"
        logger.info(message)

        return RefreshResponse(
            success=True,
            message=message,
            reconnected=reconnected,
            still_failed=still_failed,
        )

    except Exception as exc:
        logger.opt(exception=True).error("MCP 刷新异常: {}", str(exc))
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(exc)}")
