# @author lxy
import asyncio
import builtins
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from loguru import logger
from pydantic import ValidationError

from engine.api import rag
from engine.agents.knowledge_agent import KnowledgeAgent
from engine.core.rag.chunker import chunk_source
from engine.core.rag.service import RagService
from engine.core.rag.store import RagStore
from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.streaming.sse_engine import sse_event_generator
from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.path_guard import WorkspacePathGuard
from engine.core.workspace.registry import WorkspaceRegistry
from engine.orchestrator.nodes import tool_node


def _workspace(tmp_path: Path) -> tuple[Workspace, WorkspacePathGuard]:
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = Workspace(
        id="demo",
        name="Demo",
        generation="workspace-generation",
        repositories=[Repository(name="service", path=str(repo))],
    )
    return workspace, WorkspacePathGuard.from_roots((tmp_path,))


def test_chunker_preserves_python_java_and_document_line_citations():
    python = chunk_source(
        "service",
        "app.py",
        "import os\n\nclass Greeter:\n    def hello(self):\n        return 'hi'\n",
    )
    java = chunk_source(
        "service",
        "src/Greeter.java",
        "package demo;\nclass Greeter {\n  public String hello() { return \"hi\"; }\n}\n",
    )
    docs = chunk_source("service", "README.md", "# Setup\nInstall it.\n\n## Run\nStart it.\n")

    assert any(chunk.symbol == "Greeter" and chunk.start_line == 3 for chunk in python)
    assert any(chunk.symbol == "hello" and chunk.start_line == 4 for chunk in python)
    assert any(chunk.symbol == "Greeter" and chunk.start_line == 2 for chunk in java)
    assert [(chunk.symbol, chunk.start_line) for chunk in docs] == [("Setup", 1), ("Run", 4)]
    assert all(chunk.end_line >= chunk.start_line for chunk in python + java + docs)


@pytest.mark.asyncio
async def test_rebuild_is_incremental_atomic_persistent_and_generation_scoped(tmp_path, monkeypatch):
    workspace, guard = _workspace(tmp_path)
    repo = Path(workspace.repositories[0].path)
    (repo / "app.py").write_text("def answer():\n    return 'old'\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=never-index\n", encoding="utf-8")
    (repo / "secret.pem").write_text("PRIVATE KEY never-index\n", encoding="utf-8")
    (repo / "binary.py").write_bytes(b"abc\x00def")
    generated = repo / "node_modules"
    generated.mkdir()
    (generated / "dependency.py").write_text("never-index", encoding="utf-8")

    store = RagStore(tmp_path / "rag.sqlite3")
    service = RagService(store, guard)
    first = await service.rebuild(workspace)

    assert first.state == "ready"
    assert first.file_count == 1
    assert first.chunk_count >= 1
    first_generation = first.index_generation
    citations = service.query(workspace, "answer old", 5).citations
    assert citations
    assert citations[0].virtual_path == "service/app.py"
    assert citations[0].content_sha256
    assert all("never-index" not in item.excerpt for item in citations)

    from engine.core.rag import service as service_module
    real_chunk_source = service_module.chunk_source
    chunked_paths = []

    def capture_chunking(repository, path, text):
        chunked_paths.append(path)
        return real_chunk_source(repository, path, text)

    monkeypatch.setattr(service_module, "chunk_source", capture_chunking)
    unchanged = await service.rebuild(workspace)
    assert unchanged.index_generation != first_generation
    assert unchanged.file_count == 1
    assert chunked_paths == []

    (repo / "app.py").write_text("def answer():\n    return 'new'\n", encoding="utf-8")
    (repo / "added.md").write_text("# Architecture\nUses a queue.\n", encoding="utf-8")
    changed = await service.rebuild(workspace)
    assert chunked_paths == ["added.md", "app.py"]
    assert service.query(workspace, "queue", 5).citations[0].virtual_path == "service/added.md"
    assert not service.query(workspace, "old", 5).citations

    (repo / "added.md").unlink()
    await service.rebuild(workspace)
    assert not service.query(workspace, "queue", 5).citations

    reopened = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)
    assert reopened.status(workspace).state == "ready"
    assert reopened.query(workspace, "new", 5).citations
    rotated = workspace.model_copy(update={"generation": "next-workspace-generation"})
    assert reopened.status(rotated).state == "stale"
    with pytest.raises(RuntimeError, match="not ready"):
        reopened.query(rotated, "new", 5)


def test_interrupted_build_recovers_as_failed(tmp_path):
    workspace, guard = _workspace(tmp_path)
    store = RagStore(tmp_path / "rag.sqlite3")
    status = store.queue(workspace)
    store.mark_building(workspace.id, status.job_id)

    recovered = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)

    assert recovered.status(workspace).state == "failed"
    assert recovered.status(workspace).error_code == "index_interrupted"


