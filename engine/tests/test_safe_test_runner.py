# @author lxy
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from engine.core.execution.test_runner import SafeTestRunner
from engine.core.workspace.models import Repository
from engine.core.workspace.path_guard import WorkspacePathGuard


class FakeStream:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def read(self, _size: int) -> bytes:
        content, self._content = self._content, b""
        return content


class FailingStream:
    async def read(self, _size: int) -> bytes:
        raise OSError("simulated pipe failure")


class BlockingStream:
    def __init__(self) -> None:
        self.cancelled = False

    async def read(self, _size: int) -> bytes:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        block: bool = False,
    ) -> None:
        self.stdout = FakeStream(stdout)
        self.stderr = FakeStream(stderr)
        self.expected_returncode = returncode
        self.returncode = None if block else returncode
        self.block = block
        self.wait_started = asyncio.Event()
        self.release_wait = asyncio.Event()
        self.killed = False
        self.waited = False
        self.pid = 4321

    def kill(self) -> None:
        self.killed = True
        self.release_wait.set()

    async def wait(self) -> int:
        self.wait_started.set()
        if self.block:
            await self.release_wait.wait()
        self.waited = True
        if self.returncode is None:
            self.returncode = self.expected_returncode
        return self.returncode


def _repository(tmp_path: Path):
    root = tmp_path / "repository"
    root.mkdir()
    guard = WorkspacePathGuard.from_roots((tmp_path,))
    return guard.bind(Repository(name="service", path=str(root))), root


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "marker", "expected_argv"),
    [
        ("python_pytest", "pyproject.toml", (sys.executable, "-m", "pytest")),
        ("maven_test", "pom.xml", ("mvn", "test")),
        ("npm_test", "package.json", ("npm", "test")),
    ],
)
async def test_profiles_use_fixed_argv_with_exec_and_no_shell(
    tmp_path, monkeypatch, profile, marker, expected_argv
):
    repository, root = _repository(tmp_path)
    (root / marker).write_text("{}", encoding="utf-8")
    process = FakeProcess(stdout=b"ok\n")
    exec_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_exec(*args, **kwargs):
        exec_calls.append((args, kwargs))
        return process

    async def reject_shell(*_args, **_kwargs):
        raise AssertionError("SafeTestRunner must not invoke a shell")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", reject_shell)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-tests")
    monkeypatch.setattr(
        "engine.core.execution.test_runner.shutil.which",
        lambda name: str(root.parent / f"safe-{name}.exe"),
    )
    for name in ("mvn", "npm"):
        (root.parent / f"safe-{name}.exe").write_text("runtime", encoding="utf-8")

    result = await SafeTestRunner(repository).run(profile)

    assert len(exec_calls) == 1
    argv, options = exec_calls[0]
    if profile == "python_pytest":
        assert argv == expected_argv
    else:
        assert Path(argv[0]).name == f"safe-{expected_argv[0]}.exe"
        assert argv[1:] == expected_argv[1:]
    assert Path(options["cwd"]) == root
    assert options["stdout"] == asyncio.subprocess.PIPE
    assert options["stderr"] == asyncio.subprocess.PIPE
    if os.name == "nt":
        assert options["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert options["start_new_session"] is True
    assert options["env"]["CI"] == "true"
    assert "OPENAI_API_KEY" not in options["env"]
    assert result == {
        "status": "passed",
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
        "truncated": False,
        "profile": profile,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["unknown", "python_pytest; rm -rf repository"])
async def test_unknown_or_shell_like_profile_is_rejected_before_process_start(
    tmp_path, monkeypatch, profile
):
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    started = False

    async def fake_exec(*_args, **_kwargs):
        nonlocal started
        started = True
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ValueError, match="profile"):
        await SafeTestRunner(repository).run(profile)

    assert started is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "unrelated_marker"),
    [
        ("python_pytest", "package.json"),
        ("maven_test", "pyproject.toml"),
        ("npm_test", "pom.xml"),
    ],
)
async def test_profile_requires_its_project_marker(
    tmp_path, monkeypatch, profile, unrelated_marker
):
    repository, root = _repository(tmp_path)
    (root / unrelated_marker).write_text("", encoding="utf-8")
    started = False

    async def fake_exec(*_args, **_kwargs):
        nonlocal started
        started = True
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ValueError, match="marker"):
        await SafeTestRunner(repository).run(profile)

    assert started is False


