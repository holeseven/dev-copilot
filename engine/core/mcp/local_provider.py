import fnmatch
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from engine.core.mcp.providers import ToolProvider
from engine.core.workspace.path_guard import (
    GuardedRepository,
    OutsideWorkspaceError,
    WorkspacePathError,
    WorkspacePathGuard,
)


class LocalDevToolProvider(ToolProvider):
    """Read-only developer tools backed by guarded repository capabilities."""

    retry_on_error = False

    def __init__(self, name: str, workspace, path_guard: WorkspacePathGuard):
        super().__init__(name)
        self.workspace = workspace
        self.path_guard = path_guard
        self._repositories = {
            repository.name: repository for repository in workspace.repositories
        }

    async def list_tools(self) -> List[Dict[str, Any]]:
        return [
            self._schema("list_dir", "List a directory in the workspace.", {"path": {"type": "string"}}),
            self._schema("find_files", "Find files by glob pattern in the workspace.", {"glob_pattern": {"type": "string"}, "path": {"type": "string"}}, ["glob_pattern"]),
            self._schema("grep_code", "Search code with a regular expression.", {"pattern": {"type": "string"}, "path": {"type": "string"}, "ignore_case": {"type": "boolean"}}, ["pattern"]),
            self._schema("read_file", "Read a file from the workspace.", {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
            self._schema("code_search", "Search code in the workspace.", {"pattern": {"type": "string"}, "path": {"type": "string"}, "file_glob": {"type": "string"}, "max_results": {"type": "integer"}}, ["pattern"]),
        ]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == "list_dir":
            return self._list_dir(arguments.get("path", "."))
        if tool_name == "find_files":
            return self._find_files(arguments["glob_pattern"], arguments.get("path", "."))
        if tool_name == "grep_code":
            return self._grep_code(arguments["pattern"], arguments.get("path", "."), arguments.get("ignore_case", False))
        if tool_name == "read_file":
            return self._read_file(arguments["path"], arguments.get("start_line"), arguments.get("end_line"))
        if tool_name == "code_search":
            return await self._code_search(arguments["pattern"], arguments.get("path", "."), arguments.get("file_glob", ""), arguments.get("max_results", 50))
        raise ValueError(f"unsupported local tool: {tool_name}")

    @staticmethod
    def _schema(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
        return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required or []}}

    def _list_dir(self, path: str) -> List[str]:
        if path == ".":
            return [name for name, _ in self._bind_all()]
        guarded, relative = self._repository_for_path(path)
        try:
            return [
                self._virtual_entry(guarded.name, relative, entry)
                for entry in guarded.list_dir(relative)
            ]
        except WorkspacePathError as exc:
            if "does not exist" in str(exc):
                raise FileNotFoundError(f"directory not found: {path}") from None
            raise

    def _find_files(self, glob_pattern: str, path: str) -> List[str]:
        return self._across_repositories(
            path,
            lambda guarded, relative: [
                self._virtual_path(guarded.name, guarded.relative_path(file_path))
                for file_path in guarded.iter_files(relative)
                if fnmatch.fnmatch(file_path.name, glob_pattern)
            ],
        )[:100]

    def _grep_code(self, pattern: str, path: str, ignore_case: bool) -> List[str]:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)

        def search(guarded: GuardedRepository, relative: str) -> List[str]:
            matches = []
            for file_path in guarded.iter_files(relative):
                file_relative = guarded.relative_path(file_path)
                for line_number, line in enumerate(
                    guarded.read_text(file_relative).splitlines(), 1
                ):
                    if regex.search(line):
                        matches.append(
                            f"{self._virtual_path(guarded.name, file_relative)}:"
                            f"{line_number}:{line}"
                        )
            return matches

        return self._across_repositories(path, search)[:200]

    def _read_file(self, path: str, start_line: Optional[int], end_line: Optional[int]) -> str:
        guarded, relative = self._repository_for_path(path)
        try:
            lines = guarded.read_text(relative).splitlines(keepends=True)
        except WorkspacePathError as exc:
            if "does not exist" in str(exc):
                raise FileNotFoundError(f"file not found: {path}") from None
            raise
        if start_line is not None or end_line is not None:
            start = (start_line - 1) if start_line else 0
            end = end_line if end_line else len(lines)
            lines = lines[start:end]
        return "".join(lines)

    async def _code_search(self, pattern: str, path: str, file_glob: str, max_results: int) -> Dict[str, Any]:
        from engine.core.mcp.code_search_server import CodeSearchServer

        matches = []
        for guarded, relative in self._paths_for(path):
            result = await CodeSearchServer(guarded).tools_call(
                "code_search",
                {"pattern": pattern, "path": relative, "file_glob": file_glob, "max_results": max_results},
            )
            if result.get("error"):
                if "does not exist" in result["error"]:
                    return {"error": f"path does not exist: {self._virtual_path(guarded.name, relative)}", "matches": []}
                return result
            matches.extend(
                {**match, "file": self._virtual_path(guarded.name, match["file"])}
                for match in result["matches"]
            )
        matches.sort(key=lambda match: (match["file"], match["line_number"], match["content"]))
        return {"matches": matches[:max_results], "total": min(len(matches), max_results), "engine": "local", "pattern": pattern}

    def _across_repositories(self, path: str, operation) -> List[str]:
        results = []
        for guarded, relative in self._paths_for(path):
            results.extend(operation(guarded, relative))
        return sorted(results)

    def _paths_for(self, path: str) -> List[Tuple[GuardedRepository, str]]:
        if path == ".":
            return [(guarded, ".") for _, guarded in self._bind_all()]
        return [self._repository_for_path(path)]

    def _repository_for_path(self, path: str) -> Tuple[GuardedRepository, str]:
        candidate = Path(path)
        if candidate.is_absolute() or not candidate.parts:
            raise PermissionError(f"path is outside the virtual workspace: {path}")
        repository = self._repositories.get(candidate.parts[0])
        if repository is None:
            raise PermissionError(f"path is outside the virtual workspace: {path}")
        guarded = self.path_guard.bind(repository)
        relative = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path(".")
        try:
            resolved = guarded.resolve(relative, must_exist=False)
        except OutsideWorkspaceError:
            raise PermissionError(f"path is outside repository {repository.name}: {path}") from None
        return guarded, guarded.relative_path(resolved, resolve=False)

    def _bind_all(self) -> List[Tuple[str, GuardedRepository]]:
        return [
            (name, self.path_guard.bind(repository))
            for name, repository in sorted(self._repositories.items())
        ]

    @staticmethod
    def _virtual_path(repository_name: str, path: str) -> str:
        return f"{repository_name}/{Path(path).as_posix()}"

    @staticmethod
    def _virtual_entry(repository_name: str, directory: str, entry: str) -> str:
        parts = [repository_name]
        if directory != ".":
            parts.append(Path(directory).as_posix())
        parts.append(entry.replace(chr(92), "/"))
        return "/".join(parts)
