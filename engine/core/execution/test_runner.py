# @author lxy
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from typing import Dict, Optional, Tuple

from engine.core.workspace.path_guard import GuardedRepository, WorkspacePathError


@dataclass(frozen=True, slots=True)
class _TestProfile:
    marker: str
    executable: str
    arguments: Tuple[str, ...]


@dataclass(slots=True)
class _OutputBudget:
    remaining: int
    lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()

    async def retain(self, chunk: bytes) -> Tuple[bytes, bool]:
        async with self.lock:
            retained = chunk[: self.remaining]
            self.remaining -= len(retained)
            return retained, len(retained) < len(chunk)


_PROFILES: Dict[str, _TestProfile] = {
    "python_pytest": _TestProfile("pyproject.toml", "python", ("-m", "pytest")),
    "maven_test": _TestProfile("pom.xml", "mvn", ("test",)),
    "npm_test": _TestProfile("package.json", "npm", ("test",)),
}

_ALLOWED_ENVIRONMENT = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "JAVA_HOME",
    "M2_HOME",
    "MAVEN_HOME",
    "TEMP",
    "TMP",
)


class SafeTestRunner:
    """Run one server-defined test profile without accepting command arguments.

    This blocks command/profile injection. Project tests are executable code, so
    deployment must still provide a low-privilege, secret-free isolation boundary.
    """

    def __init__(
        self,
        repository: GuardedRepository,
        *,
        timeout_seconds: float = 300,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    async def run(self, profile: str) -> dict:
        self.validate_profile(profile)
        selected = _PROFILES.get(profile)
        assert selected is not None

        options = {
            "cwd": str(self.repository.root),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True

        executable = self._resolve_executable(selected.executable)
        options["env"] = self._safe_environment()
        process = await asyncio.create_subprocess_exec(
            executable, *selected.arguments, **options
        )
        budget = _OutputBudget(self.max_output_bytes)
        stdout_task = asyncio.create_task(self._read_limited(process.stdout, budget))
        stderr_task = asyncio.create_task(self._read_limited(process.stderr, budget))
        wait_task = asyncio.create_task(process.wait())
        try:
            try:
                exit_code = await asyncio.wait_for(
                    asyncio.shield(wait_task), timeout=self.timeout_seconds
                )
            except TimeoutError:
                terminated = await self._kill_and_reap(process, wait_task)
                if not terminated:
                    await self._cancel_readers(stdout_task, stderr_task)
                    return self._result(
                        profile=profile,
                        status="termination_failed",
                        exit_code=None,
                        stdout="",
                        stderr="Test process tree could not be terminated safely.",
                        truncated=False,
                    )
                stdout, stderr = await self._collect_readers(
                    stdout_task, stderr_task
                )
                return self._result(
                    profile=profile,
                    status="timed_out",
                    exit_code=None,
                    stdout=stdout[0],
                    stderr=stderr[0] or "Test execution timed out.",
                    truncated=stdout[1] or stderr[1],
                )

            stdout, stderr = await self._collect_readers(stdout_task, stderr_task)
            return self._result(
                profile=profile,
                status="passed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                stdout=stdout[0],
                stderr=stderr[0],
                truncated=stdout[1] or stderr[1],
            )
        except asyncio.CancelledError:
            await self._kill_and_reap(process, wait_task)
            await self._cancel_readers(stdout_task, stderr_task)
            raise
        except BaseException:
            await self._kill_and_reap(process, wait_task)
            await self._cancel_readers(stdout_task, stderr_task)
            raise

    async def _read_limited(
        self, stream, budget: _OutputBudget
    ) -> Tuple[str, bool]:
        if stream is None:
            return "", False
        retained = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            selected, chunk_truncated = await budget.retain(chunk)
            retained.extend(selected)
            truncated = truncated or chunk_truncated
        return retained.decode("utf-8", errors="replace"), truncated

    @staticmethod
    async def _collect_readers(*tasks: asyncio.Task):
        try:
            return await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        except TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise RuntimeError("test output streams did not close") from None

    @staticmethod
    async def _kill_and_reap(process, wait_task: asyncio.Task) -> bool:
        tree_terminated = True
        if process.returncode is None:
            if os.name == "nt" and getattr(process, "pid", None):
                killer = None
                killer_wait = None
                try:
                    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
                    taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
                    killer = await asyncio.create_subprocess_exec(
                        taskkill,
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    killer_wait = asyncio.create_task(killer.wait())
                    killer_code = await asyncio.wait_for(
                        asyncio.shield(killer_wait), timeout=5
                    )
                    if killer_code != 0:
                        tree_terminated = False
                except (OSError, TimeoutError):
                    tree_terminated = False
                    if killer is not None and killer.returncode is None:
                        with suppress(OSError):
                            killer.kill()
                    if killer_wait is not None:
                        with suppress(OSError, TimeoutError):
                            await asyncio.wait_for(
                                asyncio.shield(killer_wait), timeout=1
                            )
                        if not killer_wait.done():
                            killer_wait.cancel()
                            await asyncio.gather(killer_wait, return_exceptions=True)
            elif getattr(process, "pid", None):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        tree_terminated = False
                    with suppress(OSError):
                        process.kill()
            else:
                process.kill()
        if process.returncode is None and not tree_terminated:
            with suppress(OSError):
                process.kill()
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=5)
        except (OSError, TimeoutError):
            if process.returncode is None:
                with suppress(OSError):
                    process.kill()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=1)
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
                tree_terminated = False
        return tree_terminated

    def _resolve_executable(self, name: str) -> str:
        if name == "python":
            executable = Path(sys.executable).resolve()
        else:
            located = shutil.which(name)
            if located is None:
                raise ValueError(f"test runtime is unavailable for profile: {name}")
            executable = Path(located).resolve()
        if not executable.is_file():
            raise ValueError(f"test runtime is unavailable for profile: {name}")
        try:
            executable.relative_to(self.repository.root)
        except ValueError:
            pass
        else:
            raise ValueError("test runtime cannot be loaded from the repository")
        return str(executable)

    @staticmethod
    def _safe_environment() -> Dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in _ALLOWED_ENVIRONMENT
            if key in os.environ
        }
        environment.update({"CI": "true", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"})
        return environment

    def validate_profile(self, profile: str) -> None:
        """Validate the fixed profile, marker, and external runtime without running it."""
        selected = _PROFILES.get(profile)
        if selected is None:
            raise ValueError("unsupported test profile")
        try:
            self.repository.resolve(selected.marker, kind="file", must_exist=True)
        except WorkspacePathError as exc:
            raise ValueError("required project marker is missing") from exc
        self._resolve_executable(selected.executable)

    @staticmethod
    async def _cancel_readers(*tasks: asyncio.Task) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _result(
        *,
        profile: str,
        status: str,
        exit_code: Optional[int],
        stdout: str,
        stderr: str,
        truncated: bool,
    ) -> dict:
        return {
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "profile": profile,
        }
