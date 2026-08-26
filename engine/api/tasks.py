# @author lxy
"""
Tasks API 路由 —— 拉模式任务队列的 HTTP 接口

- POST /tasks/submit：提交任务进队列
- GET /tasks/poll：拉取并 CAS 抢占任务
- GET /tasks/{task_id}：查询任务状态
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from engine.core.scheduler.task_queue import TaskQueue

router = APIRouter()

# 模块级 TaskQueue 实例（应用生命周期内共享）
_task_queue = TaskQueue()

# 简易内存任务存储（生产环境应持久化到 Redis/DB）
_task_store: dict[str, dict] = {}


# ─── 请求/响应模型 ────────────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    """提交任务请求"""
    task_type: str = Field(default="agent_run", description="任务类型")
    payload: dict = Field(default_factory=dict, description="任务负载数据")
    priority: int = Field(default=0, description="优先级（0=普通）")


class SubmitResponse(BaseModel):
    """提交任务响应"""
    task_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str
    status: str
    task_type: str
    payload: dict
    worker_id: Optional[str] = None


class PollResponse(BaseModel):
    """拉取任务响应"""
    task_id: Optional[str] = None
    claimed: bool
    message: str


# ─── POST /tasks/submit ───────────────────────────────────────────────────────

@router.post("/submit", response_model=SubmitResponse)
async def submit_task(request: SubmitRequest):
    """
    提交任务到 pending 队列。

    生成唯一 task_id，存储元信息后推入 Redis 队列尾部。
    """
    task_id = f"task-{uuid.uuid4().hex[:12]}"

    # 存储任务元信息
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "task_type": request.task_type,
        "payload": request.payload,
        "priority": request.priority,
        "worker_id": None,
    }

    # 推入队列
    await _task_queue.submit(task_id)

    logger.info("任务已提交 | task_id={} | type={}", task_id, request.task_type)

    return SubmitResponse(
        task_id=task_id,
        status="pending",
        message="任务已提交到队列",
    )


# ─── GET /tasks/poll ──────────────────────────────────────────────────────────

@router.get("/poll", response_model=PollResponse)
async def poll_task(
    worker_id: str = Query(..., description="Worker 标识"),
):
    """
    从队列头部拉取并 CAS 抢占一个任务。

    成功返回 task_id，失败返回 claimed=False。
    """
    claimed_id = await _task_queue.poll_and_claim(worker_id)

    if claimed_id is None:
        return PollResponse(
            task_id=None,
            claimed=False,
            message="队列为空或抢占失败",
        )

    # 更新内存状态
    if claimed_id in _task_store:
        _task_store[claimed_id]["status"] = "running"
        _task_store[claimed_id]["worker_id"] = worker_id

    logger.info("任务已抢占 | task_id={} | worker={}", claimed_id, worker_id)

    return PollResponse(
        task_id=claimed_id,
        claimed=True,
        message="抢占成功",
    )


# ─── GET /tasks/{task_id} ─────────────────────────────────────────────────────

@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    查询指定任务的当前状态。
    """
    task_info = _task_store.get(task_id)
    if task_info is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    return TaskStatusResponse(
        task_id=task_info["task_id"],
        status=task_info["status"],
        task_type=task_info["task_type"],
        payload=task_info["payload"],
        worker_id=task_info.get("worker_id"),
    )
