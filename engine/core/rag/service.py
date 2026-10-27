# @author lxy
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
from pathlib import Path

from engine.core.rag.chunker import chunk_source
from engine.core.rag.models import IndexedFile, RagQueryResult


ALLOWED_SUFFIXES = {".py", ".java", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".xml", ".properties", ".gradle", ".kts", ".js", ".ts", ".tsx", ".jsx"}
EXCLUDED_DIRECTORIES = {".git", ".svn", ".hg", ".venv", "venv", "node_modules", "target", "build", "dist", "out", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*credentials*", "*secret*")
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 200_000_000
MAX_CHUNKS = 50_000


class RagService:
    def __init__(self, store, path_guard):
        self.store = store
        self.path_guard = path_guard
        self._tasks: dict[str, asyncio.Task] = {}

    def status(self, workspace):
        return self.store.status(workspace)

    def start_rebuild(self, workspace):
        status = self.store.queue(workspace)
        task = asyncio.create_task(self._run(workspace, status.job_id))
        self._tasks[status.job_id] = task
        task.add_done_callback(
            lambda completed, job_id=status.job_id: self._discard_task(job_id, completed)
        )
        return status

    def _discard_task(self, job_id: str, completed: asyncio.Task):
        if self._tasks.get(job_id) is completed:
            self._tasks.pop(job_id, None)

    async def wait(self, job_id: str):
        task = self._tasks.get(job_id)
        if task is not None:
            await task

    async def shutdown(self):
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def rebuild(self, workspace):
        status = self.start_rebuild(workspace)
        await self.wait(status.job_id)
        return self.status(workspace)

    async def _run(self, workspace, job_id):
        try:
            self.store.mark_building(workspace.id, job_id)
            files = await self._build(workspace)
            self.store.install(workspace, job_id, files)
        except Exception:
            try:
                self.store.fail(workspace.id, job_id, "index_build_failed")
            except Exception:
                pass

    async def _build(self, workspace):
        return await asyncio.to_thread(self._collect, workspace)

    def _collect(self, workspace):
        indexed = []
        total_bytes = 0
        chunk_count = 0
        active_status = self.store.status(workspace)
        manifest = self.store.active_manifest(workspace.id, workspace.generation)
        for repository in sorted(workspace.repositories, key=lambda item: item.name):
            guarded = self.path_guard.bind(repository)
            for file_path in guarded.iter_files("."):
                relative = guarded.relative_path(file_path, resolve=False)
                parts = Path(relative).parts
                name = Path(relative).name
                if any(part.lower() in EXCLUDED_DIRECTORIES or part.startswith(".") for part in parts[:-1]):
                    continue
                lower_name = name.lower()
                if name.startswith(".") or any(fnmatch.fnmatch(lower_name, pattern) for pattern in SECRET_PATTERNS):
                    continue
                if Path(relative).suffix.lower() not in ALLOWED_SUFFIXES or lower_name.endswith((".min.js", ".min.css", ".lock")):
                    continue
                safe_path = guarded.resolve(relative, kind="file", must_exist=True)
                before = safe_path.stat()
                if before.st_size > MAX_FILE_BYTES:
                    continue
                if len(indexed) >= MAX_FILES or total_bytes + before.st_size > MAX_TOTAL_BYTES:
                    raise RuntimeError("RAG index budget exceeded")
                with safe_path.open("rb") as handle:
                    opened = os.fstat(handle.fileno())
                    raw = handle.read()
                after_path = guarded.resolve(relative, kind="file", must_exist=True)
                after = after_path.stat()
                before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                if (
                    safe_path != after_path
                    or before_identity != opened_identity
                    or opened_identity != after_identity
                ):
                    raise RuntimeError("repository file changed while indexing")
                size = len(raw)
                if size > MAX_FILE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
                    raise RuntimeError("RAG index budget exceeded")
                if b"\x00" in raw:
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if manifest.get((repository.name, relative)) == (digest, size):
                    existing = self.store.load_file(
                        workspace.id,
                        workspace.generation,
                        active_status.index_generation,
                        repository.name,
                        relative,
                    )
                    if existing is not None:
                        indexed.append(existing)
                        total_bytes += size
                        chunk_count += len(existing.chunks)
                        continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                chunks = tuple(chunk_source(repository.name, relative, text))
                if chunk_count + len(chunks) > MAX_CHUNKS:
                    raise RuntimeError("RAG chunk budget exceeded")
                indexed.append(IndexedFile(repository.name, relative, digest, size, chunks))
                total_bytes += size
                chunk_count += len(chunks)
        return indexed

    def query(self, workspace, query: str, top_k: int) -> RagQueryResult:
        status, citations = self.store.query(workspace, query, top_k)
        return RagQueryResult(workspace_id=workspace.id, index_generation=status.index_generation, citations=citations)
