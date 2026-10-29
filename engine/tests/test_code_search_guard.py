import os
from pathlib import Path
import subprocess

import pytest

from engine.core.mcp.code_search_server import CodeSearchServer
from engine.core.workspace.models import Repository
from engine.core.workspace.path_guard import OutsideWorkspaceError, WorkspacePathGuard


def _server(tmp_path):
    allowed = tmp_path / "allowed"
    repository = allowed / "service"
    repository.mkdir(parents=True)
    guard = WorkspacePathGuard.from_roots((allowed,))
    bound = guard.bind(Repository(name="service", path=str(repository)))
    return CodeSearchServer(bound), repository


@pytest.mark.asyncio
async def test_code_search_rejects_outside_ripgrep_match_without_partial_results(
    tmp_path, monkeypatch
):
    server, repository = _server(tmp_path)
    inside = repository / "inside.py"
    inside.write_text("needle", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")

    async def fake_ripgrep(*args, **kwargs):
        return {
            "matches": [
                {"file": str(inside), "line_number": 1, "content": "needle"},
                {"file": str(outside), "line_number": 1, "content": "OUTSIDE_SECRET"},
            ],
            "total": 2,
            "engine": "ripgrep",
            "pattern": "needle",
        }

    monkeypatch.setattr(server, "_search_with_ripgrep", fake_ripgrep)

    with pytest.raises(OutsideWorkspaceError):
        await server.tools_call("code_search", {"pattern": "needle"})


@pytest.mark.asyncio
@pytest.mark.parametrize("ripgrep_finds_safe_match", [True, False])
async def test_code_search_preflights_tree_before_accepting_ripgrep_result(
    tmp_path, monkeypatch, ripgrep_finds_safe_match
):
    server, repository = _server(tmp_path)
    inside = repository / "inside.py"
    inside.write_text("needle", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("OUTSIDE_SECRET", encoding="utf-8")
    linked = repository / "linked"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    else:
        linked.symlink_to(outside, target_is_directory=True)

    matches = (
        [{"file": str(inside), "line_number": 1, "content": "needle"}]
        if ripgrep_finds_safe_match
        else []
    )

    async def fake_ripgrep(*args, **kwargs):
        return {
            "matches": matches,
            "total": len(matches),
            "engine": "ripgrep",
            "pattern": "needle",
        }

    monkeypatch.setattr(server, "_search_with_ripgrep", fake_ripgrep)

    try:
        with pytest.raises(OutsideWorkspaceError):
            await server.tools_call("code_search", {"pattern": "needle"})
    finally:
        if os.name == "nt":
            os.rmdir(linked)
        else:
            linked.unlink()


@pytest.mark.asyncio
async def test_code_search_fallback_uses_guarded_iteration_and_posix_paths(
    tmp_path, monkeypatch
):
    server, repository = _server(tmp_path)
    source = repository / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("needle = True\n", encoding="utf-8")

    async def no_ripgrep(*args, **kwargs):
        return None

    monkeypatch.setattr(server, "_search_with_ripgrep", no_ripgrep)

    result = await server.tools_call(
        "code_search", {"pattern": "needle", "file_glob": "*.py"}
    )

    assert result["matches"] == [
        {"file": "src/main.py", "line_number": 1, "content": "needle = True"}
    ]


@pytest.mark.asyncio
async def test_code_search_fallback_rejects_redirected_file(tmp_path, monkeypatch):
    server, repository = _server(tmp_path)
    redirected = repository / "secret.py"
    redirected.write_text("placeholder", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    async def no_ripgrep(*args, **kwargs):
        return None

    def redirect_secret(path, *args, **kwargs):
        if path == redirected:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(server, "_search_with_ripgrep", no_ripgrep)
    monkeypatch.setattr(Path, "resolve", redirect_secret)

    with pytest.raises(OutsideWorkspaceError):
        await server.tools_call("code_search", {"pattern": "secret"})
