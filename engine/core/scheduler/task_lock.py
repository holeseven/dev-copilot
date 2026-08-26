# @author lxy
"""
分布式任务锁 —— 基于 Redis SETNX + TTL 的原子抢占

状态机：
    INIT → CLAIMED → RUNNING → COMPLETED / FAILED

设计要点：
- claim: SETNX + PEXPIRE 原子操作（Lua script），保证只有一个 worker 抢到
- release: 仅持有者可释放（比对 owner 值）
- renew: 续期（心跳），防止长任务期间锁自动过期被其他 worker 抢走
"""
from __future__ import annotations

import enum
import time
import uuid
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from engine.infra.redis_client import get_redis

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
_LOCK_PREFIX = "scheduler:lock:"
_STATE_PREFIX = "scheduler:state:"
_DEFAULT_LOCK_TTL_MS = 120_000  # 锁默认存活 120s（心跳 60s 续期一次）


class TaskState(str, enum.Enum):
    """任务状态机枚举"""
    INIT = "INIT"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ──────────────────────────────────────────────
# Lua 脚本：原子 SETNX + PEXPIRE
# ──────────────────────────────────────────────
_CLAIM_SCRIPT = """
local key = KEYS[1]
local owner = ARGV[1]
local ttl = tonumber(ARGV[2])
if redis.call("EXISTS", key) == 0 then
    redis.call("SET", key, owner)
    redis.call("PEXPIRE", key, ttl)
    return 1
end
return 0
"""

# Lua 脚本：仅持有者释放
_RELEASE_SCRIPT = """
local key = KEYS[1]
local owner = ARGV[1]
if redis.call("GET", key) == owner then
    redis.call("DEL", key)
    return 1
end
return 0
"""

# Lua 脚本：仅持有者续期
_RENEW_SCRIPT = """
local key = KEYS[1]
local owner = ARGV[1]
local ttl = tonumber(ARGV[2])
if redis.call("GET", key) == owner then
    redis.call("PEXPIRE", key, ttl)
    return 1
end
return 0
"""


class TaskLock:
    """
    单个任务的分布式锁句柄

    使用示例::

        lock = TaskLock(task_id="task-001")
        if await lock.claim():
            try:
                await lock.transition(TaskState.RUNNING)
                # ... 执行任务 ...
                await lock.transition(TaskState.COMPLETED)
            finally:
                await lock.release()
    """

    def __init__(
        self,
        task_id: str,
        owner: Optional[str] = None,
        ttl_ms: int = _DEFAULT_LOCK_TTL_MS,
    ) -> None:
        self.task_id = task_id
        self.owner = owner or f"{uuid.uuid4().hex[:12]}_{int(time.time())}"
        self.ttl_ms = ttl_ms
        self._lock_key = f"{_LOCK_PREFIX}{task_id}"
        self._state_key = f"{_STATE_PREFIX}{task_id}"

    # ──────────── 核心操作 ────────────

    async def claim(self) -> bool:
        """
        尝试抢占任务锁（CAS 语义）。

        Returns:
            True  — 抢占成功，当前 worker 成为持有者
            False — 锁已被其他 worker 持有
        """
        redis: aioredis.Redis = get_redis()
        result = await redis.eval(
            _CLAIM_SCRIPT, 1, self._lock_key, self.owner, str(self.ttl_ms)
        )
        if result == 1:
            # 抢占成功，初始化状态为 CLAIMED
            await redis.set(self._state_key, TaskState.CLAIMED.value)
            logger.info(
                "[TaskLock] 抢占成功 task_id={} owner={}", self.task_id, self.owner
            )
            return True
        logger.debug("[TaskLock] 抢占失败（已被占） task_id={}", self.task_id)
        return False

    async def release(self) -> bool:
        """
        释放锁（仅持有者可释放）。

        Returns:
            True — 释放成功; False — 非持有者或锁已过期
        """
        redis: aioredis.Redis = get_redis()
        result = await redis.eval(
            _RELEASE_SCRIPT, 1, self._lock_key, self.owner
        )
        if result == 1:
            logger.info("[TaskLock] 释放成功 task_id={}", self.task_id)
            return True
        logger.warning(
            "[TaskLock] 释放失败（非持有者或锁已过期） task_id={}", self.task_id
        )
        return False

    async def renew(self) -> bool:
        """
        续期锁 TTL（心跳场景调用）。

        Returns:
            True — 续期成功; False — 非持有者或锁已过期
        """
        redis: aioredis.Redis = get_redis()
        result = await redis.eval(
            _RENEW_SCRIPT, 1, self._lock_key, self.owner, str(self.ttl_ms)
        )
        if result == 1:
            logger.debug("[TaskLock] 续期成功 task_id={}", self.task_id)
            return True
        logger.warning("[TaskLock] 续期失败 task_id={}", self.task_id)
        return False

    # ──────────── 状态机流转 ────────────

    _VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
        TaskState.INIT: {TaskState.CLAIMED},
        TaskState.CLAIMED: {TaskState.RUNNING, TaskState.FAILED},
        TaskState.RUNNING: {TaskState.COMPLETED, TaskState.FAILED},
        # 终态不可再迁移
        TaskState.COMPLETED: set(),
        TaskState.FAILED: set(),
    }

    async def transition(self, target: TaskState) -> None:
        """
        流转任务状态（合法性校验 + Redis 持久化）。

        Raises:
            ValueError — 非法流转
        """
        redis: aioredis.Redis = get_redis()
        current_raw = await redis.get(self._state_key)
        current = TaskState(current_raw) if current_raw else TaskState.INIT

        if target not in self._VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"非法状态流转: {current.value} → {target.value} (task_id={self.task_id})"
            )
        await redis.set(self._state_key, target.value)
        logger.info(
            "[TaskLock] 状态流转 {} → {} task_id={}",
            current.value,
            target.value,
            self.task_id,
        )

    async def get_state(self) -> TaskState:
        """查询当前任务状态"""
        redis: aioredis.Redis = get_redis()
        raw = await redis.get(self._state_key)
        return TaskState(raw) if raw else TaskState.INIT
