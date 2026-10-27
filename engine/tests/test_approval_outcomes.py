# @author lxy
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from engine.api import chat
from engine.core.workspace.proposal_store import SqliteProposalStore
from engine.core.workspace.path_guard import WorkspacePathGuard
from engine.tests.test_controlled_change_service import _service
from engine.core.streaming.sse_engine import _sse_line
from engine.core.hitl.interrupt_gate import ApprovalRejectedError


def _prepared_store(tmp_path):
    database = tmp_path / "proposals.sqlite3"
    store = SqliteProposalStore(database)
    service, root = _service(
        tmp_path,
        generation="generation-1",
        proposal_store=store,
    )
    preview = service.preview_create(
        "service/outcome.py", "value = 1\n", "python_pytest"
    )
    return database, store, service, root, preview


@pytest.mark.asyncio
async def test_outcome_claim_is_session_hashed_and_compare_and_set(tmp_path):
    database, first, _service_instance, _root, preview = _prepared_store(tmp_path)
    second = SqliteProposalStore(database)
    session_id = "private-session-value"

    results = await asyncio.gather(
        asyncio.to_thread(
            first.claim_outcome,
            "workspace-1",
            "generation-1",
            preview["proposal_id"],
            session_id,
        ),
        asyncio.to_thread(
            second.claim_outcome,
            "workspace-1",
            "generation-1",
            preview["proposal_id"],
            session_id,
        ),
    )

    assert sum(claimed for _outcome, claimed in results) == 1
    assert {outcome.state for outcome, _claimed in results} == {"applying"}
    assert first.get_outcome(
        "workspace-1", "generation-1", preview["proposal_id"], session_id
    ).state == "applying"
    assert first.get_outcome(
        "workspace-1", "generation-1", preview["proposal_id"], "other-session"
    ) is None

    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT session_sha256 FROM approval_outcomes"
        ).fetchone()[0]
        raw_database = "\n".join(connection.iterdump())
    assert stored_hash == hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    assert session_id not in raw_database


def test_claimed_proposal_is_not_evicted_and_can_be_consumed(tmp_path):
    database = tmp_path / "proposals.sqlite3"
    store = SqliteProposalStore(database)
    service, _root = _service(
        tmp_path,
        generation="generation-1",
        proposal_store=store,
        max_pending=1,
    )
    first = service.preview_create(
        "service/first.py", "first = True\n", "python_pytest"
    )
    outcome, claimed = store.claim_outcome(
        "workspace-1", "generation-1", first["proposal_id"], "session-1"
    )
    assert claimed is True
    assert outcome is not None and outcome.state == "applying"

    second = service.preview_create(
        "service/second.py", "second = True\n", "python_pytest"
    )

    first_stored = store.load(
        "workspace-1", "generation-1", first["proposal_id"], now=0
    )
    second_stored = store.load(
        "workspace-1", "generation-1", second["proposal_id"], now=0
    )
    assert first_stored is not None and first_stored.state == "claimed"
    assert second_stored is not None and second_stored.state == "pending"
    assert store.consume(
        "workspace-1",
        "generation-1",
        first["proposal_id"],
        first["patch_sha256"],
        now=0,
    ) is True


def test_terminal_outcome_persists_only_sanitized_change_result(tmp_path):
    _database, store, _service_instance, root, preview = _prepared_store(tmp_path)
    outcome, claimed = store.claim_outcome(
        "workspace-1",
        "generation-1",
        preview["proposal_id"],
        "session-1",
    )
    assert claimed is True

    completed = store.complete_outcome(
        "workspace-1",
        "generation-1",
        preview["proposal_id"],
        "session-1",
        "applied_tests_failed",
        {
            "status": "applied_tests_failed",
            "mutation": {
                "proposal_id": preview["proposal_id"],
                "workspace_id": "workspace-1",
                "virtual_path": "service/outcome.py",
                "operation": "create",
                "before_sha256": None,
                "after_sha256": preview["after_sha256"],
                "host_path": str(root / "outcome.py"),
            },
            "validation": {
                "status": "failed",
                "exit_code": 1,
                "stdout": "secret source",
                "stderr": str(root),
                "truncated": False,
                "profile": "python_pytest",
            },
        },
    )

    assert completed.state == "applied_tests_failed"
    assert completed.result == {
        "status": "applied_tests_failed",
        "mutation": {
            "proposal_id": preview["proposal_id"],
            "workspace_id": "workspace-1",
            "virtual_path": "service/outcome.py",
            "operation": "create",
            "before_sha256": None,
            "after_sha256": preview["after_sha256"],
        },
        "validation": {
            "status": "failed",
            "exit_code": 1,
            "truncated": False,
            "profile": "python_pytest",
        },
    }
    assert str(root) not in str(completed.result)
    assert "secret source" not in str(completed.result)


