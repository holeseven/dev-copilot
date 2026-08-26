# @author lxy
"""
动态配置热更新（代替阿里 Diamond）

用 Redis Hash 存放运行时配置（Agent 列表 / 路由模型 / 各类阈值 / memoryRounds 等），
应用侧维护一份本地缓存，通过后台定时任务轮询刷新，实现「改配置不重启即生效」。

对标 Diamond：本质是「配置中心 + 本地缓存 + 变更推送」，这里用 Redis Hash + 轮询简化实现。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from engine.config.settings import get_settings
from engine.infra.redis_client import get_redis


class DynamicConfig:
    """基于 Redis Hash 的动态配置管理器（单例使用）"""

    def __init__(self) -> None:
        settings = get_settings()
        self._hash_key = settings.dynamic_config_key
        self._refresh_interval = settings.config_refresh_interval
        # 本地缓存：field -> 反序列化后的值
        self._cache: dict[str, Any] = {}
        # 后台刷新任务句柄
        self._refresh_task: asyncio.Task | None = None

    async def load_all(self) -> None:
        """从 Redis 全量拉取配置到本地缓存"""
        redis = get_redis()
        raw = await redis.hgetall(self._hash_key)
        cache: dict[str, Any] = {}
        for field, value in raw.items():
            cache[field] = self._deserialize(value)
        self._cache = cache
        logger.info("动态配置已加载: {} 项", len(cache))

    def get_config(self, key: str, default: Any = None) -> Any:
        """从本地缓存读取配置项（不触发网络请求，纳秒级）"""
        return self._cache.get(key, default)

    async def set_config(self, key: str, value: Any) -> None:
        """写入配置项到 Redis 并同步本地缓存"""
        redis = get_redis()
        await redis.hset(self._hash_key, key, self._serialize(value))
        self._cache[key] = value
        logger.info("动态配置已更新: {}={}", key, value)

    async def refresh_loop(self) -> None:
        """后台轮询循环：定时全量刷新本地缓存（应用启动时以 create_task 拉起）"""
        logger.info("动态配置刷新循环启动，间隔 {}s", self._refresh_interval)
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await self.load_all()
            except asyncio.CancelledError:
                logger.info("动态配置刷新循环已停止")
                break
            except Exception as exc:  # noqa: BLE001 - 刷新失败不应中断循环
                logger.warning("动态配置刷新失败，保留旧缓存: {}", exc)

    def start_refresh(self) -> None:
        """启动后台刷新任务"""
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self.refresh_loop())

    async def stop_refresh(self) -> None:
        """停止后台刷新任务"""
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None

    @staticmethod
    def _serialize(value: Any) -> str:
        """统一以 JSON 存储，兼容 dict/list/数字/字符串"""
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _deserialize(value: str) -> Any:
        """尝试 JSON 反序列化，失败则原样返回字符串"""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value


# 进程内单例
_dynamic_config: DynamicConfig | None = None


def get_dynamic_config() -> DynamicConfig:
    """获取动态配置单例"""
    global _dynamic_config
    if _dynamic_config is None:
        _dynamic_config = DynamicConfig()
    return _dynamic_config
