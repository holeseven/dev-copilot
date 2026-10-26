from pathlib import Path

import pytest

from engine.core.workspace.registry import WorkspaceRegistry


def test_registry_round_trip_preserves_workspace_and_repositories(tmp_path):
    source = tmp_path / "service"
    source.mkdir()
    registry = WorkspaceRegistry(root=tmp_path / "registry")

    workspace = registry.create("order-platform", "demo")
    registry.add_repository(workspace.id, "service", source)

    restored = WorkspaceRegistry(root=tmp_path / "registry")
    loaded = restored.get(workspace.id)

    assert loaded.name == "demo"
    assert [(repo.name, repo.path) for repo in loaded.repositories] == [
        ("service", str(source.resolve()))
    ]
    assert (tmp_path / "registry" / "workspaces" / "order-platform" / "workspace.yaml").is_file()


def test_registry_active_switch_and_delete_clears_active_without_deleting_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    registry = WorkspaceRegistry(root=tmp_path / "registry")
    first = registry.create("first", "first")
    second = registry.create("second", "second")
    registry.add_repository(first.id, "source", source)

    registry.set_active(first.id)
    assert registry.get_active().id == first.id
    registry.set_active(second.id)
    assert registry.get_active().id == second.id
    assert (tmp_path / "registry" / "active_workspace").read_text(encoding="utf-8") == "second"

    registry.delete(second.id)

    assert registry.get_active() is None
    assert not (tmp_path / "registry" / "active_workspace").exists()
    assert not (tmp_path / "registry" / "workspaces" / "second").exists()
    assert source.is_dir()
    assert [workspace.id for workspace in registry.list()] == [first.id]


def test_registry_rejects_missing_repository_and_duplicate_names_per_workspace(tmp_path):
    registry = WorkspaceRegistry(root=tmp_path / "registry")
    workspace = registry.create("demo", "demo")
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(ValueError, match="does not exist"):
        registry.add_repository(workspace.id, "missing", tmp_path / "missing")

    registry.add_repository(workspace.id, "api", existing)
    with pytest.raises(ValueError, match="already exists"):
        registry.add_repository(workspace.id, "api", existing)


def test_registry_allows_one_path_in_different_workspaces(tmp_path):
    source = tmp_path / "shared"
    source.mkdir()
    registry = WorkspaceRegistry(root=tmp_path / "registry")
    first = registry.create("first", "first")
    second = registry.create("second", "second")

    registry.add_repository(first.id, "api", source)
    registry.add_repository(second.id, "api", source)

    assert registry.get(first.id).repositories[0].path == registry.get(second.id).repositories[0].path


def test_registry_rejects_workspace_ids_that_escape_metadata_root(tmp_path):
    registry = WorkspaceRegistry(root=tmp_path / "registry")
    external = tmp_path / "external"
    external.mkdir()

    for workspace_id in ("../../external", str(external), ".", ".."):
        with pytest.raises(ValueError, match="workspace id"):
            registry.create(workspace_id, "outside")

    assert external.is_dir()
    assert list(external.iterdir()) == []
