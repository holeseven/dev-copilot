# @author lxy
"""Run every local verification gate in a stable cross-platform order."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Command:
    """One verification command and the directory in which it must run."""

    args: tuple[str, ...]
    cwd: Path


def build_commands(
    *,
    repository_root: Path,
    python_executable: str,
    platform: str,
) -> list[Command]:
    """Build the required Python, frontend, and Java verification commands."""

    java_root = repository_root / "java-service"
    is_windows = platform == "win32"
    npm = "npm.cmd" if is_windows else "npm"
    wrapper = java_root / ("mvnw.cmd" if is_windows else "mvnw")
    return [
        Command(
            (python_executable, "-m", "pytest", "engine/tests", "-q"),
            repository_root,
        ),
        Command((npm, "test"), repository_root),
        Command((str(wrapper), "-B", "-ntp", "test"), java_root),
    ]


def run(commands: Iterable[Command]) -> None:
    """Run gates in order and stop immediately when one exits unsuccessfully."""

    for command in commands:
        print(f"==> {' '.join(command.args)}", flush=True)
        subprocess.run(command.args, cwd=command.cwd, check=True)


def main() -> int:
    commands = build_commands(
        repository_root=REPOSITORY_ROOT,
        python_executable=sys.executable,
        platform=sys.platform,
    )
    try:
        run(commands)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    except OSError as exc:
        print(f"verification command could not start: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
