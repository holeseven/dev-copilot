# @author lxy
"""
CodeSearchServer —— 最简 MCP Server 示例

展示 MCP 协议的 Server 端实现：
- tools_list(): 返回工具 schema（code_search）。
- tools_call(name, args): 执行代码检索（优先使用 ripgrep，降级为 Python 遍历 grep）。

该模块可独立运行，也可嵌入其他 Server 框架。
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────── 配置常量 ────────────────────────────

MAX_RESULTS = 50  # 最大返回匹配数
MAX_FILE_SIZE = 1_000_000  # 单文件最大读取字节数（1MB）
DEFAULT_WORKSPACE = os.environ.get("WORKSPACE_DIR", os.getcwd())


class CodeSearchServer:
    """
    最简 MCP Server —— 代码检索。

    实现 MCP 协议要求的两个核心方法：
    - tools_list(): 声明可提供的工具列表及其 JSON Schema。
    - tools_call(name, args): 根据工具名分发执行。

    Usage:
        server = CodeSearchServer(workspace_dir="/path/to/project")
        schemas = server.tools_list()
        result = await server.tools_call("code_search", {"pattern": "def main"})
    """

    def __init__(self, workspace_dir: Optional[str] = None):
        """
        Args:
            workspace_dir: 代码检索的根目录。
        """
        self.workspace_dir = workspace_dir or DEFAULT_WORKSPACE

    # ═══════════════════ MCP 协议接口 ═══════════════════

    def tools_list(self) -> List[Dict[str, Any]]:
        """
        返回该 Server 提供的工具列表（MCP tools/list 协议）。

        Returns:
            工具 schema 列表。
        """
        return [
            {
                "name": "code_search",
                "description": "在工作区内搜索代码，支持正则表达式。优先使用 ripgrep 加速检索。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "搜索模式（正则表达式）",
                        },
                        "path": {
                            "type": "string",
                            "description": "限定搜索子目录（相对于 workspace，可选）",
                        },
                        "file_glob": {
                            "type": "string",
                            "description": "文件名 glob 过滤（如 *.py），可选",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"最大返回结果数（默认 {MAX_RESULTS}）",
                        },
                    },
                    "required": ["pattern"],
                },
            }
        ]

    async def tools_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行工具调用（MCP tools/call 协议）。

        Args:
            name: 工具名称。
            args: 工具参数。
        Returns:
            执行结果字典。
        Raises:
            ValueError: 工具名称不支持。
        """
        if name == "code_search":
            return await self._code_search(args)
        else:
            raise ValueError(f"未知工具: {name}")

    # ═══════════════════ 内部实现 ═══════════════════

    async def _code_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        代码检索核心逻辑。

        策略：优先调用 ripgrep（rg），若不可用则降级为 Python 文件遍历。
        """
        pattern = args.get("pattern", "")
        sub_path = args.get("path", "")
        file_glob = args.get("file_glob", "")
        max_results = min(args.get("max_results", MAX_RESULTS), MAX_RESULTS)

        if not pattern:
            return {"error": "pattern 参数不能为空", "matches": []}

        # 构建搜索路径
        search_dir = Path(self.workspace_dir)
        if sub_path:
            search_dir = search_dir / sub_path
        search_dir = search_dir.resolve()

        # 路径护栏
        ws_resolved = Path(self.workspace_dir).resolve()
        if not str(search_dir).startswith(str(ws_resolved)):
            return {"error": "搜索路径越界", "matches": []}

        if not search_dir.exists():
            return {"error": f"路径不存在: {search_dir}", "matches": []}

        # 尝试 ripgrep
        rg_result = await self._search_with_ripgrep(
            pattern, str(search_dir), file_glob, max_results
        )
        if rg_result is not None:
            return rg_result

        # 降级：Python 遍历
        return await self._search_with_python(
            pattern, search_dir, file_glob, max_results
        )

    async def _search_with_ripgrep(
        self, pattern: str, search_dir: str, file_glob: str, max_results: int
    ) -> Optional[Dict[str, Any]]:
        """
        使用 ripgrep 搜索代码。

        Returns:
            搜索结果字典，若 rg 不可用则返回 None。
        """
        cmd = ["rg", "--json", "-m", str(max_results), pattern, search_dir]
        if file_glob:
            cmd.extend(["--glob", file_glob])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(), timeout=30
            )

            if process.returncode not in (0, 1):
                # rg 返回 1 表示无匹配，非错误
                return None

            import json
            matches = []
            for line in stdout_bytes.decode("utf-8", errors="replace").split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "match":
                        data = entry["data"]
                        matches.append({
                            "file": data["path"]["text"],
                            "line_number": data["line_number"],
                            "content": data["lines"]["text"].rstrip("\n"),
                        })
                except (json.JSONDecodeError, KeyError):
                    continue

            return {
                "matches": matches[:max_results],
                "total": len(matches),
                "engine": "ripgrep",
                "pattern": pattern,
            }

        except (FileNotFoundError, asyncio.TimeoutError):
            # rg 未安装或超时，降级
            return None

    async def _search_with_python(
        self, pattern: str, search_dir: Path, file_glob: str, max_results: int
    ) -> Dict[str, Any]:
        """
        降级方案：使用 Python 遍历文件并 grep。
        """
        import re

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"error": f"正则表达式无效: {e}", "matches": []}

        matches = []
        glob_pattern = file_glob or "*"

        for filepath in search_dir.rglob(glob_pattern):
            if len(matches) >= max_results:
                break
            if not filepath.is_file():
                continue
            if filepath.stat().st_size > MAX_FILE_SIZE:
                continue
            # 跳过隐藏目录和常见忽略目录
            parts = filepath.parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv") for p in parts):
                continue

            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                for line_num, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        matches.append({
                            "file": str(filepath),
                            "line_number": line_num,
                            "content": line.rstrip(),
                        })
                        if len(matches) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue

        return {
            "matches": matches,
            "total": len(matches),
            "engine": "python_grep",
            "pattern": pattern,
        }
