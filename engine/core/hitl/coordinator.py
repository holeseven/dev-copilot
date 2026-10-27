# @author lxy
from __future__ import annotations

import asyncio


class ApprovalCoordinator:
    """Provide one in-process execution lock per internal LangGraph thread."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    async def lock_for(self, thread_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(thread_id, asyncio.Lock())
