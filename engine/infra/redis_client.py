# @author lxy
"""
Redis 异步连接池封装

使用 redis.asyncio 提供全局共享的连接池，避免每次请求新建连接。
生命周期：应用启动时 init_redis() 初始化，关闭时 close_redis() 释放。
"""
from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger

from engine.config.settings import get_settings

# 全局连接池与客户端（进程内单例）
_pool: aioredis.ConnectionPool | None = None
_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """初始化 Redis 连接池并返回客户端（应用启动时调用）"""
    global _pool, _client
    if _client is not None:
        return _client

    settings = get_settings()
    # 从 URL 创建连接池，decode_responses=True 让返回值自动解码为 str
    _pool = aioredis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        decode_responses=True,
    )
    _client = aioredis.Redis(connection_pool=_pool)

    # 启动即探活，尽早暴露配置错误
    try:
        await _client.ping()
        logger.info("Redis 连接池初始化成功: {}", settings.redis_url)
    except Exception as exc:  # noqa: BLE001 - 启动期需捕获所有异常并给出清晰提示
        logger.error("Redis 连接失败: {}", exc)
        raise

    return _client


def get_redis() -> aioredis.Redis:
    """获取已初始化的 Redis 客户端（未初始化则抛错）"""
    if _client is None:
        raise RuntimeError("Redis 尚未初始化，请先调用 init_redis()")
    return _client


async def close_redis() -> None:
    """释放连接池（应用关闭时调用）"""
    global _pool, _client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
    logger.info("Redis 连接池已关闭")
