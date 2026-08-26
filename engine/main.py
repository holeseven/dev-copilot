# @author lxy
"""
Dev Copilot 引擎 FastAPI 入口

采用 lifespan 生命周期模式：
- 启动时：初始化 Redis 连接池、LLM 客户端、加载动态配置、MCP manager（占位）
- 关闭时：优雅释放上述资源

业务路由（chat/tasks/tools 等）后续由各模块补充后挂载到这里。
"""
from __future__ import annotations

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


def _setup_logging() -> None:
    """配置 loguru 日志输出"""
    settings = get_settings()
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动初始化基础设施，关闭时释放资源"""
    settings = get_settings()
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

    # 4) MCP manager 初始化（占位：后续任务实现三层容错工具链）
    # TODO(后续任务): 初始化 MCP manager，并行连接工具 Server
    logger.info("MCP manager 初始化占位——待后续任务实现")

    logger.info("{} 启动完成", settings.app_name)
    try:
        yield
    finally:
        # 关闭阶段：逆序释放资源
        logger.info("正在关闭 {} ...", settings.app_name)
        await dynamic_config.stop_refresh()
        await close_redis()
        logger.info("{} 已关闭", settings.app_name)


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
    from engine.api import chat, tasks, tools
    app.include_router(chat.router, prefix="/chat", tags=["chat"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(tools.router, prefix="/tools", tags=["tools"])

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
