# @author lxy
"""Contracts for the reproducible Python 3.12 toolchain."""

from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 is used only for legacy regression checks.
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPOSITORY_ROOT / "engine"


def test_project_requires_exactly_python_312_minor_line() -> None:
    project = tomllib.loads((ENGINE_ROOT / "pyproject.toml").read_text("utf-8"))

    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert (REPOSITORY_ROOT / ".python-version").read_text("utf-8").strip() == "3.12"


def test_uv_lock_is_present_and_contains_the_local_project() -> None:
    lock_text = (ENGINE_ROOT / "uv.lock").read_text("utf-8")
    lock = tomllib.loads(lock_text)

    assert isinstance(lock["version"], int)
    assert lock["requires-python"] == "==3.12.*"
    assert "mirrors.aliyun.com" not in lock_text
    assert {
        package["source"]["registry"]
        for package in lock["package"]
        if "registry" in package.get("source", {})
    } == {"https://pypi.org/simple"}
    assert any(
        package["name"] == "dev-copilot-engine"
        and package.get("source") == {"editable": "."}
        for package in lock["package"]
    )


def test_engine_image_installs_the_frozen_lock_with_valid_repo_layout() -> None:
    dockerfile = (ENGINE_ROOT / "Dockerfile").read_text("utf-8")

    assert "WORKDIR /app/engine" in dockerfile
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert 'PATH="/app/engine/.venv/bin:$PATH"' in dockerfile
    assert "uv pip install" not in dockerfile


def test_readme_uses_uv_with_python_312_for_local_development() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text("utf-8")

    assert "Python 3.12" in readme
    assert "uv python install 3.12" in readme
    assert "uv sync --project engine --frozen --extra dev" in readme
    assert "uv run --project engine --frozen uvicorn" in readme
    assert (
        "uv run --project engine --frozen --extra dev python scripts/verify.py"
        in readme
    )
    assert "uv run --project engine --frozen --extra dev pytest" not in readme
    assert "pip install -e" not in readme
