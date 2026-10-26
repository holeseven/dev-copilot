import os
from pathlib import Path
import subprocess

import pytest

from engine.core.workspace.models import Repository
from engine.core.workspace.path_guard import (
    OutsideWorkspaceError,
    WorkspacePathError,
    WorkspacePathGuard,
)


def _guarded_repository(tmp_path):
    allowed = tmp_path / "allowed"
    repository = allowed / "service"
    repository.mkdir(parents=True)
    return (
        WorkspacePathGuard.from_roots((allowed,)),
        Repository(name="service", path=str(repository)),
        repository,
    )


def test_bind_freezes_one_canonical_root_for_child_operations(tmp_path, monkeypatch):
    guard, repository, root = _guarded_repository(tmp_path)
    (root / "main.py").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.py").write_text("OUTSIDE_SECRET", encoding="utf-8")
    original_resolve = Path.resolve
    canonical_root = root.resolve()
    outside_root = outside.resolve()
    root_resolves = 0

    def switching_resolve(path, *args, **kwargs):
        nonlocal root_resolves
        if path == root or path == canonical_root:
            root_resolves += 1
            return canonical_root if root_resolves == 1 else outside_root
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", switching_resolve)

    bound = guard.bind(repository)

    assert bound.read_text("main.py") == "inside"
    assert bound.root == canonical_root
    assert root_resolves == 1


@pytest.mark.parametrize("unsafe_path", ["../outside.txt", "C:/outside.txt", "//server/share/file"])
def test_guard_rejects_absolute_drive_unc_and_parent_escape(tmp_path, unsafe_path):
    guard, repository, _ = _guarded_repository(tmp_path)
    bound = guard.bind(repository)

    with pytest.raises(OutsideWorkspaceError):
        bound.resolve(unsafe_path, kind="file", must_exist=False)


def test_guard_rejects_broken_and_looping_paths_as_workspace_errors(tmp_path, monkeypatch):
    guard, repository, root = _guarded_repository(tmp_path)
    broken = root / "broken.py"
    broken.write_text("placeholder", encoding="utf-8")
    bound = guard.bind(repository)
    original_resolve = Path.resolve

    def looping_resolve(path, *args, **kwargs):
        if path == broken:
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", looping_resolve)

    with pytest.raises(WorkspacePathError, match="cannot resolve"):
        bound.resolve("broken.py", kind="file", must_exist=True)


def test_guard_rejects_child_that_resolves_outside_without_partial_iteration(
    tmp_path, monkeypatch
):
    guard, repository, root = _guarded_repository(tmp_path)
    (root / "a-safe.py").write_text("safe", encoding="utf-8")
    redirected = root / "z-secret.py"
    redirected.write_text("placeholder", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_secret = outside / "secret.py"
    outside_secret.write_text("OUTSIDE_SECRET", encoding="utf-8")
    bound = guard.bind(repository)
    original_resolve = Path.resolve
    outside_resolved = outside_secret.resolve()

    def redirected_resolve(path, *args, **kwargs):
        if path == redirected:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    with pytest.raises(OutsideWorkspaceError):
        bound.iter_files(".")


def test_guard_list_dir_fails_as_a_whole_for_redirected_entry(tmp_path, monkeypatch):
    guard, repository, root = _guarded_repository(tmp_path)
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    redirected = root / "secret.txt"
    redirected.write_text("placeholder", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    bound = guard.bind(repository)
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def redirected_resolve(path, *args, **kwargs):
        if path == redirected:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    with pytest.raises(OutsideWorkspaceError):
        bound.list_dir(".")


@pytest.mark.skipif(os.name == "nt", reason="POSIX file symlink semantics")
def test_guard_rejects_posix_file_symlink_to_outside(tmp_path):
    guard, repository, root = _guarded_repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    (root / "secret.txt").symlink_to(outside)
    bound = guard.bind(repository)

    with pytest.raises(OutsideWorkspaceError):
        bound.read_text("secret.txt")


def test_guard_returns_posix_relative_paths_for_safe_files(tmp_path):
    guard, repository, root = _guarded_repository(tmp_path)
    nested = root / "src" / "main.py"
    nested.parent.mkdir()
    nested.write_text("print('ok')", encoding="utf-8")
    bound = guard.bind(repository)

    files = bound.iter_files(".")

    assert [bound.relative_path(path) for path in files] == ["src/main.py"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_guard_rejects_windows_directory_junction_to_outside(tmp_path):
    guard, repository, root = _guarded_repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")
    junction = root / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    bound = guard.bind(repository)
    try:
        with pytest.raises(OutsideWorkspaceError):
            bound.list_dir(".")
        with pytest.raises(OutsideWorkspaceError):
            bound.iter_files(".")
    finally:
        os.rmdir(junction)
