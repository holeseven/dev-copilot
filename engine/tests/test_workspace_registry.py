import os
from pathlib import Path

import pytest

from engine.core.workspace.models import Repository
from engine.core.workspace import registry as registry_module
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


def test_registry_allows_repository_inside_allowed_root_and_root_itself(tmp_path):
    allowed_root = tmp_path / "allowed"
    nested = allowed_root / "service"
    nested.mkdir(parents=True)
    registry = WorkspaceRegistry(
        root=tmp_path / "registry",
        allowed_roots=(allowed_root,),
    )
    workspace = registry.create("demo", "demo")

    registry.add_repository(workspace.id, "root", allowed_root)
    workspace = registry.add_repository(workspace.id, "nested", nested)

    assert [repository.path for repository in workspace.repositories] == [
        str(allowed_root.resolve()),
        str(nested.resolve()),
    ]


@pytest.mark.parametrize("directory_name", ["outside", "allowed-neighbour"])
def test_registry_rejects_repository_outside_allowed_root(tmp_path, directory_name):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    rejected = tmp_path / directory_name
    rejected.mkdir()
    registry = WorkspaceRegistry(
        root=tmp_path / "registry",
        allowed_roots=(allowed_root,),
    )
    workspace = registry.create("demo", "demo")

    with pytest.raises(ValueError, match="outside allowed roots"):
        registry.add_repository(workspace.id, "rejected", rejected)


def test_registry_with_empty_allowed_roots_rejects_every_repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    registry = WorkspaceRegistry(root=tmp_path / "registry", allowed_roots=())
    workspace = registry.create("demo", "demo")

    with pytest.raises(ValueError, match="outside allowed roots"):
        registry.add_repository(workspace.id, "source", source)


@pytest.mark.parametrize("invalid_kind", ["outside", "missing", "file"])
def test_registry_fails_closed_when_persisted_repository_is_invalid(tmp_path, invalid_kind):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    if invalid_kind == "outside":
        invalid_path = tmp_path / "outside"
        invalid_path.mkdir()
    elif invalid_kind == "missing":
        invalid_path = allowed_root / "missing"
        invalid_path.mkdir()
    else:
        invalid_path = allowed_root / "file.txt"
        invalid_path.mkdir()

    metadata_root = tmp_path / "registry"
    registry = WorkspaceRegistry(root=metadata_root)
    workspace = registry.create("demo", "demo")
    registry.add_repository(workspace.id, "invalid", invalid_path)
    if invalid_kind == "missing":
        invalid_path.rmdir()
    elif invalid_kind == "file":
        invalid_path.rmdir()
        invalid_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="persisted repository.*(?:outside allowed roots|does not exist|not a directory)"):
        WorkspaceRegistry(root=metadata_root, allowed_roots=(allowed_root,))


