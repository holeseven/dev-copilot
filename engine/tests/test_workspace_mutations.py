# @author lxy
from __future__ import annotations

from dataclasses import replace
import os

import pytest

from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.mutations import (
    MutationConflictError,
    MutationValidationError,
    WorkspaceMutator,
)
from engine.core.workspace.path_guard import WorkspacePathGuard


def _mutator(tmp_path, *, max_file_bytes=1024 * 1024):
    allowed = tmp_path / "allowed"
    repository_root = allowed / "service"
    repository_root.mkdir(parents=True)
    workspace = Workspace(
        id="workspace-1",
        name="Demo",
        repositories=[Repository(name="service", path=str(repository_root))],
    )
    return (
        WorkspaceMutator(
            workspace=workspace,
            path_guard=WorkspacePathGuard.from_roots((allowed,)),
            max_file_bytes=max_file_bytes,
        ),
        repository_root,
    )


def test_preview_replace_is_read_only_and_returns_auditable_diff(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "src" / "hello.py"
    target.parent.mkdir()
    target.write_text('message = "old"\n', encoding="utf-8")

    proposal = mutator.preview_replace(
        "service/src/hello.py",
        old_text='"old"',
        new_text='"new"',
    )

    assert target.read_text(encoding="utf-8") == 'message = "old"\n'
    assert proposal.workspace_id == "workspace-1"
    assert proposal.virtual_path == "service/src/hello.py"
    assert proposal.operation == "replace"
    assert len(proposal.approval_id) >= 32
    assert proposal.before_sha256 != proposal.after_sha256
    assert len(proposal.patch_sha256) == 64
    assert "--- a/service/src/hello.py" in proposal.diff
    assert "+++ b/service/src/hello.py" in proposal.diff
    assert '-message = "old"' in proposal.diff
    assert '+message = "new"' in proposal.diff
    assert "new_content=" not in repr(proposal)


def test_preview_requires_one_exact_match_and_leaves_file_unchanged(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "value.txt"
    target.write_text("same same", encoding="utf-8")

    with pytest.raises(MutationValidationError, match="exactly once"):
        mutator.preview_replace("service/value.txt", "same", "changed")

    assert target.read_text(encoding="utf-8") == "same same"


def test_preview_write_creates_nothing_until_apply(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "notes" / "new.txt"

    proposal = mutator.preview_write("service/notes/new.txt", "hello\n")

    assert not target.exists()
    assert proposal.operation == "create"
    assert proposal.before_sha256 is None
    assert "@@ -0,0 +1 @@" in proposal.diff

    result = mutator.apply(proposal)

    assert target.read_text(encoding="utf-8") == "hello\n"
    assert result.approval_id == proposal.approval_id
    assert result.after_sha256 == proposal.after_sha256


def test_apply_rejects_stale_file_without_writing(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    proposal = mutator.preview_replace("service/config.txt", "before", "after")
    target.write_text("someone else changed it\n", encoding="utf-8")

    with pytest.raises(MutationConflictError, match="changed since preview"):
        mutator.apply(proposal)

    assert target.read_text(encoding="utf-8") == "someone else changed it\n"


def test_create_rejects_target_that_appeared_after_preview(tmp_path):
    mutator, root = _mutator(tmp_path)
    proposal = mutator.preview_write("service/new.txt", "agent content\n")
    target = root / "new.txt"
    target.write_text("owner content\n", encoding="utf-8")

    with pytest.raises(MutationConflictError, match="changed since preview"):
        mutator.apply(proposal)

    assert target.read_text(encoding="utf-8") == "owner content\n"


def test_create_uses_atomic_no_replace_when_target_appears_at_commit(
    tmp_path, monkeypatch
):
    mutator, root = _mutator(tmp_path)
    proposal = mutator.preview_write("service/new.txt", "agent content\n")
    target = root / "new.txt"
    real_link = os.link

    def concurrent_create(source, destination):
        target.write_text("owner content\n", encoding="utf-8")
        return real_link(source, destination)

    monkeypatch.setattr(os, "link", concurrent_create)

    with pytest.raises(MutationConflictError, match="changed since preview"):
        mutator.apply(proposal)

    assert target.read_text(encoding="utf-8") == "owner content\n"
    assert list(root.glob(".dev-copilot-*.tmp")) == []


def test_create_cleanup_failure_does_not_misreport_committed_write(
    tmp_path, monkeypatch
):
    mutator, root = _mutator(tmp_path)
    proposal = mutator.preview_write("service/new.txt", "agent content\n")
    real_unlink = type(root).unlink
    failed_once = False

    def fail_first_temp_cleanup(path, *args, **kwargs):
        nonlocal failed_once
        if path.name.startswith(".dev-copilot-") and not failed_once:
            failed_once = True
            raise OSError("simulated antivirus sharing violation")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "unlink", fail_first_temp_cleanup)

    result = mutator.apply(proposal)

    assert result.after_sha256 == proposal.after_sha256
    assert (root / "new.txt").read_text(encoding="utf-8") == "agent content\n"
    assert list(root.glob(".dev-copilot-*.tmp")) == []


def test_apply_rejects_repository_replaced_after_preview(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    proposal = mutator.preview_replace("service/config.txt", "before", "after")
    moved = root.with_name("service-old")
    root.rename(moved)
    root.mkdir()
    (root / "config.txt").write_text("before\n", encoding="utf-8")

    with pytest.raises(MutationConflictError, match="repository changed"):
        mutator.apply(proposal)

    assert (root / "config.txt").read_text(encoding="utf-8") == "before\n"
    assert (moved / "config.txt").read_text(encoding="utf-8") == "before\n"


def test_apply_rejects_tampered_proposal_without_writing(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    proposal = mutator.preview_replace("service/config.txt", "before", "after")
    tampered = replace(proposal, new_content="malicious\n")

    with pytest.raises(MutationValidationError, match="integrity"):
        mutator.apply(tampered)

    assert target.read_text(encoding="utf-8") == "before\n"


def test_successful_proposal_cannot_be_applied_twice(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    proposal = mutator.preview_replace("service/config.txt", "before", "after")

    mutator.apply(proposal)

    with pytest.raises(MutationValidationError, match="already been applied"):
        mutator.apply(proposal)
    assert target.read_text(encoding="utf-8") == "after\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode semantics")
def test_replace_preserves_existing_file_mode(tmp_path):
    mutator, root = _mutator(tmp_path)
    target = root / "script.sh"
    target.write_text("echo before\n", encoding="utf-8")
    target.chmod(0o751)
    proposal = mutator.preview_replace("service/script.sh", "before", "after")

    mutator.apply(proposal)

    assert target.stat().st_mode & 0o777 == 0o751


def test_atomic_replace_failure_preserves_original_and_removes_temp_file(
    tmp_path, monkeypatch
):
    mutator, root = _mutator(tmp_path)
    target = root / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    proposal = mutator.preview_replace("service/config.txt", "before", "after")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(MutationValidationError, match="atomically apply"):
        mutator.apply(proposal)

    assert target.read_text(encoding="utf-8") == "before\n"
    assert list(root.glob(".dev-copilot-*.tmp")) == []


@pytest.mark.parametrize(
    "virtual_path",
    ["../outside.txt", "service/../../outside.txt", "C:/outside.txt"],
)
def test_preview_rejects_paths_outside_virtual_workspace(tmp_path, virtual_path):
    mutator, _ = _mutator(tmp_path)

    with pytest.raises((PermissionError, MutationValidationError)):
        mutator.preview_write(virtual_path, "blocked")


def test_preview_rejects_non_utf8_and_oversized_files(tmp_path):
    mutator, root = _mutator(tmp_path, max_file_bytes=8)
    (root / "binary.bin").write_bytes(b"\xff\xfe")
    (root / "large.txt").write_text("123456789", encoding="utf-8")

    with pytest.raises(MutationValidationError, match="UTF-8"):
        mutator.preview_replace("service/binary.bin", "x", "y")
    with pytest.raises(MutationValidationError, match="size limit"):
        mutator.preview_replace("service/large.txt", "1", "2")


@pytest.mark.skipif(os.name == "nt", reason="POSIX file symlink semantics")
def test_preview_rejects_symlink_even_when_it_stays_inside_repository(tmp_path):
    mutator, root = _mutator(tmp_path)
    actual = root / "actual.txt"
    actual.write_text("before\n", encoding="utf-8")
    (root / "linked.txt").symlink_to(actual)

    with pytest.raises(MutationValidationError, match="unsafe"):
        mutator.preview_replace("service/linked.txt", "before", "after")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_preview_rejects_windows_junction_inside_repository(tmp_path):
    import subprocess

    mutator, root = _mutator(tmp_path)
    actual = root / "actual"
    actual.mkdir()
    (actual / "value.txt").write_text("before\n", encoding="utf-8")
    junction = root / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(actual)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    try:
        with pytest.raises(MutationValidationError, match="unsafe"):
            mutator.preview_replace("service/linked/value.txt", "before", "after")
    finally:
        os.rmdir(junction)