@pytest.mark.asyncio
async def test_rag_api_contract_and_background_rebuild(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Payments\nHandles invoices.\n", encoding="utf-8")
    registry = WorkspaceRegistry(tmp_path / "metadata", allowed_roots=(tmp_path,))
    registry.create("demo", "Demo")
    registry.add_repository("demo", "service", repo)
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), registry.path_guard)
    app = FastAPI()
    app.state.workspace_registry = registry
    app.state.rag_service = service
    app.include_router(rag.router, prefix="/workspaces")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/workspaces/demo/rag/index")
        started = await client.post("/workspaces/demo/rag/index/rebuild")
        assert started.status_code == 202
        assert set(started.json()) == {
            "workspace_id", "state", "index_generation", "job_id",
            "repository_count", "file_count", "chunk_count", "updated_at", "error_code",
        }
        await service.wait(started.json()["job_id"])
        ready = await client.get("/workspaces/demo/rag/index")
        queried = await client.post(
            "/workspaces/demo/rag/query", json={"query": "invoices", "top_k": 3}
        )
        invalid = await client.post(
            "/workspaces/demo/rag/query", json={"query": "", "top_k": 21}
        )

    assert initial.json() == {
        "workspace_id": "demo",
        "state": "not_indexed",
        "index_generation": None,
        "job_id": None,
        "repository_count": 0,
        "file_count": 0,
        "chunk_count": 0,
        "updated_at": None,
        "error_code": None,
    }
    assert ready.json()["state"] == "ready"
    assert ready.json()["error_code"] is None
    assert queried.status_code == 200
    assert set(queried.json()) == {"workspace_id", "index_generation", "citations"}
    citation = queried.json()["citations"][0]
    assert set(citation) == {
        "citation_id", "rank", "repository", "virtual_path", "start_line",
        "end_line", "excerpt", "content_sha256",
    }
    assert citation["virtual_path"] == "service/README.md"
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_rebuild_rejects_concurrent_job(tmp_path):
    workspace, guard = _workspace(tmp_path)
    store = RagStore(tmp_path / "rag.sqlite3")
    service = RagService(store, guard)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = service._build

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    service._build = blocked
    first = service.start_rebuild(workspace)
    await entered.wait()
    with pytest.raises(RuntimeError, match="already running"):
        service.start_rebuild(workspace)
    release.set()
    await service.wait(first.job_id)


@pytest.mark.asyncio
async def test_building_and_failed_rebuild_keep_previous_index_queryable(tmp_path):
    workspace, guard = _workspace(tmp_path)
    repo = Path(workspace.repositories[0].path)
    source = repo / "app.py"
    source.write_text("def version():\n    return 'stable'\n", encoding="utf-8")
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)
    ready = await service.rebuild(workspace)
    active_generation = ready.index_generation
    source.write_text("def version():\n    return 'broken-build'\n", encoding="utf-8")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fail_build(_workspace):
        entered.set()
        await release.wait()
        raise RuntimeError("synthetic failure")

    service._build = fail_build
    queued = service.start_rebuild(workspace)
    await entered.wait()

    assert service.status(workspace).state == "building"
    during = service.query(workspace, "stable", 5)
    assert during.index_generation == active_generation
    assert during.citations

    release.set()
    await service.wait(queued.job_id)
    assert service.status(workspace).state == "failed"
    assert service.query(workspace, "stable", 5).citations
    assert not service.query(workspace, "broken-build", 5).citations


@pytest.mark.asyncio
async def test_local_provider_exposes_read_only_workspace_search(tmp_path):
    workspace, guard = _workspace(tmp_path)

    class FakeRag:
        def query(self, received_workspace, query, top_k):
            assert received_workspace is workspace
            assert (query, top_k) == ("payments", 4)
            return {
                "workspace_id": "demo",
                "index_generation": "index-1",
                "citations": [],
            }

    provider = LocalDevToolProvider("local", workspace, guard, rag_service=FakeRag())
    tools = await provider.list_tools()
    result = await provider.call_tool("search_workspace", {"query": "payments", "top_k": 4})

    schema = next(item for item in tools if item["name"] == "search_workspace")
    assert schema["parameters"]["required"] == ["query"]
    assert schema["parameters"]["additionalProperties"] is False
    assert result["workspace_id"] == "demo"


