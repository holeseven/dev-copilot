# @author lxy
import asyncio
from types import SimpleNamespace

import pytest

from engine.infra import dynamic_config


def make_dynamic_config(monkeypatch, *, refresh_interval=3600):
    settings = SimpleNamespace(
        dynamic_config_key="test:config",
        config_refresh_interval=refresh_interval,
    )
    monkeypatch.setattr(dynamic_config, "get_settings", lambda: settings)
    return dynamic_config.DynamicConfig()


@pytest.mark.asyncio
async def test_stop_refresh_waits_until_previous_task_is_done(monkeypatch):
    config = make_dynamic_config(monkeypatch)
    config.start_refresh()
    task = config._refresh_task
    await asyncio.sleep(0)

    await config.stop_refresh()

    assert task is not None
    assert task.done()
    assert config._refresh_task is None


@pytest.mark.asyncio
async def test_stop_refresh_does_not_swallow_caller_cancellation(monkeypatch):
    config = make_dynamic_config(monkeypatch)
    cancellation_received = asyncio.Event()
    release_task = asyncio.Event()

    async def slow_to_cancel():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_received.set()
            await release_task.wait()

    refresh_task = asyncio.create_task(slow_to_cancel())
    config._refresh_task = refresh_task
    stop_task = asyncio.create_task(config.stop_refresh())
    await cancellation_received.wait()

    stop_task.cancel()
    release_task.set()

    with pytest.raises(asyncio.CancelledError):
        await stop_task
    assert refresh_task.done()
    assert config._refresh_task is None
