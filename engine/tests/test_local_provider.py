from pathlib import Path

import pytest

from engine.core.mcp.manager import MCPManager
from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.path_guard import WorkspacePathGuard


def _workspace(tmp_path):
    api = tmp_path / "api"
    web = tmp_path / "web"
    sibling = tmp_path / "api-other"
    for directory in (api, web, sibling):
        directory.mkdir()
    (api / "main.py").write_text("needle = 'api'\n", encoding="utf-8")
    (api / "subdir").mkdir()
    (api / "subdir" / "nested.py").write_text("needle = 'nested'\n", encoding="utf-8")
    (web / "index.js").write_text("const needle = 'web';\n", encoding="utf-8")
    (sibling / "secret.txt").write_text("secret\n", encoding="utf-8")
    return Workspace(
        id="workspace-1",
        name="demo",
        repositories=[
            Repository(name="api", path=str(api)),
            Repository(name="web", path=str(web)),
        ],
    )


def _provider(tmp_path):
    return LocalDevToolProvider(
        "local",
        _workspace(tmp_path),
        path_guard=WorkspacePathGuard.from_roots((tmp_path,)),
    )


@pytest.mark.asyncio
async def test_local_provider_exposes_read_tools_and_controlled_change_tools(tmp_path):
    provider = _provider(tmp_path)

    schemas = await provider.list_tools()

    assert [schema["name"] for schema in schemas] == [
        "list_dir",
        "find_files",
        "grep_code",
        "read_file",
        "code_search",
        "preview_edit_file",
        "preview_create_file",
        "apply_workspace_change",
    ]
    assert "workspace_id" not in str(schemas)
    assert "write_file" not in str(schemas)
    assert "run_command" not in str(schemas)
    assert set(schemas[0]["parameters"]["properties"]) == {"path"}
    assert set(schemas[1]["parameters"]["properties"]) == {"glob_pattern", "path"}
    assert all(schema["parameters"]["additionalProperties"] is False for schema in schemas)


@pytest.mark.asyncio
async def test_controlled_change_requires_approved_provider_path(tmp_path):
    class ChangeService:
        def preview_replace(self, path, old_text, new_text, profile):
            return {"status": "previewed", "proposal_id": "proposal-1"}

        def approval_request(self, proposal_id, patch_sha256):
            return type(
                "Request",
                (),
                {
                    "approval_id": proposal_id,
                    "action": "apply_workspace_change",
                    "payload": {
                        "tool_name": "apply_workspace_change",
                        "virtual_path": "api/main.py",
                        "diff": "safe diff",
                        "patch_sha256": patch_sha256,
                    },
                },
            )()

        async def apply_approved(self, proposal_id, patch_sha256, approval_id):
            assert approval_id == proposal_id
            return {"status": "applied_and_tests_passed"}

    provider = LocalDevToolProvider(
        "local",
        _workspace(tmp_path),
        path_guard=WorkspacePathGuard.from_roots((tmp_path,)),
        change_service=ChangeService(),
    )
    arguments = {"proposal_id": "proposal-1", "patch_sha256": "hash-1"}

    with pytest.raises(PermissionError, match="approved"):
        await provider.call_tool("apply_workspace_change", arguments)
    approval = await provider.prepare_approval("apply_workspace_change", arguments)
    result = await provider.call_approved_tool(
        "apply_workspace_change", arguments, approval.approval_id
    )

    assert approval.approval_id == "proposal-1"
    assert set(approval.payload) == {
        "tool_name", "virtual_path", "diff", "patch_sha256"
    }
    assert result == {"status": "applied_and_tests_passed"}