@pytest.mark.asyncio
async def test_tool_node_and_sse_emit_only_safe_citation_fields(monkeypatch):
    citation = {
        "citation_id": "cite-1",
        "rank": 1,
        "repository": "service",
        "virtual_path": "service/app.py",
        "start_line": 1,
        "end_line": 3,
        "excerpt": "def answer(): ...",
        "content_sha256": "a" * 64,
        "absolute_path": "C:/secret/repo/app.py",
    }

    class Manager:
        async def call_tool(self, name, args):
            return {"workspace_id": "demo", "index_generation": "index-1", "citations": [citation]}

    captured = []

    async def capture(name, data, config):
        captured.append((name, data))

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", capture)
    state = {"messages": [AIMessage(content="", tool_calls=[{"name": "search_workspace", "args": {"query": "answer"}, "id": "call-1"}])]}
    await tool_node(state, {"configurable": {"mcp_manager": Manager(), "workspace_id": "demo"}})
    emitted = next(data for name, data in captured if name == "citation")
    assert emitted == {
        "tool_call_id": "call-1",
        "workspace_id": "demo",
        "index_generation": "index-1",
        "citation": {key: citation[key] for key in (
            "citation_id", "rank", "repository", "virtual_path", "start_line",
            "end_line", "excerpt", "content_sha256",
        )},
    }

    class Graph:
        async def astream_events(self, initial_state, config, version):
            yield {
                "event": "on_custom_event", "name": "citation",
                "data": {**emitted, "host_path": "C:/leak", "citation": {**citation, "host_path": "C:/nested-leak"}},
            }

    frames = [frame async for frame in sse_event_generator(Graph(), {}, {})]
    citation_frame = next(frame for frame in frames if frame.startswith("event: citation"))
    assert "service/app.py" in citation_frame
    assert "C:/" not in citation_frame


def test_indexer_revalidates_guarded_path_before_and_after_read(tmp_path, monkeypatch):
    source = tmp_path / "app.py"
    source.write_text("def safe():\n    return True\n", encoding="utf-8")
    workspace = Workspace(
        id="demo",
        name="Demo",
        generation="generation",
        repositories=[Repository(name="service", path=str(tmp_path))],
    )
    resolve_calls = 0
    read_observed_resolve = False

    class Guarded:
        def iter_files(self, relative):
            return (source,)

        def relative_path(self, path, resolve=False):
            return "app.py"

        def resolve(self, relative, *, kind=None, must_exist=True):
            nonlocal resolve_calls
            resolve_calls += 1
            return source

    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        nonlocal read_observed_resolve
        if path == source and args and args[0] == "rb":
            read_observed_resolve = resolve_calls > 0
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), type("Guard", (), {"bind": lambda self, repository: Guarded()})())

    files = service._collect(workspace)

    assert read_observed_resolve is True
    assert resolve_calls >= 2
    assert files[0].size == len(source.read_bytes())


def test_indexer_rejects_when_opened_handle_is_not_the_validated_file(tmp_path, monkeypatch):
    safe = tmp_path / "safe.py"
    outside = tmp_path / "outside.py"
    safe.write_text("def safe():\n    return True\n", encoding="utf-8")
    outside.write_text("SECRET_FROM_OUTSIDE = True\n", encoding="utf-8")
    workspace = Workspace(
        id="demo", name="Demo", generation="generation",
        repositories=[Repository(name="service", path=str(tmp_path))],
    )

    class Guarded:
        def iter_files(self, relative):
            return (safe,)

        def relative_path(self, path, resolve=False):
            return "safe.py"

        def resolve(self, relative, *, kind=None, must_exist=True):
            return safe

    original_open = Path.open

    def swapped_open(path, *args, **kwargs):
        if path == safe and args and args[0] == "rb":
            return builtins.open(outside, "rb")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swapped_open)
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), type("Guard", (), {"bind": lambda self, repository: Guarded()})())

    with pytest.raises(RuntimeError, match="changed while indexing"):
        service._collect(workspace)


@pytest.mark.asyncio
async def test_legacy_knowledge_agent_uses_injected_workspace_scoped_search():
    agent = KnowledgeAgent.__new__(KnowledgeAgent)
    agent._workspace_search = lambda query, top_k: {
        "workspace_id": "demo",
        "index_generation": "index-1",
        "citations": [],
    }

    messages = await agent._execute_search([
        {"name": "search_workspace", "args": {"query": "payments", "top_k": 3}, "id": "call-1"}
    ])

    assert "unknown" not in messages[0].content.lower()
    assert "workspace_id" in messages[0].content


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   ", "top_k": 1},
        {"query": "query", "top_k": True},
        {"query": "query", "top_k": 1, "unexpected": "field"},
    ],
)
def test_rag_query_request_rejects_whitespace_bool_and_unknown_fields(payload):
    with pytest.raises(ValidationError):
        rag.RagQueryRequest.model_validate(payload)


def test_rag_query_request_strips_query_before_use():
    payload = rag.RagQueryRequest.model_validate({"query": "  payment queue  ", "top_k": 3})

    assert payload.query == "payment queue"


