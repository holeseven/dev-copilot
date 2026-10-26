import pytest

from engine.core.scheduler.task_lock import TaskLock, TaskState


@pytest.fixture
def lock_redis(monkeypatch, fake_redis):
    from engine.core.scheduler import task_lock

    monkeypatch.setattr(task_lock, "get_redis", lambda: fake_redis)
    return fake_redis


@pytest.mark.asyncio
async def test_only_one_worker_claims_the_same_task(lock_redis):
    first = TaskLock("task-1", owner="first")
    second = TaskLock("task-1", owner="second")

    assert await first.claim() is True
    assert await second.claim() is False


@pytest.mark.asyncio
async def test_non_owner_cannot_renew_or_release_a_lock(lock_redis):
    owner = TaskLock("task-1", owner="owner")
    other = TaskLock("task-1", owner="other")
    await owner.claim()

    assert await other.renew() is False
    assert await other.release() is False


@pytest.mark.asyncio
async def test_an_expired_lock_can_be_claimed_by_a_new_owner(lock_redis):
    first = TaskLock("task-1", owner="first", ttl_ms=10)
    second = TaskLock("task-1", owner="second", ttl_ms=10)
    await first.claim()
    lock_redis.advance(10)

    assert await second.claim() is True


@pytest.mark.asyncio
async def test_expired_owner_cannot_transition_after_a_new_owner_claims(lock_redis):
    first = TaskLock("task-1", owner="first", ttl_ms=10)
    second = TaskLock("task-1", owner="second", ttl_ms=10)
    await first.claim()
    lock_redis.advance(10)
    await second.claim()

    with pytest.raises(PermissionError):
        await first.transition(TaskState.RUNNING)