@pytest.mark.asyncio
async def test_local_provider_uses_virtual_root_for_multi_repository_reads_and_searches(tmp_path):
    provider = _provider(tmp_path)

    assert await provider.call_tool("list_dir", {"path": "."}) == ["api", "web"]
    api_entries = await provider.call_tool("list_dir", {"path": "api"})
    assert api_entries == ["api/main.py", "api/subdir/"]
    assert await provider.call_tool("read_file", {"path": api_entries[0]}) == "needle = 'api'\n"
    assert await provider.call_tool("read_file", {"path": "api/main.py"}) == "needle = 'api'\n"
    assert await provider.call_tool("find_files", {"glob_pattern": "*.py", "path": "."}) == [
        "api/main.py",
        "api/subdir/nested.py",
    ]
    assert await provider.call_tool("grep_code", {"pattern": "needle", "path": "."}) == [
        "api/main.py:1:needle = 'api'",
        "api/subdir/nested.py:1:needle = 'nested'",
        "web/index.js:1:const needle = 'web';",
    ]
    search = await provider.call_tool("code_search", {"pattern": "needle", "path": "."})
    assert [item["file"] for item in search["matches"]] == [
        "api/main.py",
        "api/subdir/nested.py",
        "web/index.js",
    ]
    assert str(tmp_path) not in str(search)


@pytest.mark.asyncio
async def test_local_provider_preserves_nested_directory_in_listed_virtual_paths(tmp_path):
    provider = _provider(tmp_path)

    entries = await provider.call_tool("list_dir", {"path": "api/subdir"})

    assert entries == ["api/subdir/nested.py"]
    assert await provider.call_tool("read_file", {"path": entries[0]}) == "needle = 'nested'\n"


@pytest.mark.asyncio
async def test_local_provider_rewrites_missing_paths_without_host_absolute_paths(tmp_path):
    provider = _provider(tmp_path)

    with pytest.raises(FileNotFoundError) as missing_file:
        await provider.call_tool("read_file", {"path": "api/missing.py"})
    missing_search = await provider.call_tool("code_search", {"pattern": "needle", "path": "api/missing"})

    assert "api/missing.py" in str(missing_file.value)
    assert str(tmp_path) not in str(missing_file.value)
    assert missing_search["error"] == "path does not exist: api/missing"
    assert str(tmp_path) not in str(missing_search)


@pytest.mark.asyncio
async def test_local_provider_rejects_escape_and_similarly_prefixed_sibling(tmp_path):
    provider = _provider(tmp_path)

    with pytest.raises(PermissionError):
        await provider.call_tool("read_file", {"path": "../api-other/secret.txt"})
    with pytest.raises(PermissionError):
        await provider.call_tool("read_file", {"path": "api/../api-other/secret.txt"})


@pytest.mark.asyncio
async def test_manager_dispatches_local_provider_once_when_it_raises(tmp_path):
    provider = _provider(tmp_path)
    manager = MCPManager([{"name": "local", "type": "local", "provider": provider}])
    await manager.init_all()
    calls = 0

    async def fail_once(name, arguments):
        nonlocal calls
        calls += 1
        raise PermissionError("outside workspace")

    provider.call_tool = fail_once
    result = await manager.call_tool("read_file", {"path": "../outside"})
    await manager.shutdown()

    assert calls == 1
    assert "outside workspace" in result


@pytest.mark.asyncio
async def test_repeated_manager_init_reuses_and_shutdown_closes_heal_task():
    manager = MCPManager([])

    await manager.init_all()
    first_task = manager._heal_task
    await manager.init_all()
    second_task = manager._heal_task
    await manager.shutdown()

    assert first_task is second_task
    assert first_task is not None
    assert first_task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("read_file", {"path": "api/main.py"}),
        ("find_files", {"glob_pattern": "*.py", "path": "."}),
    ],
)
async def test_local_provider_revalidates_repository_roots_before_each_operation(
    tmp_path, monkeypatch, tool_name, arguments
):
    workspace = _workspace(tmp_path)
    allowed_root = tmp_path / "api"
    outside = tmp_path / "outside"
    outside.mkdir()
    provider = LocalDevToolProvider(
        "local",
        Workspace(
            id="workspace-1",
            name="demo",
            repositories=[workspace.repositories[0]],
        ),
        path_guard=WorkspacePathGuard.from_roots((allowed_root,)),
    )
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def replaced_root(path, *args, **kwargs):
        if path == allowed_root:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", replaced_root)

    with pytest.raises(ValueError, match="outside allowed roots"):
        await provider.call_tool(tool_name, arguments)