def test_registry_rejects_symlink_that_resolves_outside_allowed_root(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed_root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    if not link.exists():
        pytest.skip("directory symlink could not be created")

    registry = WorkspaceRegistry(root=tmp_path / "registry", allowed_roots=(allowed_root,))
    workspace = registry.create("demo", "demo")

    with pytest.raises(ValueError, match="outside allowed roots"):
        registry.add_repository(workspace.id, "escape", link)


def test_registry_results_are_detached_from_internal_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    registry = WorkspaceRegistry(root=tmp_path / "registry")

    created = registry.create("demo", "Demo")
    created.name = "mutated create"
    added = registry.add_repository("demo", "source", source)
    added.repositories.clear()
    fetched = registry.get("demo")
    fetched.name = "mutated get"
    listed = registry.list()[0]
    listed.repositories.clear()
    active = registry.set_active("demo")
    active.name = "mutated active"
    restored_active = registry.get_active()
    restored_active.repositories.clear()

    internal_view = registry.get("demo")
    assert internal_view.name == "Demo"
    assert [(repository.name, repository.path) for repository in internal_view.repositories] == [
        ("source", str(source.resolve()))
    ]


def test_registry_save_rejects_invalid_copy_before_mutating_memory_or_disk(tmp_path):
    allowed_root = tmp_path / "allowed"
    source = allowed_root / "source"
    source.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    metadata_root = tmp_path / "registry"
    registry = WorkspaceRegistry(metadata_root, allowed_roots=(allowed_root,))
    registry.create("demo", "Demo")
    registry.add_repository("demo", "source", source)
    before = (metadata_root / "workspaces" / "demo" / "workspace.yaml").read_text(
        encoding="utf-8"
    )
    changed = registry.get("demo")
    changed.repositories.append(Repository(name="outside", path=str(outside)))

    with pytest.raises(ValueError, match="outside allowed roots"):
        registry.save(changed)

    assert [repository.name for repository in registry.get("demo").repositories] == ["source"]
    assert (metadata_root / "workspaces" / "demo" / "workspace.yaml").read_text(
        encoding="utf-8"
    ) == before


def test_registry_revalidates_repository_after_root_target_changes(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    repository_root = allowed_root / "source"
    repository_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    registry = WorkspaceRegistry(tmp_path / "registry", allowed_roots=(allowed_root,))
    registry.create("demo", "Demo")
    registry.add_repository("demo", "source", repository_root)
    repository = registry.get("demo").repositories[0]
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def replaced_root(path, *args, **kwargs):
        if path == repository_root:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", replaced_root)

    with pytest.raises(ValueError, match="outside allowed roots"):
        registry.validate_repository(repository)


def test_registry_wraps_resolve_failures_as_value_error(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    registry = WorkspaceRegistry(tmp_path / "registry", allowed_roots=(tmp_path,))
    workspace = registry.create("demo", "Demo")
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == source:
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)

    with pytest.raises(ValueError, match="cannot resolve repository path"):
        registry.add_repository(workspace.id, "source", source)


def test_registry_wraps_metadata_resolve_failures_as_value_error(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "registry")
    metadata_root = registry._workspaces_root
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == metadata_root:
            raise OSError("unreadable reparse point")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)

    with pytest.raises(ValueError, match="cannot resolve workspace metadata path"):
        registry.create("demo", "Demo")


@pytest.mark.parametrize("failure_stage", ["fsync", "replace"])
def test_registry_save_io_failure_preserves_memory_and_yaml(
    tmp_path, monkeypatch, failure_stage
):
    registry = WorkspaceRegistry(tmp_path / "registry")
    registry.create("demo", "Original")
    workspace_file = tmp_path / "registry" / "workspaces" / "demo" / "workspace.yaml"
    original_yaml = workspace_file.read_text(encoding="utf-8")
    changed = registry.get("demo")
    changed.name = "Changed"

    def fail(*args, **kwargs):
        raise OSError(f"{failure_stage} failed")

    monkeypatch.setattr(os, failure_stage, fail)

    with pytest.raises(OSError, match=failure_stage):
        registry.save(changed)

    assert registry.get("demo").name == "Original"
    assert workspace_file.read_text(encoding="utf-8") == original_yaml
    assert list(workspace_file.parent.glob("*.tmp")) == []


def test_registry_save_serialization_failure_preserves_memory_and_yaml(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "registry")
    registry.create("demo", "Original")
    workspace_file = tmp_path / "registry" / "workspaces" / "demo" / "workspace.yaml"
    original_yaml = workspace_file.read_text(encoding="utf-8")
    changed = registry.get("demo")
    changed.name = "Changed"

    def fail_serialization(*args, **kwargs):
        raise ValueError("serialization failed")

    monkeypatch.setattr(registry_module.yaml, "safe_dump", fail_serialization)

    with pytest.raises(ValueError, match="serialization failed"):
        registry.save(changed)

    assert registry.get("demo").name == "Original"
    assert workspace_file.read_text(encoding="utf-8") == original_yaml


def test_registry_save_write_failure_preserves_memory_and_yaml(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "registry")
    registry.create("demo", "Original")
    workspace_file = tmp_path / "registry" / "workspaces" / "demo" / "workspace.yaml"
    original_yaml = workspace_file.read_text(encoding="utf-8")
    changed = registry.get("demo")
    changed.name = "Changed"
    original_fdopen = registry_module.os.fdopen

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.stream.close()

        def write(self, payload):
            raise OSError("write failed")

    def failing_fdopen(*args, **kwargs):
        return FailingWriter(original_fdopen(*args, **kwargs))

    monkeypatch.setattr(registry_module.os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="write failed"):
        registry.save(changed)

    assert registry.get("demo").name == "Original"
    assert workspace_file.read_text(encoding="utf-8") == original_yaml
    assert list(workspace_file.parent.glob("*.tmp")) == []
