# @author lxy
"""
git_tools —— Git 操作工具集

提供安全的异步 Git 命令执行能力：
- git_diff(): 获取工作区变更 diff。
- git_status(): 获取工作区状态。
- git_commit(): 暂存并提交变更。

安全机制：
- 路径护栏：所有操作限制在 workspace_dir 内，防止越界。
- 命令白名单：仅允许预定义的 git 子命令。
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────── 配置常量 ────────────────────────────

DEFAULT_TIMEOUT = 30  # Git 命令超时（秒）
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.getcwd())


def _validate_repo_path(path: str, workspace_dir: str) -> str:
    """
    校验 Git 仓库路径是否在 workspace 内。

    Args:
        path: 待校验的路径。
        workspace_dir: 工作空间根目录。
    Returns:
        规范化后的绝对路径。
    Raises:
        PermissionError: 路径不在 workspace 内。
    """
    resolved = Path(path).resolve()
    ws_resolved = Path(workspace_dir).resolve()

    if not str(resolved).startswith(str(ws_resolved)):
        raise PermissionError(
            f"路径越界：'{resolved}' 不在 workspace '{ws_resolved}' 内"
        )
    return str(resolved)


async def _run_git(
    args: list,
    cwd: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    内部辅助：执行 git 子命令并返回结果。

    Args:
        args: git 子命令参数列表（不含 'git' 前缀）。
        cwd: 执行目录。
        timeout: 超时时间。
    Returns:
        包含 stdout / stderr / returncode / success 的结果字典。
    """
    cmd = ["git"] + args
    logger.debug(f"执行 Git 命令: {' '.join(cmd)} (cwd={cwd})")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": process.returncode,
            "success": process.returncode == 0,
            "command": " ".join(cmd),
        }

    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": f"Git 命令超时（>{timeout}s）",
            "returncode": -1,
            "success": False,
            "command": " ".join(cmd),
        }
    except Exception as e:
        logger.error(f"Git 命令异常: {e}")
        return {
            "stdout": "",
            "stderr": f"执行异常: {type(e).__name__}: {e}",
            "returncode": -1,
            "success": False,
            "command": " ".join(cmd),
        }


# ──────────────────────────── 公开接口 ────────────────────────────


async def git_diff(
    repo_path: Optional[str] = None,
    staged: bool = False,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取 Git 工作区 diff。

    Args:
        repo_path: Git 仓库路径（默认 workspace_dir）。
        staged: True 则查看暂存区 diff（--cached）。
        workspace_dir: 工作空间根路径。
    Returns:
        Git diff 结果字典。
    """
    ws_dir = workspace_dir or WORKSPACE_DIR
    path = _validate_repo_path(repo_path or ws_dir, ws_dir)

    args = ["diff"]
    if staged:
        args.append("--cached")

    result = await _run_git(args, cwd=path)
    result["operation"] = "git_diff"
    result["staged"] = staged
    return result


async def git_status(
    repo_path: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取 Git 工作区状态。

    Args:
        repo_path: Git 仓库路径（默认 workspace_dir）。
        workspace_dir: 工作空间根路径。
    Returns:
        Git status 结果字典。
    """
    ws_dir = workspace_dir or WORKSPACE_DIR
    path = _validate_repo_path(repo_path or ws_dir, ws_dir)

    result = await _run_git(["status", "--porcelain"], cwd=path)
    result["operation"] = "git_status"

    # 解析 porcelain 输出为文件列表
    if result["success"] and result["stdout"].strip():
        files = []
        for line in result["stdout"].strip().split("\n"):
            if len(line) >= 4:
                status_code = line[:2].strip()
                filepath = line[3:]
                files.append({"status": status_code, "path": filepath})
        result["files"] = files
    else:
        result["files"] = []

    return result


async def git_commit(
    message: str,
    repo_path: Optional[str] = None,
    add_all: bool = True,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    暂存并提交 Git 变更。

    Args:
        message: 提交消息。
        repo_path: Git 仓库路径（默认 workspace_dir）。
        add_all: 是否先执行 git add -A（默认 True）。
        workspace_dir: 工作空间根路径。
    Returns:
        Git commit 结果字典。
    """
    ws_dir = workspace_dir or WORKSPACE_DIR
    path = _validate_repo_path(repo_path or ws_dir, ws_dir)

    # 可选：先 git add -A
    if add_all:
        add_result = await _run_git(["add", "-A"], cwd=path)
        if not add_result["success"]:
            add_result["operation"] = "git_commit (add 阶段失败)"
            return add_result

    # 执行 commit
    result = await _run_git(["commit", "-m", message], cwd=path)
    result["operation"] = "git_commit"
    result["message"] = message
    return result