@pytest.mark.asyncio
async def test_local_provider_uses_frozen_validated_root_without_second_root_resolve(
    tmp_path, monkeypatch
):
    workspace = _workspace(tmp_path)
    repository_root = tmp_path / "api"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.py").write_text("OUTSIDE_SECRET\n", encoding="utf-8")
    provider = LocalDevToolProvider(
        "local",
        Workspace(
            id="workspace-1",
            name="demo",
            repositories=[workspace.repositories[0]],
        ),
        path_guard=WorkspacePathGuard.from_roots((repository_root,)),
    )
    original_resolve = Path.resolve
    canonical_root = repository_root.resolve()
    outside_root = outside.resolve()
    root_resolves = 0

    def switching_resolve(path, *args, **kwargs):
        nonlocal root_resolves
        if path == repository_root or path == canonical_root:
            root_resolves += 1
            return canonical_root if root_resolves == 1 else outside_root
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", switching_resolve)

    content = await provider.call_tool("read_file", {"path": "api/main.py"})

    assert content == "needle = 'api'\n"
    assert root_resolves == 1


@pytest.mark.asyncio
async def test_local_provider_virtual_root_fails_closed_when_repository_disappears(tmp_path):
    workspace = _workspace(tmp_path)
    provider = LocalDevToolProvider(
        "local", workspace, path_guard=WorkspacePathGuard.from_roots((tmp_path,))
    )
    repository_root = tmp_path / "api"
    repository_root.rename(tmp_path / "api-removed")

    with pytest.raises(ValueError, match="does not exist"):
        await provider.call_tool("list_dir", {"path": "."})


@pytest.mark.asyncio
async def test_local_provider_virtual_root_revalidates_redirected_repositories(
    tmp_path, monkeypatch
):
    workspace = _workspace(tmp_path)
    repository_root = tmp_path / "api"
    outside = tmp_path / "outside"
    outside.mkdir()
    provider = LocalDevToolProvider(
        "local",
        Workspace(
            id="workspace-1",
            name="demo",
            repositories=[workspace.repositories[0]],
        ),
        path_guard=WorkspacePathGuard.from_roots((repository_root,)),
    )
    original_resolve = Path.resolve
    outside_root = outside.resolve()

    def redirected_root(path, *args, **kwargs):
        if path == repository_root:
            return outside_root
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_root)

    with pytest.raises(ValueError, match="outside allowed roots"):
        await provider.call_tool("list_dir", {"path": "."})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("find_files", {"glob_pattern": "*.py", "path": "."}),
        ("grep_code", {"pattern": "secret", "path": "."}),
    ],
)
async def test_local_provider_searches_fail_without_partial_results_on_redirect(
    tmp_path, monkeypatch, tool_name, arguments
):
    workspace = _workspace(tmp_path)
    repository_root = tmp_path / "api"
    redirected = repository_root / "z-secret.py"
    redirected.write_text("placeholder", encoding="utf-8")
    outside = tmp_path / "outside-secret.py"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    provider = LocalDevToolProvider(
        "local",
        Workspace(
            id="workspace-1",
            name="demo",
            repositories=[workspace.repositories[0]],
        ),
        path_guard=WorkspacePathGuard.from_roots((repository_root,)),
    )
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def redirect_secret(path, *args, **kwargs):
        if path == redirected:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirect_secret)

    with pytest.raises(ValueError, match="outside repository"):
        await provider.call_tool(tool_name, arguments)
