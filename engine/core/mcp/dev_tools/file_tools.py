# @author lxy
"""
文件操作工具函数集

提供 list_dir / read_file / write_file / edit_file / grep_code / find_files 六个工具。
所有路径操作均限制在 settings.workspace_dir 内，防止 ../ 路径越界攻击。
"""
import fnmatch
import os
import re
from pathlib import Path
from typing import List, Optional

from engine.config.settings import get_settings


# ──────────────────────────── 路径安全校验 ────────────────────────────

def _resolve_workspace() -> str:
    """获取并解析工作区绝对路径"""
    settings = get_settings()
    return os.path.realpath(os.path.expanduser(settings.workspace_dir))


def _safe_resolve(path: str) -> str:
    """
    将用户传入路径解析为绝对路径，并校验是否在 workspace_dir 内。
    越界则抛出 PermissionError。
    """
    workspace = _resolve_workspace()
    # 相对路径以 workspace 为基准
    if not os.path.isabs(path):
        resolved = os.path.realpath(os.path.join(workspace, path))
    else:
        resolved = os.path.realpath(path)

    if not resolved.startswith(workspace + os.sep) and resolved != workspace:
        raise PermissionError(
            f"路径越界: {path} 解析为 {resolved}，不在工作区 {workspace} 内"
        )
    return resolved


# ──────────────────────────── 工具函数 ────────────────────────────

def list_dir(path: str = ".") -> List[str]:
    """
    列出目录内容，返回条目名列表。

    Args:
        path: 相对或绝对路径，默认为工作区根目录。
    Returns:
        目录内文件/子目录名称列表。
    """
    resolved = _safe_resolve(path)
    if not os.path.isdir(resolved):
        raise FileNotFoundError(f"目录不存在: {resolved}")
    entries = sorted(os.listdir(resolved))
    result = []
    for entry in entries:
        full = os.path.join(resolved, entry)
        suffix = "/" if os.path.isdir(full) else ""
        result.append(f"{entry}{suffix}")
    return result


def read_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    读取文件内容，支持指定行号范围。

    Args:
        path: 文件路径（相对或绝对）。
        start_line: 起始行号（1-based，含），None 表示从头。
        end_line: 结束行号（1-based，含），None 表示到底。
    Returns:
        文件内容字符串。
    """
    resolved = _safe_resolve(path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"文件不存在: {resolved}")

    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if start_line is not None or end_line is not None:
        s = (start_line - 1) if start_line else 0
        e = end_line if end_line else len(lines)
        lines = lines[s:e]

    return "".join(lines)


def write_file(path: str, content: str, create_dirs: bool = True) -> str:
    """
    写入/创建文件。

    Args:
        path: 目标路径。
        content: 文件完整内容。
        create_dirs: 是否自动创建父目录。
    Returns:
        写入结果消息。
    """
    resolved = _safe_resolve(path)
    if create_dirs:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)

    with open(resolved, "w", encoding="utf-8") as f:
        f.write(content)

    return f"已写入 {resolved}（{len(content)} 字节）"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    搜索替换编辑文件中的文本片段。

    Args:
        path: 文件路径。
        old_text: 要被替换的原始文本（必须在文件中唯一匹配）。
        new_text: 替换后的新文本。
    Returns:
        编辑结果消息。
    """
    resolved = _safe_resolve(path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"文件不存在: {resolved}")

    with open(resolved, "r", encoding="utf-8") as f:
        content = f.read()

    count = content.count(old_text)
    if count == 0:
        raise ValueError("未找到匹配的文本片段")
    if count > 1:
        raise ValueError(f"文本片段匹配 {count} 处，需更精确定位")

    new_content = content.replace(old_text, new_text, 1)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"已编辑 {resolved}"


def grep_code(pattern: str, path: str = ".", ignore_case: bool = False) -> List[str]:
    """
    在文件中搜索正则模式，返回匹配行（带文件路径和行号）。

    Args:
        pattern: 正则表达式。
        path: 搜索起始路径（文件或目录）。
        ignore_case: 是否忽略大小写。
    Returns:
        匹配结果列表，格式: "文件:行号:内容"。
    """
    resolved = _safe_resolve(path)
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags)
    results: List[str] = []
    max_results = 200

    def _search_file(fpath: str):
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if regex.search(line):
                        rel = os.path.relpath(fpath, _resolve_workspace())
                        results.append(f"{rel}:{lineno}:{line.rstrip()}")
                        if len(results) >= max_results:
                            return
        except (OSError, UnicodeDecodeError):
            pass

    if os.path.isfile(resolved):
        _search_file(resolved)
    else:
        for root, _dirs, files in os.walk(resolved):
            for fname in files:
                if len(results) >= max_results:
                    break
                _search_file(os.path.join(root, fname))

    return results


def find_files(glob_pattern: str, path: str = ".") -> List[str]:
    """
    按 glob 模式搜索文件名。

    Args:
        glob_pattern: 文件名 glob 模式，如 "*.py"、"test_*"。
        path: 搜索起始目录。
    Returns:
        匹配的相对路径列表。
    """
    resolved = _safe_resolve(path)
    workspace = _resolve_workspace()
    results: List[str] = []
    max_results = 100

    for root, _dirs, files in os.walk(resolved):
        for fname in files:
            if fnmatch.fnmatch(fname, glob_pattern):
                full = os.path.join(root, fname)
                results.append(os.path.relpath(full, workspace))
                if len(results) >= max_results:
                    return results
    return results
