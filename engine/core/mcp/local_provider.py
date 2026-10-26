from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.core.mcp.providers import ToolProvider


class LocalDevToolProvider(ToolProvider):
    """Read-only developer tools bound to one workspace's repositories."""

    retry_on_error = False

    def __init__(self, name: str, workspace):
        super().__init__(name)
        self.workspace = workspace
        self._repositories = {repository.name: repository for repository in workspace.repositories}

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
            return sorted(self._repositories)
        repository, relative = self._repository_for_path(path)
        from engine.core.mcp.dev_tools.file_tools import list_dir
        return list_dir(relative, workspace_dir=repository.path)

    def _find_files(self, glob_pattern: str, path: str) -> List[str]:
        from engine.core.mcp.dev_tools.file_tools import find_files
        return self._across_repositories(path, lambda repository, relative: [self._virtual_path(repository.name, result) for result in find_files(glob_pattern, relative, workspace_dir=repository.path)])

    def _grep_code(self, pattern: str, path: str, ignore_case: bool) -> List[str]:
        from engine.core.mcp.dev_tools.file_tools import grep_code
        return self._across_repositories(path, lambda repository, relative: [self._virtual_path(repository.name, result) for result in grep_code(pattern, relative, ignore_case, workspace_dir=repository.path)])

    def _read_file(self, path: str, start_line: Optional[int], end_line: Optional[int]) -> str:
        repository, relative = self._repository_for_path(path)
        from engine.core.mcp.dev_tools.file_tools import read_file
        return read_file(relative, start_line, end_line, workspace_dir=repository.path)

    async def _code_search(self, pattern: str, path: str, file_glob: str, max_results: int) -> Dict[str, Any]:
        from engine.core.mcp.code_search_server import CodeSearchServer
        matches = []
        for repository, relative in self._paths_for(path):
            result = await CodeSearchServer(workspace_dir=repository.path).tools_call("code_search", {"pattern": pattern, "path": relative, "file_glob": file_glob, "max_results": max_results})
            if result.get("error"):
                return result
            matches.extend({**match, "file": self._virtual_path(repository.name, match["file"])} for match in result["matches"])
        matches.sort(key=lambda match: (match["file"], match["line_number"], match["content"]))
        return {"matches": matches[:max_results], "total": min(len(matches), max_results), "engine": "local", "pattern": pattern}

    def _across_repositories(self, path: str, operation) -> List[str]:
        results = []
        for repository, relative in self._paths_for(path):
            results.extend(operation(repository, relative))
        return sorted(results)

    def _paths_for(self, path: str) -> List[Tuple[Any, str]]:
        if path == ".":
            return [(repository, ".") for _, repository in sorted(self._repositories.items())]
        return [self._repository_for_path(path)]

    def _repository_for_path(self, path: str) -> Tuple[Any, str]:
        candidate = Path(path)
        if candidate.is_absolute() or not candidate.parts:
            raise PermissionError(f"path is outside the virtual workspace: {path}")
        repository = self._repositories.get(candidate.parts[0])
        if repository is None:
            raise PermissionError(f"path is outside the virtual workspace: {path}")
        root = Path(repository.path).resolve()
        relative = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path(".")
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise PermissionError(f"path is outside repository {repository.name}: {path}")
        return repository, str(resolved.relative_to(root)) or "."

    @staticmethod
    def _virtual_path(repository_name: str, path: str) -> str:
        return f"{repository_name}/{Path(path).as_posix()}"