def test_outcome_rejects_invalid_terminal_state_and_scope(tmp_path):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    assert store.claim_outcome(
        "other-workspace", "generation-1", preview["proposal_id"], "session-1"
    ) == (None, False)

    store.claim_outcome(
        "workspace-1", "generation-1", preview["proposal_id"], "session-1"
    )
    with pytest.raises(ValueError, match="outcome state"):
        store.complete_outcome(
            "workspace-1",
            "generation-1",
            preview["proposal_id"],
            "session-1",
            "arbitrary",
            {},
        )


def test_outcome_claim_requires_a_still_pending_proposal(tmp_path):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    store.discard(
        "workspace-1", "generation-1", preview["proposal_id"], "expired"
    )

    assert store.claim_outcome(
        "workspace-1", "generation-1", preview["proposal_id"], "session-1"
    ) == (None, False)


@pytest.mark.asyncio
async def test_outcome_api_is_strictly_scoped_and_unknown_is_not_found(tmp_path):
    _database, store, service, _root, preview = _prepared_store(tmp_path)
    store.claim_outcome(
        "workspace-1", "generation-1", preview["proposal_id"], "session-1"
    )
    store.complete_outcome(
        "workspace-1",
        "generation-1",
        preview["proposal_id"],
        "session-1",
        "rejected",
        {"status": "rejected"},
    )

    class Registry:
        def get(self, workspace_id):
            return service.workspace if workspace_id == "workspace-1" else None

    app = FastAPI()
    app.state.workspace_registry = Registry()
    app.state.proposal_store = store
    app.include_router(chat.router, prefix="/chat")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        found = await client.post(
            "/chat/outcome",
            json={
                "workspace_id": "workspace-1",
                "session_id": "session-1",
                "approval_id": preview["proposal_id"],
            },
        )
        wrong_session = await client.post(
            "/chat/outcome",
            json={
                "workspace_id": "workspace-1",
                "session_id": "session-2",
                "approval_id": preview["proposal_id"],
            },
        )
        unknown = await client.post(
            "/chat/outcome",
            json={
                "workspace_id": "workspace-1",
                "session_id": "session-1",
                "approval_id": "unknown-approval",
            },
        )

    assert found.status_code == 200
    assert found.json() == {
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "approval_id": preview["proposal_id"],
        "status": "rejected",
        "terminal": True,
    }
    assert wrong_session.status_code == 404
    assert unknown.status_code == 404


def test_restart_reconciles_after_hash_without_reapplying(tmp_path):
    database, store, service, root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    target = root / "outcome.py"
    target.write_bytes(b"value = 1\n")

    restarted = SqliteProposalStore(database)
    outcomes = restarted.reconcile_applying(
        service.workspace,
        WorkspacePathGuard.from_roots((tmp_path / "allowed",)),
    )

    assert [outcome.state for outcome in outcomes] == ["applied_recovered"]
    stored = restarted.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == "applied_recovered"
    assert stored.result["status"] == "applied_recovered"
    assert stored.result["mutation"]["after_sha256"] == preview["after_sha256"]
    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.parametrize("content", [None, "unexpected = True\n"])
