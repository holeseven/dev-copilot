# @author lxy
from pathlib import Path
import subprocess

import pytest

from scripts import verify


def test_build_commands_selects_windows_tools_and_preserves_order(tmp_path):
    commands = verify.build_commands(
        repository_root=tmp_path,
        platform="win32",
    )

    assert [command.args for command in commands] == [
        ("uv.exe", "lock", "--check", "--project", "engine"),
        (
            "uv.exe", "run", "--project", "engine", "--frozen", "--extra", "dev",
            "python", "-m", "compileall", "-q", "engine/main.py", "engine/agents",
            "engine/api", "engine/config", "engine/core", "engine/infra",
            "engine/orchestrator",
        ),
        (
            "uv.exe", "run", "--project", "engine", "--frozen", "--extra", "dev",
            "python", "-m", "pytest", "engine/tests", "-q",
        ),
        ("npm.cmd", "test"),
        (str(tmp_path / "java-service" / "mvnw.cmd"), "-B", "-ntp", "test"),
    ]
    assert [command.cwd for command in commands] == [
        tmp_path,
        tmp_path,
        tmp_path,
        tmp_path,
        tmp_path / "java-service",
    ]


def test_build_commands_selects_posix_tools(tmp_path):
    commands = verify.build_commands(
        repository_root=tmp_path,
        platform="linux",
    )

    assert commands[0].args == ("uv", "lock", "--check", "--project", "engine")
    assert commands[1].args[-7:] == (
        "engine/main.py",
        "engine/agents",
        "engine/api",
        "engine/config",
        "engine/core",
        "engine/infra",
        "engine/orchestrator",
    )
    assert commands[3].args == ("npm", "test")
    assert commands[3].cwd == tmp_path
    assert commands[4].args == (
        str(tmp_path / "java-service" / "mvnw"),
        "-B",
        "-ntp",
        "test",
    )
    assert commands[4].cwd == tmp_path / "java-service"


def test_run_inherits_output_and_stops_at_first_failure(tmp_path, monkeypatch):
    commands = [
        verify.Command(("first",), tmp_path),
        verify.Command(("second",), tmp_path),
        verify.Command(("third",), tmp_path),
    ]
    calls = []

    def fake_run(args, **kwargs):
        calls.append((tuple(args), kwargs))
        if args[0] == "second":
            raise subprocess.CalledProcessError(7, args)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as raised:
        verify.run(commands)

    assert raised.value.returncode == 7
    assert calls == [
        (("first",), {"cwd": tmp_path, "check": True}),
        (("second",), {"cwd": tmp_path, "check": True}),
    ]


def test_main_returns_the_failed_command_exit_code(monkeypatch):
    monkeypatch.setattr(verify, "build_commands", lambda **kwargs: [])

    def fail(_commands):
        raise subprocess.CalledProcessError(9, ["broken"])

    monkeypatch.setattr(verify, "run", fail)

    assert verify.main() == 9
