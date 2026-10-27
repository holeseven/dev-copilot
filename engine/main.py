# @author lxy
"""
Dev Copilot 引擎 FastAPI 入口

采用 lifespan 生命周期模式：
- 启动时：初始化 Redis、LLM、动态配置与共享 Workspace 运行时
- 关闭时：释放全部 Workspace manager 和基础设施资源

业务路由（chat/tasks/tools 等）后续由各模块补充后挂载到这里。
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from loguru import logger

from engine.config.settings import get_settings
from engine.infra.dynamic_config import get_dynamic_config
from engine.infra.llm_client import Complexity, get_llm_by_complexity
from engine.infra.redis_client import close_redis, init_redis
from engine.infra.checkpoint import open_sqlite_checkpointer
from engine.core.dispatcher.dispatcher import get_agent_dispatcher
from engine.core.hitl.coordinator import ApprovalCoordinator
from engine.core.workspace.registry import WorkspaceRegistry
from engine.core.workspace.scanner import WorkspaceScanner
from engine.core.workspace.proposal_store import SqliteProposalStore
from engine.core.rag.service import RagService
from engine.core.rag.store import RagStore
from engine.orchestrator.graph_builder import build_agent_graph


def _setup_logging() -> None:
    """配置 loguru 日志输出"""
    settings = get_settings()
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    async with open_sqlite_checkpointer(
        settings.resolved_checkpoint_db_path
    ) as checkpointer:
        async with _runtime_lifespan(app, settings, checkpointer):
            yield


@asynccontextmanager
async def _runtime_lifespan(app: FastAPI, settings, checkpointer):
    """应用生命周期：启动初始化基础设施，关闭时释放资源"""
    _setup_logging()
    logger.info("正在启动 {} ...", settings.app_name)

    # 1) 初始化 Redis 连接池
    await init_redis()

    # 2) 预热 LLM 客户端（触发实例缓存创建，暴露配置问题）
    get_llm_by_complexity(Complexity.L1)

    # 3) 加载动态配置并启动后台热更新
    dynamic_config = get_dynamic_config()
    await dynamic_config.load_all()
    dynamic_config.start_refresh()

    # 4) 初始化 Workspace 运行时；manager 按 workspace 惰性创建
    app.state.workspace_registry = WorkspaceRegistry(
        root=settings.workspace_metadata_path,
        allowed_roots=settings.workspace_allowed_root_paths,
    )
    app.state.workspace_scanner = WorkspaceScanner(
        path_guard=app.state.workspace_registry.path_guard
    )
    app.state.proposal_store = SqliteProposalStore(settings.resolved_proposal_db_path)
    recovered_outcomes = []
    for workspace in app.state.workspace_registry.list():
        recovered_outcomes.extend(
            app.state.proposal_store.reconcile_applying(
                workspace, app.state.workspace_registry.path_guard
            )
        )
    if recovered_outcomes:
        logger.warning(
            "Reconciled interrupted approval outcomes | count={}",
            len(recovered_outcomes),
        )
    app.state.rag_service = RagService(
        RagStore(settings.resolved_rag_db_path),
        app.state.workspace_registry.path_guard,
    )
    app.state.agent_graph = build_agent_graph(checkpointer=checkpointer)
    app.state.approval_coordinator = ApprovalCoordinator()
    app.state.approval_runs = {}
    app.state.approval_runs_lock = asyncio.Lock()
    app.state.dispatcher = get_agent_dispatcher()
    app.state.mcp_managers = {}
    app.state.workspace_manager_lock = asyncio.Lock()

    logger.info("{} 启动完成", settings.app_name)
    body_error: Exception | None = None
    try:
        yield
    except Exception as exc:
        body_error = exc
    finally:
        # 关闭阶段：逆序释放资源
        logger.info("正在关闭 {} ...", settings.app_name)
        approval_tasks = [
            run.task
            for run in app.state.approval_runs.values()
            if run.task is not None and not run.task.done()
        ]
        for task in approval_tasks:
            task.cancel()
        if approval_tasks:
            await asyncio.gather(*approval_tasks, return_exceptions=True)
        app.state.approval_runs.clear()
        async with app.state.workspace_manager_lock:
            managers = list(app.state.mcp_managers.items())
            app.state.mcp_managers.clear()
        cleanup_errors: list[Exception] = []
        for workspace_id, manager in managers:
            try:
                await manager.shutdown()
            except Exception as exc:
                logger.exception(
                    "Workspace {} runtime cleanup failed", workspace_id
                )
                cleanup_errors.append(exc)
        try:
            await app.state.rag_service.shutdown()
        except Exception as exc:
            logger.exception("RAG service cleanup failed")
            cleanup_errors.append(exc)
        try:
            await dynamic_config.stop_refresh()
        except Exception as exc:
            logger.exception("Dynamic configuration cleanup failed")
            cleanup_errors.append(exc)
        try:
            await close_redis()
        except Exception as exc:
            logger.exception("Redis cleanup failed")
            cleanup_errors.append(exc)
        if not cleanup_errors:
            logger.info("{} 已关闭", settings.app_name)

    primary_error = (
        body_error
        if body_error is not None
        else (cleanup_errors[0] if cleanup_errors else None)
    )
    if primary_error is not None:
        additional_errors = (
            cleanup_errors if body_error is not None else cleanup_errors[1:]
        )
        notes = [
            f"Additional cleanup failure: {error!r}"
            for error in additional_errors
        ]
        if hasattr(primary_error, "add_note"):
            for note in notes:
                primary_error.add_note(note)
        elif notes:
            primary_error.__notes__ = [
                *getattr(primary_error, "__notes__", []),
                *notes,
            ]
        raise primary_error


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    settings = get_settings()
    app = FastAPI(
        title="Dev Copilot Engine",
        description="AI 研发助手（Dev Copilot）引擎 —— OpenSpec 四阶段流程 + 多 Agent 协作",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        """健康检查：用于容器探活 / 负载均衡"""
        return {"status": "ok", "app": settings.app_name}

    # ===== 业务路由挂载 =====
    from engine.api import chat, rag, tasks, tools, workspaces
    app.include_router(chat.router, prefix="/chat", tags=["chat"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(tools.router, prefix="/tools", tags=["tools"])
    app.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
    app.include_router(rag.router, prefix="/workspaces", tags=["rag"])

    # ===== 静态前端首页 =====
    _frontend_path = Path(__file__).parent / "frontend" / "index.html"

    @app.get("/", tags=["frontend"], response_class=HTMLResponse)
    async def serve_frontend():
        """提供单文件前端聊天界面"""
        return HTMLResponse(content=_frontend_path.read_text(encoding="utf-8"))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "engine.main:app",
        host=_settings.app_host,
        port=_settings.app_port,
        reload=True,
    )