@pytest.mark.asyncio
async def test_rag_tasks_are_cleaned_after_wait_without_losing_waiter(tmp_path):
    workspace, guard = _workspace(tmp_path)
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_workspace):
        entered.set()
        await release.wait()
        return []

    service._build = blocked
    started = service.start_rebuild(workspace)
    waiter = asyncio.create_task(service.wait(started.job_id))
    await entered.wait()
    assert not waiter.done()

    release.set()
    await waiter
    await asyncio.sleep(0)

    assert started.job_id not in service._tasks
    assert service.status(workspace).state == "ready"


@pytest.mark.asyncio
async def test_knowledge_agent_never_returns_or_logs_exception_details():
    secret = "C:/private/token.txt API_KEY=super-secret"

    class BrokenLlm:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError(secret)

    agent = KnowledgeAgent.__new__(KnowledgeAgent)
    agent._llm = BrokenLlm()
    agent._tools = []
    agent._workspace_search = lambda _query, _top_k: (_ for _ in ()).throw(RuntimeError(secret))

    log_messages = []
    sink_id = logger.add(log_messages.append, format="{message}")
    try:
        answer = await agent.run("find token")
        tool_messages = await agent._execute_search([
            {"name": "search_workspace", "args": {"query": "token"}, "id": "call-1"}
        ])
    finally:
        logger.remove(sink_id)
    captured_logs = "".join(log_messages)

    assert secret not in answer
    assert "super-secret" not in tool_messages[0].content
    assert "C:/private" not in tool_messages[0].content
    assert secret not in captured_logs
    assert "super-secret" not in captured_logs
    assert "C:/private" not in captured_logs
    assert "knowledge_agent_run_failed" in captured_logs
    assert "knowledge_agent_search_failed" in captured_logs


@pytest.mark.asyncio
async def test_indexer_excludes_case_insensitive_build_dirs_and_secret_files(tmp_path):
    workspace, guard = _workspace(tmp_path)
    repo = Path(workspace.repositories[0].path)
    (repo / "app.py").write_text("SAFE_TOKEN = 'visible'\n", encoding="utf-8")
    for directory in ("NODE_MODULES", "Build"):
        generated = repo / directory
        generated.mkdir()
        (generated / "leak.py").write_text("LEAK_TOKEN = 'hidden'\n", encoding="utf-8")
    for name in ("secret.yml", "secret.json", "secrets.yaml", "Credentials.JSON"):
        (repo / name).write_text("LEAK_TOKEN: hidden\n", encoding="utf-8")

    service = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)
    ready = await service.rebuild(workspace)

    assert ready.file_count == 1
    assert service.query(workspace, "SAFE_TOKEN", 5).citations
    assert not service.query(workspace, "LEAK_TOKEN", 20).citations


@pytest.mark.asyncio
async def test_same_query_is_isolated_between_workspaces(tmp_path):
    left_repo = tmp_path / "left"
    right_repo = tmp_path / "right"
    left_repo.mkdir()
    right_repo.mkdir()
    (left_repo / "app.py").write_text("sharedterm leftonly\n", encoding="utf-8")
    (right_repo / "app.py").write_text("sharedterm rightonly\n", encoding="utf-8")
    left = Workspace(id="left", name="Left", generation="g-left", repositories=[Repository(name="leftrepo", path=str(left_repo))])
    right = Workspace(id="right", name="Right", generation="g-right", repositories=[Repository(name="rightrepo", path=str(right_repo))])
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), WorkspacePathGuard.from_roots((tmp_path,)))
    await service.rebuild(left)
    await service.rebuild(right)

    left_results = service.query(left, "sharedterm", 5).citations
    right_results = service.query(right, "sharedterm", 5).citations

    assert {item.repository for item in left_results} == {"leftrepo"}
    assert {item.repository for item in right_results} == {"rightrepo"}
    assert all("rightonly" not in item.excerpt for item in left_results)
    assert all("leftonly" not in item.excerpt for item in right_results)


@pytest.mark.asyncio
async def test_fts_query_treats_special_syntax_and_long_tokens_as_plain_text(tmp_path):
    workspace, guard = _workspace(tmp_path)
    repo = Path(workspace.repositories[0].path)
    (repo / "app.py").write_text("needle ordinary content\n", encoding="utf-8")
    service = RagService(RagStore(tmp_path / "rag.sqlite3"), guard)
    await service.rebuild(workspace)

    special = service.query(workspace, '" OR NEAR(needle) * - ^', 5)
    long_token = "x" * 20_000
    mixed = service.query(workspace, f"{long_token} needle", 5)

    assert special.citations
    assert mixed.citations
