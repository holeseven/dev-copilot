"""
Memory Retriever - 负责从 Redis ZSET 中检索最近的对话轮次记忆。
# @author lxy
"""

import json
from typing import List, Dict, Any, Optional

from engine.infra.redis_client import get_redis


class MemoryRetriever:
    """从 Redis ZSET 检索会话记忆，支持按时间倒序获取最近 N 轮对话。"""

    def __init__(self):
        self._redis = None

    async def _ensure_redis(self):
        """确保 Redis 连接已初始化。"""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def get_recent(self, session_id: str, last_n: int = 10) -> List[Dict[str, Any]]:
        """
        获取指定会话最近 N 轮对话记录。

        使用 ZREVRANGEBYSCORE 按时间戳倒序取出最近的记录，
        然后反转为时间正序返回。

        Args:
            session_id: 会话唯一标识
            last_n: 需要获取的最近轮次数量，默认 10

        Returns:
            对话记录列表，每条记录格式为:
            {"q": "用户提问", "a": "助手回答", "ts": 时间戳}
            列表按时间正序排列（最早的在前）
        """
        redis = await self._ensure_redis()
        key = f"mem:{session_id}"

        # ZREVRANGEBYSCORE: +inf -> -inf 按分数倒序取，限制数量为 last_n
        raw_members = await redis.zrevrangebyscore(
            key,
            max="+inf",
            min="-inf",
            start=0,
            num=last_n
        )

        if not raw_members:
            return []

        # 反序列化 JSON 并反转为时间正序
        results = []
        for member in reversed(raw_members):
            try:
                if isinstance(member, bytes):
                    member = member.decode("utf-8")
                record = json.loads(member)
                results.append(record)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Failed to deserialize memory record: {e}"
                )
                continue

        return results

    async def get_by_time_range(
        self,
        session_id: str,
        start_ts: float,
        end_ts: float
    ) -> List[Dict[str, Any]]:
        """
        获取指定时间范围内的对话记录。

        Args:
            session_id: 会话唯一标识
            start_ts: 起始时间戳（包含）
            end_ts: 结束时间戳（包含）

        Returns:
            对话记录列表，按时间正序排列
        """
        redis = await self._ensure_redis()
        key = f"mem:{session_id}"

        raw_members = await redis.zrangebyscore(
            key,
            min=start_ts,
            max=end_ts
        )

        if not raw_members:
            return []

        results = []
        for member in raw_members:
            try:
                if isinstance(member, bytes):
                    member = member.decode("utf-8")
                record = json.loads(member)
                results.append(record)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        return results
