import os
from pathlib import Path
import subprocess

import pytest

from engine.core.workspace.models import Repository
from engine.core.workspace.path_guard import OutsideWorkspaceError, WorkspacePathGuard
from engine.core.workspace.scanner import WorkspaceScanner


def _scanner(repository):
    return WorkspaceScanner(
        path_guard=WorkspacePathGuard.from_roots((repository,))
    )


def test_scan_is_deterministic_and_reports_manifest_config_and_compose_facts(tmp_path):
    repository = tmp_path / "catalog"
    repository.mkdir()
    (repository / "package.json").write_text(
        '{"name":"catalog-api","dependencies":{"express":"4.0.0","redis":"4.0.0"}}',
        encoding="utf-8",
    )
    (repository / "application.yml").write_text(
        "spring:\n  application:\n    name: catalog-service\n  data:\n    redis:\n      host: redis\n",
        encoding="utf-8",
    )
    (repository / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: catalog-api\n    depends_on:\n      - redis\n  redis:\n    image: redis:7\n",
        encoding="utf-8",
    )
    scanner = _scanner(repository)
    source = Repository(name="catalog", path=str(repository))

    first = scanner.scan(source)
    second = scanner.scan(source)

    assert first == second
    assert first.languages == ["JavaScript"]
    assert first.build_systems == ["npm"]
    assert first.services == ["api", "catalog-api", "catalog-service", "redis"]
    assert first.middleware == ["Redis"]
    assert [(item.source, item.target) for item in first.dependencies] == [
        ("api", "redis"),
        ("catalog-api", "express"),
        ("catalog-api", "redis"),
    ]
    assert {item.source for item in first.evidence} == {
        "application.yml",
        "docker-compose.yml",
        "package.json",
    }


def test_scan_does_not_infer_dependencies_from_ordinary_source_strings(tmp_path):
    repository = tmp_path / "plain"
    repository.mkdir()
    (repository / "app.py").write_text('message = "postgres redis depends_on"\n', encoding="utf-8")

    result = _scanner(repository).scan(Repository(name="plain", path=str(repository)))

    assert result.dependencies == []
    assert result.evidence == []


def test_scan_reads_git_branch_and_origin_from_repository_metadata(tmp_path):
    repository = tmp_path / "tracked"
    git = repository / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "config").write_text(
        '[remote "origin"]\n\turl = git@example.com:team/tracked.git\n', encoding="utf-8"
    )

    result = _scanner(repository).scan(Repository(name="tracked", path=str(repository)))

    assert result.git.branch == "main"
    assert result.git.remote == "git@example.com:team/tracked.git"


def test_scan_detects_database_from_application_jdbc_url_and_keeps_url_evidence(tmp_path):
    repository = tmp_path / "billing"
    repository.mkdir()
    (repository / "application.yaml").write_text(
        "spring:\n  datasource:\n    url: jdbc:postgresql://db:5432/billing\n",
        encoding="utf-8",
    )

    result = _scanner(repository).scan(Repository(name="billing", path=str(repository)))

    assert result.middleware == ["PostgreSQL"]
    assert ("application.yaml", "spring.datasource.url") in {
        (item.source, item.detail) for item in result.evidence
    }


def test_scan_rejects_git_metadata_that_resolves_outside(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    repository = allowed / "service"
    git = repository / ".git"
    git.mkdir(parents=True)
    head = git / "HEAD"
    head.write_text("ref: refs/heads/main\n", encoding="utf-8")
    outside = tmp_path / "outside-head"
    outside.write_text("ref: refs/heads/secret\n", encoding="utf-8")
    guard = WorkspacePathGuard.from_roots((allowed,))
    scanner = WorkspaceScanner(path_guard=guard)
    original_resolve = Path.resolve
    outside_resolved = outside.resolve()

    def redirect_head(path, *args, **kwargs):
        if path == head:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirect_head)

    with pytest.raises(OutsideWorkspaceError):
        scanner.scan(Repository(name="service", path=str(repository)))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_scan_rejects_real_git_junction_to_outside(tmp_path):
    allowed = tmp_path / "allowed"
    repository = allowed / "service"
    repository.mkdir(parents=True)
    outside_git = tmp_path / "outside-git"
    outside_git.mkdir()
    (outside_git / "HEAD").write_text(
        "ref: refs/heads/secret\n", encoding="utf-8"
    )
    junction = repository / ".git"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_git)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    scanner = WorkspaceScanner(
        path_guard=WorkspacePathGuard.from_roots((allowed,))
    )
    try:
        with pytest.raises(OutsideWorkspaceError):
            scanner.scan(Repository(name="service", path=str(repository)))
    finally:
        os.rmdir(junction)
