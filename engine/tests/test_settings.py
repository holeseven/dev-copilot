# @author lxy
"""Security-sensitive application setting tests."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.config.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep developer machine environment and .env files out of these tests."""

    setting_names = {
        "APP_HOST",
        "WORKSPACE_DIR",
        "WORKSPACE_METADATA_DIR",
        "WORKSPACE_ALLOWED_ROOTS",
    }
    normalized_setting_names = {name.casefold() for name in setting_names}
    for name in list(os.environ):
        if name.casefold() in normalized_setting_names:
            monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_secure_local_defaults_are_backward_compatible() -> None:
    settings = make_settings()

    assert settings.app_host == "127.0.0.1"
    assert settings.workspace_metadata_path == Path("~/.dev-copilot").expanduser().resolve()
    assert settings.workspace_allowed_root_paths == (
        Path(settings.workspace_dir).expanduser().resolve(),
    )


def test_allowed_roots_accept_json_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", json.dumps([str(first), str(second)]))

    settings = make_settings()

    assert settings.workspace_allowed_root_paths == (first.resolve(), second.resolve())


def test_allowed_roots_accept_platform_path_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(
        "WORKSPACE_ALLOWED_ROOTS",
        os.pathsep.join((str(first), str(second))),
    )

    settings = make_settings()

    assert settings.workspace_allowed_root_paths == (first.resolve(), second.resolve())


def test_json_windows_path_is_not_split_on_drive_colon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows_path = str(tmp_path) if os.name == "nt" else r"C:\projects\dev-copilot"
    if os.name != "nt":
        monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", json.dumps([windows_path]))

    if os.name == "nt":
        expected = Path(windows_path).resolve()
        settings = make_settings()
        assert settings.workspace_allowed_root_paths == (expected,)
    else:
        with pytest.raises(ValueError, match="does not exist") as error:
            make_settings()
        assert windows_path in str(error.value)


@pytest.mark.parametrize("value", ["[", '["unfinished"'])
def test_malformed_allowed_roots_json_is_rejected(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", value)

    with pytest.raises(ValidationError, match="contains invalid JSON"):
        make_settings()


def test_json_allowed_roots_reject_non_string_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", json.dumps([str(tmp_path), 7]))

    with pytest.raises(ValidationError, match="must contain only directory strings"):
        make_settings()


def test_duplicate_allowed_roots_are_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "WORKSPACE_ALLOWED_ROOTS",
        json.dumps([str(tmp_path), str(tmp_path)]),
    )

    settings = make_settings()

    assert settings.workspace_allowed_root_paths == (tmp_path.resolve(),)


@pytest.mark.parametrize("value", ["", "   ", "[]"])
def test_explicit_empty_allowed_roots_fail_closed(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", value)

    with pytest.raises(ValidationError, match="must contain at least one directory"):
        make_settings()


def test_nonexistent_allowed_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", json.dumps([str(missing)]))

    with pytest.raises(ValidationError, match="does not exist"):
        make_settings()


def test_file_allowed_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("content", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ALLOWED_ROOTS", json.dumps([str(file_path)]))

    with pytest.raises(ValidationError, match="not a directory"):
        make_settings()