def test_restart_marks_before_or_unknown_hash_as_recovery_required(
    tmp_path, content
):
    database, store, service, root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    target = root / "outcome.py"
    if content is not None:
        target.write_text(content, encoding="utf-8")

    restarted = SqliteProposalStore(database)
    outcomes = restarted.reconcile_applying(
        service.workspace,
        WorkspacePathGuard.from_roots((tmp_path / "allowed",)),
    )

    assert [outcome.state for outcome in outcomes] == ["recovery_required"]
    stored = restarted.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.result["status"] == "recovery_required"
    assert chat._outcome_response("workspace-1", "session-1", stored)["status"] == (
        "recovery_required"
    )
    assert "mutation" not in stored.result
    assert not target.exists() if content is None else target.read_text() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("validation_status", "expected"),
    [
        ("passed", "applied_and_tests_passed"),
        ("failed", "applied_tests_failed"),
    ],
)
async def test_background_resume_persists_success_and_test_failure(
    tmp_path, monkeypatch, validation_status, expected
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    async def frames(*_args):
        yield _sse_line(
            "file_change",
            {
                "tool_call_id": "apply-outcome",
                "proposal_id": approval_id,
                "workspace_id": "workspace-1",
                "virtual_path": "service/outcome.py",
                "operation": "create",
                "before_sha256": None,
                "after_sha256": preview["after_sha256"],
            },
        )
        yield _sse_line(
            "verification",
            {
                "tool_call_id": "apply-outcome",
                "profile": "python_pytest",
                "status": validation_status,
                "exit_code": 0 if validation_status == "passed" else 1,
                "truncated": False,
            },
        )
        yield _sse_line("done", {"status": "completed"})

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    registry = {("run",): run}
    await chat._execute_resume_run(
        run=run,
        graph=object(),
        config={},
        resume_value={"approved": True},
        approval_id=approval_id,
        coordinator=object(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry=registry,
        key=("run",),
    )

    stored = store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == expected
    assert stored.result["validation"]["status"] == validation_status
    assert registry == {}


@pytest.mark.asyncio
async def test_background_resume_records_applied_change_without_verification_as_recovered(
    tmp_path, monkeypatch
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    async def frames(*_args):
        yield _sse_line(
            "file_change",
            {
                "tool_call_id": "apply-outcome",
                "proposal_id": approval_id,
                "workspace_id": "workspace-1",
                "virtual_path": "service/outcome.py",
                "operation": "create",
                "before_sha256": None,
                "after_sha256": preview["after_sha256"],
            },
        )
        yield _sse_line("error", {"code": "verification_unavailable"})
        yield _sse_line("done", {"status": "failed"})

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    await chat._execute_resume_run(
        run=run,
        graph=object(),
        config={},
        resume_value={"approved": True},
        approval_id=approval_id,
        coordinator=object(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry={("run",): run},
        key=("run",),
    )

    stored = store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == "applied_recovered"
    assert stored.result["mutation"]["proposal_id"] == approval_id
    assert "validation" not in stored.result


@pytest.mark.asyncio
async def test_background_resume_exception_after_change_is_applied_recovered(
    tmp_path, monkeypatch
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    async def frames(*_args):
        yield _sse_line(
            "file_change",
            {
                "tool_call_id": "apply-before-error",
                "proposal_id": approval_id,
                "workspace_id": "workspace-1",
                "virtual_path": "service/outcome.py",
                "operation": "create",
                "before_sha256": None,
                "after_sha256": preview["after_sha256"],
            },
        )
        raise RuntimeError("verification stream failed")

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    await chat._execute_resume_run(
        run=run,
        graph=object(),
        config={},
        resume_value={"approved": True},
        approval_id=approval_id,
        coordinator=object(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry={("run",): run},
        key=("run",),
    )

    stored = store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == "applied_recovered"
    assert stored.result["mutation"]["proposal_id"] == approval_id
    assert "validation" not in stored.result


@pytest.mark.asyncio
async def test_background_resume_failure_without_change_discards_orphan_verification(
    tmp_path, monkeypatch
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    async def frames(*_args):
        yield _sse_line(
            "verification",
            {
                "tool_call_id": "orphan-verification",
                "profile": "python_pytest",
                "status": "failed",
                "exit_code": 1,
                "truncated": False,
            },
        )
        yield _sse_line("done", {"status": "failed"})

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    await chat._execute_resume_run(
        run=run,
        graph=object(),
        config={},
        resume_value={"approved": True},
        approval_id=approval_id,
        coordinator=object(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry={("run",): run},
        key=("run",),
    )

    stored = store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == "failed"
    assert stored.result == {"status": "failed"}


@pytest.mark.asyncio
async def test_background_resume_does_not_bind_verification_from_another_tool_call(
    tmp_path, monkeypatch
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    async def frames(*_args):
        yield _sse_line(
            "file_change",
            {
                "tool_call_id": "apply-target",
                "proposal_id": approval_id,
                "workspace_id": "workspace-1",
                "virtual_path": "service/outcome.py",
                "operation": "create",
                "before_sha256": None,
                "after_sha256": preview["after_sha256"],
            },
        )
        yield _sse_line(
            "verification",
            {
                "tool_call_id": "apply-other",
                "profile": "python_pytest",
                "status": "passed",
                "exit_code": 0,
                "truncated": False,
            },
        )
        yield _sse_line("done", {"status": "completed"})

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    await chat._execute_resume_run(
        run=run,
        graph=object(),
        config={},
        resume_value={"approved": True},
        approval_id=approval_id,
        coordinator=object(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry={("run",): run},
        key=("run",),
    )

    stored = store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    assert stored.state == "applied_recovered"
    assert "validation" not in stored.result


@pytest.mark.asyncio
async def test_client_subscription_cancel_does_not_cancel_background_resume(
    tmp_path, monkeypatch
):
    _database, store, _service_instance, _root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )
    release = asyncio.Event()

    async def frames(*_args):
        yield _sse_line("message", {"token": "working"})
        await release.wait()
        yield _sse_line(
            "error", {"code": "approval_rejected", "message": "rejected"}
        )
        yield _sse_line("done", {"status": "failed"})

    monkeypatch.setattr(chat, "_resume_stream_once", frames)
    run = chat._DurableResumeRun()
    registry = {("run",): run}
    task = asyncio.create_task(
        chat._execute_resume_run(
            run=run,
            graph=object(),
            config={},
            resume_value={"approved": False},
            approval_id=approval_id,
            coordinator=object(),
            store=store,
            workspace_id="workspace-1",
            generation="generation-1",
            session_id="session-1",
            registry=registry,
            key=("run",),
        )
    )
    subscriber = run.subscribe()
    assert "working" in await anext(subscriber)
    await subscriber.aclose()
    release.set()
    await task

    assert task.cancelled() is False
    assert store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    ).state == "rejected"


@pytest.mark.asyncio
async def test_disconnected_resume_buffer_is_bounded():
    run = chat._DurableResumeRun()
    for index in range(300):
        await run.publish(_sse_line("message", {"token": f"token-{index}"}))

    assert len(run.frames) <= 256
    assert run.frame_bytes <= 1024 * 1024


@pytest.mark.asyncio
async def test_real_rejection_error_path_is_rejected_without_writing(tmp_path):
    _database, store, _service_instance, root, preview = _prepared_store(tmp_path)
    approval_id = preview["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    )

    class RejectingGraph:
        async def aget_state(self, _config):
            interrupt = SimpleNamespace(
                value={
                    "type": "approval_required",
                    "approval_id": approval_id,
                }
            )
            return SimpleNamespace(
                tasks=(SimpleNamespace(interrupts=(interrupt,)),)
            )

        async def astream_events(self, *_args, **_kwargs):
            if False:
                yield None
            raise ApprovalRejectedError("apply_workspace_change", "user rejected")

    run = chat._DurableResumeRun()
    registry = {("run",): run}
    await chat._execute_resume_run(
        run=run,
        graph=RejectingGraph(),
        config={"configurable": {"thread_id": "internal-thread"}},
        resume_value={"approved": False},
        approval_id=approval_id,
        coordinator=chat.ApprovalCoordinator(),
        store=store,
        workspace_id="workspace-1",
        generation="generation-1",
        session_id="session-1",
        registry=registry,
        key=("run",),
    )

    assert store.get_outcome(
        "workspace-1", "generation-1", approval_id, "session-1"
    ).state == "rejected"
    assert not (root / "outcome.py").exists()


def test_terminal_outcome_history_is_bounded_per_workspace_generation(tmp_path):
    database = tmp_path / "bounded.sqlite3"
    store = SqliteProposalStore(database, max_outcomes=2)
    service, _root = _service(
        tmp_path,
        generation="generation-1",
        proposal_store=store,
        max_pending=2,
    )
    approval_ids = []
    for index in range(4):
        preview = service.preview_create(
            f"service/history-{index}.py", f"value = {index}\n", "python_pytest"
        )
        approval_ids.append(preview["proposal_id"])
        store.claim_outcome(
            "workspace-1", "generation-1", preview["proposal_id"], "session-1"
        )
        store.complete_outcome(
            "workspace-1",
            "generation-1",
            preview["proposal_id"],
            "session-1",
            "rejected",
            {"status": "rejected"},
        )

    outcomes = store.list_outcomes(
        "workspace-1", "generation-1", "session-1"
    )
    assert [item.approval_id for item in outcomes] == approval_ids[-2:]


def test_terminal_outcome_limit_is_shared_across_sessions(tmp_path):
    database = tmp_path / "bounded-across-sessions.sqlite3"
    store = SqliteProposalStore(database, max_outcomes=2)
    service, _root = _service(
        tmp_path,
        generation="generation-1",
        proposal_store=store,
        max_pending=3,
    )
    approval_ids = []
    for index in range(3):
        preview = service.preview_create(
            f"service/session-{index}.py", f"value = {index}\n", "python_pytest"
        )
        approval_ids.append(preview["proposal_id"])
        session_id = f"session-{index}"
        store.claim_outcome(
            "workspace-1", "generation-1", preview["proposal_id"], session_id
        )
        store.complete_outcome(
            "workspace-1",
            "generation-1",
            preview["proposal_id"],
            session_id,
            "rejected",
            {"status": "rejected"},
        )

    assert store.list_outcomes("workspace-1", "generation-1", "session-0") == []
    retained = {
        outcome.approval_id
        for index in (1, 2)
        for outcome in store.list_outcomes(
            "workspace-1", "generation-1", f"session-{index}"
        )
    }
    assert retained == set(approval_ids[-2:])
    with sqlite3.connect(database) as connection:
        terminal_count = connection.execute(
            """
            SELECT COUNT(*) FROM approval_outcomes
            WHERE workspace_id = ? AND generation = ? AND state != 'applying'
            """,
            ("workspace-1", "generation-1"),
        ).fetchone()[0]
    assert terminal_count == 2


def test_new_generation_prunes_old_terminals_but_keeps_old_applying(tmp_path):
    database = tmp_path / "generation-retention.sqlite3"
    store = SqliteProposalStore(database, max_outcomes=2)
    old_service, _old_root = _service(
        tmp_path / "old",
        generation="generation-old",
        proposal_store=store,
        max_pending=2,
    )
    old_applying = old_service.preview_create(
        "service/applying.py", "value = 1\n", "python_pytest"
    )
    store.claim_outcome(
        "workspace-1",
        "generation-old",
        old_applying["proposal_id"],
        "old-applying-session",
    )
    old_terminal = old_service.preview_create(
        "service/terminal.py", "value = 2\n", "python_pytest"
    )
    store.claim_outcome(
        "workspace-1",
        "generation-old",
        old_terminal["proposal_id"],
        "old-terminal-session",
    )
    store.complete_outcome(
        "workspace-1",
        "generation-old",
        old_terminal["proposal_id"],
        "old-terminal-session",
        "rejected",
        {"status": "rejected"},
    )

    current_service, _current_root = _service(
        tmp_path / "current",
        generation="generation-current",
        proposal_store=store,
    )
    current = current_service.preview_create(
        "service/current.py", "value = 3\n", "python_pytest"
    )
    store.claim_outcome(
        "workspace-1",
        "generation-current",
        current["proposal_id"],
        "current-session",
    )

    with sqlite3.connect(database) as connection:
        old_rows = connection.execute(
            """
            SELECT approval_id, state FROM approval_outcomes
            WHERE workspace_id = ? AND generation = ? ORDER BY approval_id
            """,
            ("workspace-1", "generation-old"),
        ).fetchall()
    assert old_rows == [(old_applying["proposal_id"], "applying")]


def test_outcome_retention_keeps_latest_terminals_and_never_prunes_applying(
    tmp_path,
):
    database, store, service, _root, initial = _prepared_store(tmp_path)
    session_id = "session-retention"
    applying_id = initial["proposal_id"]
    store.claim_outcome(
        "workspace-1", "generation-1", applying_id, session_id
    )

    terminal_ids = []
    for index in range(102):
        preview = service.preview_create(
            f"service/retention-{index}.py", f"value = {index}\n", "python_pytest"
        )
        approval_id = preview["proposal_id"]
        terminal_ids.append(approval_id)
        outcome, claimed = store.claim_outcome(
            "workspace-1", "generation-1", approval_id, session_id
        )
        assert outcome is not None and claimed is True
        store.complete_outcome(
            "workspace-1",
            "generation-1",
            approval_id,
            session_id,
            "rejected",
            {"status": "rejected"},
        )

    outcomes = store.list_outcomes(
        "workspace-1", "generation-1", session_id
    )
    returned_ids = [item.approval_id for item in outcomes]

    assert len([item for item in outcomes if item.state != "applying"]) == 100
    assert applying_id in returned_ids
    assert terminal_ids[:2] == [
        approval_id for approval_id in terminal_ids if approval_id not in returned_ids
    ]
    assert returned_ids[-1] == terminal_ids[-1]
    with sqlite3.connect(database) as connection:
        terminal_count = connection.execute(
            "SELECT count(*) FROM approval_outcomes WHERE state != 'applying'"
        ).fetchone()[0]
        applying_count = connection.execute(
            "SELECT count(*) FROM approval_outcomes WHERE state = 'applying'"
        ).fetchone()[0]
    assert (terminal_count, applying_count) == (100, 1)
