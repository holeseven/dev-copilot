# @author lxy
import fnmatch
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from engine.core.mcp.approval import ToolApprovalRequest
from engine.core.mcp.providers import ToolProvider
from engine.core.workspace.change_service import ControlledChangeService
from engine.core.workspace.path_guard import (
    GuardedRepository,
    OutsideWorkspaceError,
    WorkspacePathError,
    WorkspacePathGuard,
)


class LocalDevToolProvider(ToolProvider):
    """Read-only developer tools backed by guarded repository capabilities."""

    retry_on_error = False

    def __init__(
        self,
        name: str,
        workspace,
        path_guard: WorkspacePathGuard,
        *,
        change_service: Optional[ControlledChangeService] = None,
        rag_service: Any = None,
        write_enabled: bool = False,
    ):
        super().__init__(name)
        self.workspace = workspace
        self.path_guard = path_guard
        self._repositories = {
            repository.name: repository for repository in workspace.repositories
        }
        self.write_enabled = write_enabled
        self.change_service = change_service
        if self.write_enabled and self.change_service is None:
            self.change_service = ControlledChangeService(
                workspace=workspace,
                path_guard=path_guard,
            )
        self.rag_service = rag_service

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools = [
            self._schema("list_dir", "List a directory in the workspace.", {"path": {"type": "string"}}),
            self._schema("find_files", "Find files by glob pattern in the workspace.", {"glob_pattern": {"type": "string"}, "path": {"type": "string"}}, ["glob_pattern"]),
            self._schema("grep_code", "Search code with a regular expression.", {"pattern": {"type": "string"}, "path": {"type": "string"}, "ignore_case": {"type": "boolean"}}, ["pattern"]),
            self._schema("read_file", "Read a file from the workspace.", {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
            self._schema("code_search", "Search code in the workspace.", {"pattern": {"type": "string"}, "path": {"type": "string"}, "file_glob": {"type": "string"}, "max_results": {"type": "integer"}}, ["pattern"]),
            self._schema(
                "search_workspace",
                "Search the user-built workspace RAG index and return line-addressable citations.",
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                ["query"],
            ),
        ]
        if not self.write_enabled:
            return tools
        tools.extend([
            self._schema(
                "preview_edit_file",
                "Preview one exact UTF-8 replacement. Use the returned proposal with apply_workspace_change.",
                {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                    "validation_profile": {
                        "type": "string",
                        "enum": ["python_pytest", "maven_test", "npm_test"],
                    },
                },
                ["path", "old_text", "new_text", "validation_profile"],
            ),
            self._schema(
                "preview_create_file",
                "Preview creation of one UTF-8 file. Use the returned proposal with apply_workspace_change.",
                {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "validation_profile": {
                        "type": "string",
                        "enum": ["python_pytest", "maven_test", "npm_test"],
                    },
                },
                ["path", "content", "validation_profile"],
            ),
            self._schema(
                "apply_workspace_change",
                "Request human approval, atomically apply a prepared change, then run its fixed test profile.",
                {
                    "proposal_id": {"type": "string"},
                    "patch_sha256": {"type": "string"},
                },
                ["proposal_id", "patch_sha256"],
            ),
        ])
        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name in {
            "preview_edit_file",
            "preview_create_file",
            "apply_workspace_change",
        } and not self.write_enabled:
            raise PermissionError("workspace write mode is disabled")
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
        if tool_name == "search_workspace":
            if set(arguments) - {"query", "top_k"} or "query" not in arguments:
                raise ValueError("tool arguments do not match the required schema")
            query = arguments["query"]
            top_k = arguments.get("top_k", 8)
            if not isinstance(query, str) or not 1 <= len(query) <= 4000:
                raise ValueError("query length must be between 1 and 4000")
            if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
                raise ValueError("top_k must be between 1 and 20")
            if self.rag_service is None:
                raise RuntimeError("workspace RAG is unavailable")
            result = self.rag_service.query(self.workspace, query, top_k)
            return result.model_dump() if hasattr(result, "model_dump") else result
        if tool_name == "preview_edit_file":
            self._require_exact_keys(
                arguments, {"path", "old_text", "new_text", "validation_profile"}
            )
            return self.change_service.preview_replace(
                arguments["path"],
                arguments["old_text"],
                arguments["new_text"],
                arguments["validation_profile"],
            )
        if tool_name == "preview_create_file":
            self._require_exact_keys(
                arguments, {"path", "content", "validation_profile"}
            )
            return self.change_service.preview_create(
                arguments["path"],
                arguments["content"],
                arguments["validation_profile"],
            )
        if tool_name == "apply_workspace_change":
            raise PermissionError("workspace changes require an approved tool call")
        raise ValueError(f"unsupported local tool: {tool_name}")

    async def prepare_approval(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Optional[ToolApprovalRequest]:
        if tool_name != "apply_workspace_change":
            return None
        if not self.write_enabled:
            raise PermissionError("workspace write mode is disabled")
        self._require_exact_keys(arguments, {"proposal_id", "patch_sha256"})
        request = self.change_service.approval_request(
            arguments["proposal_id"], arguments["patch_sha256"]
        )
        return ToolApprovalRequest(
            approval_id=request.approval_id,
            action=request.action,
            payload=request.payload,
        )

    async def call_approved_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        approval_id: str,
    ) -> Any:
        if tool_name != "apply_workspace_change":
            raise PermissionError("tool is not available for approved execution")
        if not self.write_enabled:
            raise PermissionError("workspace write mode is disabled")
        self._require_exact_keys(arguments, {"proposal_id", "patch_sha256"})
        return await self.change_service.apply_approved(
            arguments["proposal_id"],
            arguments["patch_sha256"],
            approval_id,
        )

    @staticmethod
    def _schema(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
        return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}

    @staticmethod
    def _require_exact_keys(arguments: Dict[str, Any], expected: set[str]) -> None:
        if not isinstance(arguments, dict) or set(arguments) != expected:
            raise ValueError("tool arguments do not match the required schema")
        if any(not isinstance(arguments[key], str) for key in expected):
            raise ValueError("tool arguments must be strings")

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
