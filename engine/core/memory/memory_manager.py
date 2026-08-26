"""
Memory Manager - 负责将对话轮次异步写入 Redis ZSET 进行持久化存储。
# @author lxy
"""

import json
import time
import asyncio
from typing import Optional

from engine.infra.redis_client import get_redis


class MemoryManager:
    """管理会话记忆的写入，使用 Redis ZSET 按时间戳排序存储对话轮次。"""

    def __init__(self):
        self._redis = None

    async def _ensure_redis(self):
        """确保 Redis 连接已初始化。"""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def add_turn(self, session_id: str, question: str, answer: str) -> None:
        """
        异步写入一轮对话到 Redis ZSET。

        使用 asyncio.create_task 将写入操作放入后台执行，不阻塞主流程。

        Args:
            session_id: 会话唯一标识
            question: 用户提问内容
            answer: 助手回答内容
        """
        asyncio.create_task(self._write_turn(session_id, question, answer))

    async def _write_turn(self, session_id: str, question: str, answer: str) -> None:
        """
        实际执行 Redis ZSET 写入的内部方法。

        Key 格式: mem:{session_id}
        Member: JSON 序列化的对话记录 {"q": ..., "a": ..., "ts": ...}
        Score: Unix 时间戳（用于按时间排序）

        Args:
            session_id: 会话唯一标识
            question: 用户提问内容
            answer: 助手回答内容
        """
        try:
            redis = await self._ensure_redis()
            key = f"mem:{session_id}"
            ts = time.time()
            member = json.dumps({
                "q": question,
                "a": answer,
                "ts": ts
            }, ensure_ascii=False)
            await redis.zadd(key, {member: ts})
        except Exception as e:
            # 记忆写入失败不应影响主流程，仅记录日志
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to write memory turn for session {session_id}: {e}"
            )

    async def clear_session(self, session_id: str) -> None:
        """
        清除指定会话的所有记忆记录。

        Args:
            session_id: 会话唯一标识
        """
        redis = await self._ensure_redis()
        key = f"mem:{session_id}"
        await redis.delete(key)

    async def get_turn_count(self, session_id: str) -> int:
        """
        获取指定会话的对话轮次总数。

        Args:
            session_id: 会话唯一标识

        Returns:
            对话轮次数量
        """
        redis = await self._ensure_redis()
        key = f"mem:{session_id}"
        return await redis.zcard(key)
