# @author lxy
from pathlib import Path
import json
import xml.etree.ElementTree as ElementTree

import yaml
import tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_ci_runs_the_cross_stack_gate_with_least_privilege():
    workflow_path = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.load(workflow_path.read_text("utf-8"), Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "true"

    job = workflow["jobs"]["verify"]
    assert job["timeout-minutes"] == "30"
    steps = job["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses == [
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e",
        "actions/setup-node@v7",
        "actions/setup-java@v6",
    ]
    checkout = steps[0]
    assert checkout["with"]["persist-credentials"] == "false"
    assert any(
        step.get("run") == "uv sync --project engine --frozen --extra dev"
        for step in steps
    )
    assert any(
        step.get("run") == "npm ci"
        and step.get("working-directory") == "web"
        for step in steps
    )
    assert steps[-1]["run"] == "python scripts/verify.py"


def test_repository_contains_standard_mit_license_for_lxy():
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text("utf-8")

    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 lxy" in license_text
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in license_text


def test_public_contribution_and_security_policies_are_release_ready():
    contributing = (REPOSITORY_ROOT / "CONTRIBUTING.md").read_text("utf-8")
    security = (REPOSITORY_ROOT / "SECURITY.md").read_text("utf-8")

    assert "python scripts/verify.py" in contributing
    assert "WORKSPACE_WRITE_ENABLED" in contributing
    assert "不要提交" in contributing
    assert "GitHub Security Advisory" in security
    assert "不要在公开 Issue" in security
    assert "本地单用户" in security


def test_public_release_manifest_defines_reproducible_acceptance_and_rollback():
    release = (REPOSITORY_ROOT / "RELEASE.md").read_text("utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text("utf-8")

    assert "0.1.0" in release
    assert "python scripts/verify.py" in release
    assert "docker compose config" in release
    assert "WORKSPACE_WRITE_ENABLED=false" in release
    assert "回滚" in release
    assert "RELEASE.md" in readme


def test_release_version_is_consistent_across_all_components():
    engine = tomllib.loads(
        (REPOSITORY_ROOT / "engine/pyproject.toml").read_text("utf-8")
    )
    root_package = json.loads((REPOSITORY_ROOT / "package.json").read_text("utf-8"))
    web_package = json.loads(
        (REPOSITORY_ROOT / "web/package.json").read_text("utf-8")
    )
    java_project = ElementTree.fromstring(
        (REPOSITORY_ROOT / "java-service/pom.xml").read_text("utf-8")
    )
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}

    assert {
        engine["project"]["version"],
        root_package["version"],
        web_package["version"],
        java_project.findtext("m:version", namespaces=namespace),
    } == {"0.1.0"}


def test_readme_describes_ci_license_and_current_rag_status():
    readme = (REPOSITORY_ROOT / "README.md").read_text("utf-8")

    assert "python scripts/verify.py" in readme
    assert "Python 3.12" in readme
    assert "Node.js 20.19" in readme
    assert "Java 17" in readme
    assert "GitHub Actions" in readme
    assert "[MIT License](LICENSE)" in readme
    assert "`engine/core/rag`、`engine/core/openspec`" not in readme
