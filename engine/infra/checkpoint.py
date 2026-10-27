# @author lxy
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def strict_checkpoint_serializer() -> JsonPlusSerializer:
    """Use only LangGraph's safe msgpack types, without pickle fallback."""

    return JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)


@asynccontextmanager
async def open_sqlite_checkpointer(path: str | Path) -> AsyncIterator[Any]:
    """Open and initialize the durable LangGraph checkpointer."""

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    database = Path(path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(database)) as connection:
        saver = AsyncSqliteSaver(connection, serde=strict_checkpoint_serializer())
        await saver.setup()
        yield saver
