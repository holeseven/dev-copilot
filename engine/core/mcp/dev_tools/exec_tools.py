# @author lxy
"""
exec_tools —— 命令执行与测试运行工具

提供安全的异步子进程执行能力：
- run_command(): 执行任意 shell 命令，限制 cwd 在 workspace 内。
- run_tests(): 在指定路径运行测试（自动检测 pytest/unittest）。

安全机制：
- 路径护栏：cwd 必须位于 workspace_dir 目录内，防止越界操作。
- 超时控制：默认 60s 超时，防止命令挂起。
- 输出截断：stdout/stderr 超过 MAX_OUTPUT_SIZE 时截断。
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────── 配置常量 ────────────────────────────

DEFAULT_TIMEOUT = 60  # 命令执行超时（秒）
MAX_OUTPUT_SIZE = 50000  # 输出最大字符数
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.getcwd())


def _validate_cwd(cwd: str, workspace_dir: str) -> str:
    """
    校验并规范化 cwd 路径，确保在 workspace_dir 内。

    Args:
        cwd: 待校验的工作目录。
        workspace_dir: 允许的工作空间根目录。
    Returns:
        规范化后的绝对路径。
    Raises:
        PermissionError: cwd 不在 workspace_dir 内时抛出。
    """
    resolved_cwd = Path(cwd).resolve()
    resolved_workspace = Path(workspace_dir).resolve()

    if not str(resolved_cwd).startswith(str(resolved_workspace)):
        raise PermissionError(
            f"路径越界：cwd '{resolved_cwd}' 不在 workspace '{resolved_workspace}' 内"
        )
    return str(resolved_cwd)


def _truncate_output(text: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    """截断过长的输出文本"""
    if len(text) > max_size:
        return text[:max_size] + f"\n... [输出截断，共 {len(text)} 字符]"
    return text


async def run_command(
    cmd: str,
    cwd: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    异步执行 shell 命令。

    Args:
        cmd: 要执行的命令字符串。
        cwd: 命令工作目录（默认为 workspace_dir）。
        timeout: 超时时间（秒）。
        workspace_dir: 工作空间根目录（路径护栏边界）。
    Returns:
        包含 stdout / stderr / returncode / success 的结果字典。
    """
    ws_dir = workspace_dir or WORKSPACE_DIR
    work_dir = _validate_cwd(cwd or ws_dir, ws_dir)

    logger.info(f"执行命令: {cmd} (cwd={work_dir}, timeout={timeout}s)")

    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        stdout = _truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = _truncate_output(stderr_bytes.decode("utf-8", errors="replace"))
        returncode = process.returncode

        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "success": returncode == 0,
            "command": cmd,
            "cwd": work_dir,
        }

    except asyncio.TimeoutError:
        # 超时则强制终止进程
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": f"命令执行超时（>{timeout}s）",
            "returncode": -1,
            "success": False,
            "command": cmd,
            "cwd": work_dir,
            "timeout": True,
        }
    except PermissionError as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "success": False,
            "command": cmd,
            "cwd": cwd or ws_dir,
            "permission_error": True,
        }
    except Exception as e:
        logger.error(f"命令执行异常: {e}")
        return {
            "stdout": "",
            "stderr": f"执行异常: {type(e).__name__}: {e}",
            "returncode": -1,
            "success": False,
            "command": cmd,
            "cwd": work_dir,
        }


async def run_tests(
    path: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    timeout: float = 120,
) -> Dict[str, Any]:
    """
    在指定路径运行测试。

    自动检测测试框架：
    - 若存在 pytest.ini / pyproject.toml 则使用 pytest
    - 否则使用 python -m unittest discover

    Args:
        path: 测试目录或文件路径（默认为 workspace 根目录）。
        workspace_dir: 工作空间根目录。
        timeout: 超时时间（秒）。
    Returns:
        命令执行结果字典。
    """
    ws_dir = workspace_dir or WORKSPACE_DIR
    test_path = path or ws_dir
    validated_path = _validate_cwd(test_path, ws_dir)

    # 检测测试框架
    test_dir = Path(validated_path)
    use_pytest = (
        (test_dir / "pytest.ini").exists()
        or (test_dir / "pyproject.toml").exists()
        or (test_dir / "setup.cfg").exists()
    )

    if use_pytest:
        cmd = f"python -m pytest {validated_path} -v --tb=short"
    else:
        cmd = f"python -m unittest discover -s {validated_path} -v"

    logger.info(f"运行测试: {'pytest' if use_pytest else 'unittest'} at {validated_path}")

    result = await run_command(cmd, cwd=ws_dir, timeout=timeout, workspace_dir=ws_dir)
    result["test_framework"] = "pytest" if use_pytest else "unittest"
    result["test_path"] = validated_path
    return result