@pytest.mark.asyncio
async def test_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    process = FakeProcess(block=True)

    async def fake_exec(*args, **_kwargs):
        if args and str(args[0]).lower().endswith("taskkill.exe"):
            process.kill()
            return FakeProcess()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    if os.name != "nt":
        monkeypatch.setattr(
            "engine.core.execution.test_runner.os.killpg",
            lambda _pid, _signal: process.kill(),
        )

    result = await SafeTestRunner(repository, timeout_seconds=0.01).run(
        "python_pytest"
    )

    assert process.killed is True
    assert process.waited is True
    assert result == {
        "status": "timed_out",
        "exit_code": None,
        "stdout": "",
        "stderr": "Test execution timed out.",
        "truncated": False,
        "profile": "python_pytest",
    }


@pytest.mark.asyncio
async def test_cancellation_kills_and_reaps_process_then_propagates(
    tmp_path, monkeypatch
):
    repository, root = _repository(tmp_path)
    (root / "pom.xml").write_text("", encoding="utf-8")
    process = FakeProcess(block=True)

    async def fake_exec(*args, **_kwargs):
        if args and str(args[0]).lower().endswith("taskkill.exe"):
            process.kill()
            return FakeProcess()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    if os.name != "nt":
        monkeypatch.setattr(
            "engine.core.execution.test_runner.os.killpg",
            lambda _pid, _signal: process.kill(),
        )
    runtime = root.parent / "safe-mvn.exe"
    runtime.write_text("runtime", encoding="utf-8")
    monkeypatch.setattr(
        "engine.core.execution.test_runner.shutil.which", lambda _name: str(runtime)
    )
    task = asyncio.create_task(SafeTestRunner(repository).run("maven_test"))
    await process.wait_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_output_is_truncated_to_the_configured_byte_limit(
    tmp_path, monkeypatch
):
    repository, root = _repository(tmp_path)
    (root / "package.json").write_text("{}", encoding="utf-8")
    process = FakeProcess(stdout=b"abcdefgh", stderr=b"uvwxyz")

    async def fake_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runtime = root.parent / "safe-npm.exe"
    runtime.write_text("runtime", encoding="utf-8")
    monkeypatch.setattr(
        "engine.core.execution.test_runner.shutil.which", lambda _name: str(runtime)
    )

    result = await SafeTestRunner(repository, max_output_bytes=5).run("npm_test")

    assert result["status"] == "passed"
    assert result["exit_code"] == 0
    assert len((result["stdout"] + result["stderr"]).encode("utf-8")) == 5
    assert result["truncated"] is True
    assert result["profile"] == "npm_test"


@pytest.mark.asyncio
async def test_real_python_profile_smoke_uses_the_guarded_repository(tmp_path):
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (root / "test_sample.py").write_text(
        "def test_sample():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    result = await SafeTestRunner(repository, timeout_seconds=30).run(
        "python_pytest"
    )

    assert result["status"] == "passed"
    assert result["exit_code"] == 0
    assert "1 passed" in result["stdout"]


@pytest.mark.asyncio
async def test_reader_failure_cancels_and_reaps_the_other_reader(tmp_path, monkeypatch):
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    process = FakeProcess()
    blocked = BlockingStream()
    process.stdout = FailingStream()
    process.stderr = blocked

    async def fake_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(OSError, match="pipe failure"):
        await SafeTestRunner(repository).run("python_pytest")

    assert blocked.cancelled is True


@pytest.mark.asyncio
async def test_windows_taskkill_failure_is_reported(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows taskkill semantics")
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    process = FakeProcess(block=True)

    async def fake_exec(*args, **_kwargs):
        if str(args[0]).lower().endswith("taskkill.exe"):
            return FakeProcess(returncode=5)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await SafeTestRunner(repository, timeout_seconds=0.01).run(
        "python_pytest"
    )

    assert process.killed is True
    assert process.waited is True
    assert result["status"] == "termination_failed"
    assert result["exit_code"] is None


@pytest.mark.asyncio
async def test_posix_group_kill_is_used_for_timeout(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX process-group semantics")
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    process = FakeProcess(block=True)
    killed_groups = []

    async def fake_exec(*_args, **_kwargs):
        return process

    def fake_killpg(pid, used_signal):
        killed_groups.append((pid, used_signal))
        process.kill()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("engine.core.execution.test_runner.os.killpg", fake_killpg)

    await SafeTestRunner(repository, timeout_seconds=0.01).run("python_pytest")

    assert killed_groups == [(process.pid, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_posix_group_kill_failure_is_reported(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX process-group semantics")
    repository, root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    process = FakeProcess(block=True)

    async def fake_exec(*_args, **_kwargs):
        return process

    def deny_killpg(_pid, _used_signal):
        raise PermissionError("simulated EPERM")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("engine.core.execution.test_runner.os.killpg", deny_killpg)

    result = await SafeTestRunner(repository, timeout_seconds=0.01).run(
        "python_pytest"
    )

    assert process.killed is True
    assert process.waited is True
    assert result["status"] == "termination_failed"
