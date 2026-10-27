# @author lxy
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from engine.core.workspace.change_service import (
    ControlledChangeService,
    ProposalUnavailableError,
)
from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.mutations import MutationConflictError
from engine.core.workspace.path_guard import WorkspacePathGuard


class FakeClock:
    def __init__(self, value: float = 1_700_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingRunner:
    outcomes: list[dict] = []
    runs: list[tuple[str, str]] = []
    validations: list[tuple[str, str]] = []

    def __init__(self, repository) -> None:
        self.repository = repository

    def validate_profile(self, profile: str) -> None:
        self.validations.append((self.repository.name, profile))
        if profile not in {"python_pytest", "maven_test", "npm_test"}:
            raise ValueError("unsupported test profile")
        marker = {
            "python_pytest": "pyproject.toml",
            "maven_test": "pom.xml",
            "npm_test": "package.json",
        }[profile]
        if not (self.repository.root / marker).is_file():
            raise ValueError("required project marker is missing")

    async def run(self, profile: str) -> dict:
        self.runs.append((self.repository.name, profile))
        if self.outcomes:
            return self.outcomes.pop(0)
        return {
            "status": "passed",
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "truncated": False,
            "profile": profile,
        }


@pytest.fixture(autouse=True)
def reset_runner() -> None:
    RecordingRunner.outcomes = []
    RecordingRunner.runs = []
    RecordingRunner.validations = []


def _service(
    tmp_path: Path,
    *,
    clock=None,
    ttl: float = 1800,
    max_pending: int = 100,
    runner_factory=RecordingRunner,
):
    allowed = tmp_path / "allowed"
    root = allowed / "service"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    workspace = Workspace(
        id="workspace-1",
        name="Demo",
        repositories=[Repository(name="service", path=str(root))],
    )
    service = ControlledChangeService(
        workspace=workspace,
        path_guard=WorkspacePathGuard.from_roots((allowed,)),
        runner_factory=runner_factory,
        clock=clock or FakeClock(),
        proposal_ttl_seconds=ttl,
        max_pending=max_pending,
    )
    return service, root


def test_preview_is_read_only_and_returns_only_safe_public_fields(tmp_path):
    service, root = _service(tmp_path)
    target = root / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")

    preview = service.preview_replace(
        "service/app.py",
        "value = 1",
        "value = 2",
        "python_pytest",
    )

    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert set(preview) == {
        "status",
        "proposal_id",
        "workspace_id",
        "repository",
        "virtual_path",
        "operation",
        "before_sha256",
        "after_sha256",
        "patch_sha256",
        "diff",
        "validation_profile",
        "expires_at",
    }
    assert preview["status"] == "previewed"
    assert preview["virtual_path"] == "service/app.py"
    assert "value = 2" in preview["diff"]
    assert str(root) not in str(preview)
    assert "new_content" not in preview
    assert RecordingRunner.validations == [("service", "python_pytest")]


def test_preview_prevalidates_profile_and_project_marker(tmp_path):
    service, root = _service(tmp_path)
    (root / "pyproject.toml").unlink()

    with pytest.raises(ValueError, match="marker"):
        service.preview_create(
            "service/new.py", "print('x')\n", "python_pytest"
        )

    assert not (root / "new.py").exists()


def test_capacity_evicts_oldest_proposal_and_expiry_fails_closed(tmp_path):
    clock = FakeClock()
    service, _ = _service(tmp_path, clock=clock, ttl=10, max_pending=2)
    first = service.preview_create(
        "service/one.py", "one\n", "python_pytest"
    )
    second = service.preview_create(
        "service/two.py", "two\n", "python_pytest"
    )
    third = service.preview_create(
        "service/three.py", "three\n", "python_pytest"
    )

    with pytest.raises(ProposalUnavailableError, match="unavailable"):
        service.approval_request(first["proposal_id"], first["patch_sha256"])
    assert service.approval_request(
        second["proposal_id"], second["patch_sha256"]
    ).approval_id == second["proposal_id"]

    clock.value += 11
    with pytest.raises(ProposalUnavailableError, match="expired"):
        service.approval_request(third["proposal_id"], third["patch_sha256"])


def test_approval_request_is_server_bound_and_contains_no_host_path(tmp_path):
    service, root = _service(tmp_path)
    preview = service.preview_create(
        "service/new.py", "secret content\n", "python_pytest"
    )

    approval = service.approval_request(
        preview["proposal_id"], preview["patch_sha256"]
    )

    assert approval.approval_id == preview["proposal_id"]
    assert approval.action == "apply_workspace_change"
    assert approval.payload == {
        "tool_name": "apply_workspace_change",
        "operation": "create",
        "workspace_id": "workspace-1",
        "repository": "service",
        "virtual_path": "service/new.py",
        "diff": preview["diff"],
        "before_sha256": None,
        "after_sha256": preview["after_sha256"],
        "patch_sha256": preview["patch_sha256"],
        "validation_profile": "python_pytest",
    }
    assert str(root) not in str(approval.payload)
    assert "secret content" in approval.payload["diff"]


@pytest.mark.asyncio
async def test_apply_writes_once_then_runs_bound_profile(tmp_path):
    service, root = _service(tmp_path)
    preview = service.preview_create(
        "service/new.py", "print('ok')\n", "python_pytest"
    )

    result = await service.apply_approved(
        preview["proposal_id"],
        preview["patch_sha256"],
        approval_id=preview["proposal_id"],
    )

    assert (root / "new.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert result["status"] == "applied_and_tests_passed"
    assert result["mutation"] == {
        "proposal_id": preview["proposal_id"],
        "workspace_id": "workspace-1",
        "virtual_path": "service/new.py",
        "operation": "create",
        "before_sha256": None,
        "after_sha256": preview["after_sha256"],
    }
    assert result["validation"]["profile"] == "python_pytest"
    assert RecordingRunner.runs == [("service", "python_pytest")]

    with pytest.raises(ProposalUnavailableError, match="already consumed"):
        await service.apply_approved(
            preview["proposal_id"],
            preview["patch_sha256"],
            approval_id=preview["proposal_id"],
        )
    assert RecordingRunner.runs == [("service", "python_pytest")]


@pytest.mark.asyncio
async def test_hash_or_approval_mismatch_never_writes_or_runs_tests(tmp_path):
    service, root = _service(tmp_path)
    preview = service.preview_create(
        "service/new.py", "blocked\n", "python_pytest"
    )

    with pytest.raises(ProposalUnavailableError, match="hash"):
        await service.apply_approved(
            preview["proposal_id"], "0" * 64, approval_id=preview["proposal_id"]
        )
    with pytest.raises(ProposalUnavailableError, match="approval"):
        await service.apply_approved(
            preview["proposal_id"],
            preview["patch_sha256"],
            approval_id="another",
        )

    assert not (root / "new.py").exists()
    assert RecordingRunner.runs == []


@pytest.mark.asyncio
async def test_conflict_consumes_proposal_and_skips_tests(tmp_path):
    service, root = _service(tmp_path)
    target = root / "app.py"
    target.write_text("before\n", encoding="utf-8")
    preview = service.preview_replace(
        "service/app.py", "before", "after", "python_pytest"
    )
    target.write_text("owner change\n", encoding="utf-8")

    with pytest.raises(MutationConflictError, match="changed since preview"):
        await service.apply_approved(
            preview["proposal_id"],
            preview["patch_sha256"],
            approval_id=preview["proposal_id"],
        )

    assert target.read_text(encoding="utf-8") == "owner change\n"
    assert RecordingRunner.runs == []
    assert preview["proposal_id"] not in service.mutator._issued
    with pytest.raises(ProposalUnavailableError, match="already consumed"):
        await service.apply_approved(
            preview["proposal_id"],
            preview["patch_sha256"],
            approval_id=preview["proposal_id"],
        )


@pytest.mark.asyncio
async def test_failed_test_is_reported_without_rolling_back_applied_file(tmp_path):
    service, root = _service(tmp_path)
    RecordingRunner.outcomes = [
        {
            "status": "failed",
            "exit_code": 1,
            "stdout": "",
            "stderr": "one failed",
            "truncated": False,
            "profile": "python_pytest",
        }
    ]
    preview = service.preview_create(
        "service/new.py", "kept\n", "python_pytest"
    )

    result = await service.apply_approved(
        preview["proposal_id"],
        preview["patch_sha256"],
        approval_id=preview["proposal_id"],
    )

    assert result["status"] == "applied_tests_failed"
    assert result["validation"]["status"] == "failed"
    assert (root / "new.py").read_text(encoding="utf-8") == "kept\n"


@pytest.mark.asyncio
async def test_repository_lock_covers_apply_and_test(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingRunner(RecordingRunner):
        async def run(self, profile: str) -> dict:
            self.runs.append((self.repository.name, profile))
            if len(self.runs) == 1:
                started.set()
                await release.wait()
            return await super().run(profile)

    service, root = _service(tmp_path, runner_factory=BlockingRunner)
    first = service.preview_create(
        "service/one.py", "one\n", "python_pytest"
    )
    second = service.preview_create(
        "service/two.py", "two\n", "python_pytest"
    )

    first_task = asyncio.create_task(
        service.apply_approved(
            first["proposal_id"], first["patch_sha256"], first["proposal_id"]
        )
    )
    await started.wait()
    second_task = asyncio.create_task(
        service.apply_approved(
            second["proposal_id"], second["patch_sha256"], second["proposal_id"]
        )
    )
    await asyncio.sleep(0)

    assert (root / "one.py").exists()
    assert not (root / "two.py").exists()

    release.set()
    await asyncio.gather(first_task, second_task)
    assert (root / "two.py").read_text(encoding="utf-8") == "two\n"
