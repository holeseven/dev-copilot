from engine.core.workspace.models import Repository
from engine.core.workspace.scanner import WorkspaceScanner


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
    scanner = WorkspaceScanner()
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

    result = WorkspaceScanner().scan(Repository(name="plain", path=str(repository)))

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

    result = WorkspaceScanner().scan(Repository(name="tracked", path=str(repository)))

    assert result.git.branch == "main"
    assert result.git.remote == "git@example.com:team/tracked.git"
