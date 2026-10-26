# @author lxy
"""Contracts for the dependency-free root frontend test entry point."""

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_root_package_exposes_the_supported_frontend_test_entry() -> None:
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text("utf-8"))

    assert package["private"] is True
    assert package["author"] == "lxy"
    assert package["engines"]["node"] == ">=20 <21"
    assert package["scripts"]["test:frontend"] == (
        "node engine/tests/frontend_stream_switch.test.js"
    )
    assert package["scripts"]["test"] == "npm run test:frontend"
    assert "dependencies" not in package
    assert "devDependencies" not in package


def test_node_version_and_lockfile_match_the_root_package() -> None:
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text("utf-8"))
    lock = json.loads((REPOSITORY_ROOT / "package-lock.json").read_text("utf-8"))

    assert (REPOSITORY_ROOT / ".nvmrc").read_text("utf-8").strip() == "20"
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["name"] == package["name"]
    assert lock["packages"][""]["version"] == package["version"]
    assert lock["packages"][""]["engines"] == package["engines"]
    assert len(lock["packages"]) == 1
