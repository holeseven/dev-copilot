# @author lxy
import asyncio
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from engine.core.workspace.path_guard import (
    GuardedRepository,
    WorkspacePathError,
)


MAX_RESULTS = 50
MAX_FILE_SIZE = 1_000_000
class CodeSearchServer:
    """Code search constrained by a guarded repository capability."""

    def __init__(
        self,
        repository: GuardedRepository,
    ):
        self.repository = repository

    def tools_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "code_search",
                "description": "Search source code with a regular expression.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "file_glob": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["pattern"],
                },
            }
        ]

    async def tools_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name != "code_search":
            raise ValueError(f"unknown tool: {name}")
        return await self._code_search(args)

    async def _code_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        pattern = args.get("pattern", "")
        sub_path = args.get("path", ".") or "."
        file_glob = args.get("file_glob", "")
        max_results = min(args.get("max_results", MAX_RESULTS), MAX_RESULTS)
        if not pattern:
            return {"error": "pattern must not be empty", "matches": []}

        try:
            search_dir = self.repository.resolve(
                sub_path, kind="dir", must_exist=True
            )
        except WorkspacePathError as exc:
            if "does not exist" in str(exc):
                return {"error": f"path does not exist: {sub_path}", "matches": []}
            raise
        search_relative = self.repository.relative_path(search_dir, resolve=False)
        guarded_files = self.repository.iter_files(search_relative)

        rg_result = await self._search_with_ripgrep(
            pattern, str(search_dir), file_glob, max_results
        )
        if rg_result is not None:
            return self._guard_result_paths(rg_result)
        return await self._search_with_python(
            pattern, guarded_files, file_glob, max_results
        )

    def _guard_result_paths(self, result: Dict[str, Any]) -> Dict[str, Any]:
        validated_matches = []
        for match in result.get("matches", []):
            raw_path = Path(match["file"])
            if raw_path.is_absolute() or raw_path.drive:
                relative = self.repository.relative_path(raw_path)
            else:
                path = self.repository.resolve(raw_path, kind="file", must_exist=True)
                relative = self.repository.relative_path(path, resolve=False)
            validated_matches.append({**match, "file": relative})
        return {**result, "matches": validated_matches}

    async def _search_with_ripgrep(
        self,
        pattern: str,
        search_dir: str,
        file_glob: str,
        max_results: int,
    ) -> Optional[Dict[str, Any]]:
        command = [
            "rg",
            "--json",
            "--no-follow",
            "-m",
            str(max_results),
            pattern,
            search_dir,
        ]
        if file_glob:
            command.extend(["--glob", file_glob])
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except (FileNotFoundError, asyncio.TimeoutError):
            return None
        if process.returncode not in (0, 1):
            return None

        matches = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
                if entry.get("type") != "match":
                    continue
                data = entry["data"]
                matches.append(
                    {
                        "file": data["path"]["text"],
                        "line_number": data["line_number"],
                        "content": data["lines"]["text"].rstrip("\n"),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return {
            "matches": matches[:max_results],
            "total": min(len(matches), max_results),
            "engine": "ripgrep",
            "pattern": pattern,
        }

    async def _search_with_python(
        self,
        pattern: str,
        guarded_files: Tuple[Path, ...],
        file_glob: str,
        max_results: int,
    ) -> Dict[str, Any]:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return {"error": f"invalid pattern: {exc}", "matches": []}

        matches = []
        glob_pattern = file_glob or "*"
        for file_path in guarded_files:
            relative = self.repository.relative_path(file_path, resolve=False)
            parts = Path(relative).parts
            if not Path(relative).match(glob_pattern):
                continue
            if any(
                part.startswith(".")
                or part in ("node_modules", "__pycache__", "venv")
                for part in parts
            ):
                continue
            try:
                if file_path.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError as exc:
                raise WorkspacePathError(
                    f"cannot inspect search file: {relative}"
                ) from exc
            content = self.repository.read_text(relative, errors="ignore")
            for line_number, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    matches.append(
                        {
                            "file": relative,
                            "line_number": line_number,
                            "content": line,
                        }
                    )
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        return {
            "matches": matches,
            "total": len(matches),
            "engine": "python_grep",
            "pattern": pattern,
        }
