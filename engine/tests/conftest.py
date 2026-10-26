import asyncio

import pytest


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.zsets = {}
        self.now_ms = 0
        self.zadd_event = asyncio.Event()

    def advance(self, milliseconds):
        self.now_ms += milliseconds

    def _expired(self, key):
        expiry = self.expiries.get(key)
        return expiry is not None and expiry <= self.now_ms

    def _purge(self, key):
        if self._expired(key):
            self.values.pop(key, None)
            self.expiries.pop(key, None)

    async def set(self, key, value):
        self.values[key] = value

    async def get(self, key):
        self._purge(key)
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        self.expiries.pop(key, None)
        self.zsets.pop(key, None)

    async def zadd(self, key, members):
        self.zsets.setdefault(key, {}).update(members)
        self.zadd_event.set()

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zrevrangebyscore(self, key, max, min, start=0, num=None):
        members = sorted(
            self.zsets.get(key, {}).items(), key=lambda item: item[1], reverse=True
        )
        return [member for member, _ in members[start : start + num]]

    async def zrangebyscore(self, key, min, max):
        members = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        return [member for member, score in members if min <= score <= max]

    async def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        values = args[numkeys:]
        if "EXISTS" in script:
            lock_key = keys[0]
            state_key = keys[1] if len(keys) > 1 else None
            owner, ttl = values
            self._purge(lock_key)
            if lock_key in self.values:
                return 0
            self.values[lock_key] = owner
            self.expiries[lock_key] = self.now_ms + int(ttl)
            if state_key is not None:
                self.values[state_key] = "CLAIMED"
            return 1
        if "local current" in script:
            lock_key, state_key = keys
            owner, target = values
            if await self.get(lock_key) != owner:
                return -1
            current = self.values.get(state_key, "INIT")
            valid = {
                "INIT": {"CLAIMED"},
                "CLAIMED": {"RUNNING", "FAILED"},
                "RUNNING": {"COMPLETED", "FAILED"},
                "COMPLETED": set(),
                "FAILED": set(),
            }
            if target not in valid[current]:
                return 0
            self.values[state_key] = target
            return 1
        lock_key = keys[0]
        owner = values[0]
        if await self.get(lock_key) != owner:
            return 0
        if "DEL" in script:
            await self.delete(lock_key)
            return 1
        self.expiries[lock_key] = self.now_ms + int(values[1])
        return 1


class FakeStructuredRouter:
    def __init__(self, result=None, wait_forever=False):
        self.result = result
        self.wait_forever = wait_forever

    async def ainvoke(self, messages):
        if self.wait_forever:
            await asyncio.Event().wait()
        return self.result


class FakeRouter:
    def __init__(self, structured_router):
        self.structured_router = structured_router

    def with_structured_output(self, schema, method):
        return self.structured_router


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def router_factory(monkeypatch):
    def install(structured_router):
        from engine.core.dispatcher import dispatcher

        monkeypatch.setattr(
            dispatcher,
            "get_llm_by_complexity",
            lambda **kwargs: FakeRouter(structured_router),
        )

    return install
