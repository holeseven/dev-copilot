import asyncio
import json

import pytest

from engine.core.memory.memory_manager import MemoryManager
from engine.core.memory.memory_retriever import MemoryRetriever


@pytest.mark.asyncio
async def test_manager_uses_synchronous_redis_factory(monkeypatch, fake_redis):
    from engine.core.memory import memory_manager

    monkeypatch.setattr(memory_manager, "get_redis", lambda: fake_redis)

    assert await MemoryManager()._ensure_redis() is fake_redis


@pytest.mark.asyncio
async def test_manager_writes_counts_and_clears_a_turn(monkeypatch, fake_redis):
    from engine.core.memory import memory_manager

    monkeypatch.setattr(memory_manager, "get_redis", lambda: fake_redis)
    manager = MemoryManager()

    await manager.add_turn("session-1", "question", "answer")
    await asyncio.wait_for(fake_redis.zadd_event.wait(), timeout=1)

    stored = next(iter(fake_redis.zsets["mem:session-1"]))
    assert json.loads(stored)["q"] == "question"
    assert await manager.get_turn_count("session-1") == 1
    await manager.clear_session("session-1")
    assert await manager.get_turn_count("session-1") == 0


@pytest.mark.asyncio
async def test_retriever_uses_synchronous_factory_and_returns_recent_turns_in_order(
    monkeypatch, fake_redis
):
    from engine.core.memory import memory_retriever

    monkeypatch.setattr(memory_retriever, "get_redis", lambda: fake_redis)
    fake_redis.zsets["mem:session-1"] = {
        json.dumps({"q": "first", "a": "one", "ts": 1}): 1,
        json.dumps({"q": "second", "a": "two", "ts": 2}): 2,
        json.dumps({"q": "third", "a": "three", "ts": 3}): 3,
    }

    records = await MemoryRetriever().get_recent("session-1", last_n=2)

    assert [record["q"] for record in records] == ["second", "third"]
