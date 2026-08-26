# @author lxy
"""
拉模式任务队列 —— 基于 Redis List 的 pending 队列 + CAS 抢占

核心流程：
    1. submit(task_id) → RPUSH 到 pending list
    2. poll_and_claim(worker_id) → LPOP pending → TaskLock.claim() 抢占
    3. heartbeat(task_id) → asyncio.Task 每 60s 续期锁
    4. complete/fail(task_id) → 状态机 RUNNING → COMPLETED/FAILED 并释放锁

依赖：
    - engine.core.scheduler.task_lock: TaskLock / TaskState
    - engine.infra.redis_client: get_redis()
"""
from __future__ import annotations

import asyncio
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from engine.core.scheduler.task_lock import TaskLock, TaskState
from engine.infra.redis_client import get_redis

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
_PENDING_QUEUE_KEY = "scheduler:queue:pending"
_HEARTBEAT_INTERVAL_S = 60  # 心跳续期间隔（秒）


class TaskQueue:
    """
    拉模式任务队列

    Worker 主动 poll → CAS 抢占 → 执行 → complete/fail。
    内部维护每个运行中任务的心跳协程，防止锁超时被夺。
    """

    def __init__(self) -> None:
        # task_id → (TaskLock, heartbeat asyncio.Task)
        self._active_locks: dict[str, tuple[TaskLock, asyncio.Task]] = {}

    # ──────────── 提交任务 ────────────

    async def submit(self, task_id: str) -> None:
        """
        将任务 id 推入 Redis pending 队列尾部（RPUSH）。

        Args:
            task_id: 全局唯一的任务标识
        """
        redis: aioredis.Redis = get_redis()
        await redis.rpush(_PENDING_QUEUE_KEY, task_id)
        logger.info("[TaskQueue] 任务已提交 task_id={}", task_id)

    # ──────────── 拉取并抢占 ────────────

    async def poll_and_claim(self, worker_id: str) -> Optional[str]:
        """
        从 pending 队列头部取出一个任务，尝试 CAS 抢占。

        流程：LPOP → TaskLock.claim()
        - 抢到：流转到 RUNNING，启动心跳，返回 task_id
        - 抢不到（被其他 worker 先占）：跳过，返回 None

        Args:
            worker_id: 当前 worker 标识（作为锁 owner）

        Returns:
            抢到的 task_id，或 None
        """
        redis: aioredis.Redis = get_redis()
        task_id: Optional[str] = await redis.lpop(_PENDING_QUEUE_KEY)
        if task_id is None:
            return None

        lock = TaskLock(task_id=task_id, owner=worker_id)
        claimed = await lock.claim()
        if not claimed:
            logger.debug(
                "[TaskQueue] 抢占失败，跳过 task_id={} worker={}",
                task_id,
                worker_id,
            )
            return None

        # 抢占成功 → 流转到 RUNNING
        await lock.transition(TaskState.RUNNING)

        # 启动心跳续期协程
        hb_task = asyncio.create_task(
            self._heartbeat_loop(lock), name=f"hb-{task_id}"
        )
        self._active_locks[task_id] = (lock, hb_task)

        logger.info(
            "[TaskQueue] 抢占成功并启动心跳 task_id={} worker={}",
            task_id,
            worker_id,
        )
        return task_id

    # ──────────── 心跳续期 ────────────

    async def heartbeat(self, task_id: str) -> bool:
        """
        手动触发一次心跳续期（也可依赖内部自动心跳）。

        Returns:
            True — 续期成功; False — 任务不在本队列或续期失败
        """
        entry = self._active_locks.get(task_id)
        if entry is None:
            logger.warning("[TaskQueue] 心跳失败：任务不在本地活跃列表 task_id={}", task_id)
            return False
        lock, _ = entry
        return await lock.renew()

    async def _heartbeat_loop(self, lock: TaskLock) -> None:
        """后台协程：每 60s 续期一次锁 TTL，直到被取消"""
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                success = await lock.renew()
                if not success:
                    logger.warning(
                        "[TaskQueue] 心跳续期失败，停止循环 task_id={}",
                        lock.task_id,
                    )
                    break
        except asyncio.CancelledError:
            logger.debug("[TaskQueue] 心跳协程被取消 task_id={}", lock.task_id)

    # ──────────── 完成 / 失败 ────────────

    async def complete(self, task_id: str) -> None:
        """
        标记任务完成：RUNNING → COMPLETED，释放锁，停止心跳。

        Raises:
            ValueError: 非法状态流转
            KeyError: task_id 不在本地活跃列表
        """
        await self._finalize(task_id, TaskState.COMPLETED)
        logger.info("[TaskQueue] 任务完成 task_id={}", task_id)

    async def fail(self, task_id: str) -> None:
        """
        标记任务失败：RUNNING → FAILED，释放锁，停止心跳。

        Raises:
            ValueError: 非法状态流转
            KeyError: task_id 不在本地活跃列表
        """
        await self._finalize(task_id, TaskState.FAILED)
        logger.info("[TaskQueue] 任务失败 task_id={}", task_id)

    async def _finalize(self, task_id: str, target_state: TaskState) -> None:
        """内部统一收尾逻辑：状态流转 → 释放锁 → 取消心跳"""
        entry = self._active_locks.pop(task_id, None)
        if entry is None:
            raise KeyError(
                f"task_id={task_id} 不在本地活跃列表，无法 finalize"
            )

        lock, hb_task = entry

        # 状态流转
        await lock.transition(target_state)

        # 释放锁
        await lock.release()

        # 取消心跳协程
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass

    # ──────────── 辅助 ────────────

    async def pending_size(self) -> int:
        """返回当前 pending 队列长度"""
        redis: aioredis.Redis = get_redis()
        return await redis.llen(_PENDING_QUEUE_KEY)

    @property
    def active_count(self) -> int:
        """本 worker 当前活跃任务数"""
        return len(self._active_locks)
