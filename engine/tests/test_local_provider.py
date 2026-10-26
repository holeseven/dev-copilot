import pytest

from engine.core.mcp.manager import MCPManager
from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.workspace.models import Repository, Workspace


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


@pytest.mark.asyncio
async def test_local_provider_exposes_only_read_only_tools_with_matching_parameters(tmp_path):
    provider = LocalDevToolProvider("local", _workspace(tmp_path))

    schemas = await provider.list_tools()

    assert [schema["name"] for schema in schemas] == [
        "list_dir", "find_files", "grep_code", "read_file", "code_search"
    ]
    assert "workspace_id" not in str(schemas)
    assert "write_file" not in str(schemas)
    assert set(schemas[0]["parameters"]["properties"]) == {"path"}
    assert set(schemas[1]["parameters"]["properties"]) == {"glob_pattern", "path"}


@pytest.mark.asyncio
async def test_local_provider_uses_virtual_root_for_multi_repository_reads_and_searches(tmp_path):
    provider = LocalDevToolProvider("local", _workspace(tmp_path))

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
    provider = LocalDevToolProvider("local", _workspace(tmp_path))

    entries = await provider.call_tool("list_dir", {"path": "api/subdir"})

    assert entries == ["api/subdir/nested.py"]
    assert await provider.call_tool("read_file", {"path": entries[0]}) == "needle = 'nested'\n"


@pytest.mark.asyncio
async def test_local_provider_rewrites_missing_paths_without_host_absolute_paths(tmp_path):
    provider = LocalDevToolProvider("local", _workspace(tmp_path))

    with pytest.raises(FileNotFoundError) as missing_file:
        await provider.call_tool("read_file", {"path": "api/missing.py"})
    missing_search = await provider.call_tool("code_search", {"pattern": "needle", "path": "api/missing"})

    assert "api/missing.py" in str(missing_file.value)
    assert str(tmp_path) not in str(missing_file.value)
    assert missing_search["error"] == "path does not exist: api/missing"
    assert str(tmp_path) not in str(missing_search)


@pytest.mark.asyncio
async def test_local_provider_rejects_escape_and_similarly_prefixed_sibling(tmp_path):
    provider = LocalDevToolProvider("local", _workspace(tmp_path))

    with pytest.raises(PermissionError):
        await provider.call_tool("read_file", {"path": "../api-other/secret.txt"})
    with pytest.raises(PermissionError):
        await provider.call_tool("read_file", {"path": "api/../api-other/secret.txt"})


@pytest.mark.asyncio
async def test_manager_dispatches_local_provider_once_when_it_raises(tmp_path):
    provider = LocalDevToolProvider("local", _workspace(tmp_path))
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
