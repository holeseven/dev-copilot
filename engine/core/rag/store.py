# @author lxy
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from engine.core.rag.models import IndexedFile, RagCitation, RagIndexStatus, SourceChunk


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RagStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()
        self._recover()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _setup(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rag_status(
                    workspace_id TEXT PRIMARY KEY, workspace_generation TEXT NOT NULL,
                    active_workspace_generation TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL, index_generation TEXT NOT NULL, job_id TEXT NOT NULL,
                    building_generation TEXT NOT NULL DEFAULT '',
                    repository_count INTEGER NOT NULL DEFAULT 0, file_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS rag_files(
                    workspace_id TEXT NOT NULL, workspace_generation TEXT NOT NULL,
                    index_generation TEXT NOT NULL, repository TEXT NOT NULL, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                    PRIMARY KEY(workspace_id,index_generation,repository,path)
                );
                CREATE TABLE IF NOT EXISTS rag_chunks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
                    workspace_generation TEXT NOT NULL, index_generation TEXT NOT NULL,
                    repository TEXT NOT NULL, path TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, symbol TEXT NOT NULL,
                    kind TEXT NOT NULL, text TEXT NOT NULL, content_sha256 TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
                    text, workspace_id UNINDEXED, index_generation UNINDEXED, chunk_id UNINDEXED,
                    tokenize='unicode61'
                );
                CREATE INDEX IF NOT EXISTS rag_chunks_scope ON rag_chunks(workspace_id,index_generation);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(rag_status)")}
            if "building_generation" not in columns:
                db.execute("ALTER TABLE rag_status ADD COLUMN building_generation TEXT NOT NULL DEFAULT ''")
            if "active_workspace_generation" not in columns:
                db.execute("ALTER TABLE rag_status ADD COLUMN active_workspace_generation TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE rag_status SET active_workspace_generation=workspace_generation WHERE index_generation<>''")

    def _recover(self):
        with self._connect() as db:
            db.execute(
                "UPDATE rag_status SET state='failed', building_generation='', error_code='index_interrupted', updated_at=? WHERE state IN ('queued','building')",
                (_now(),),
            )

    def status(self, workspace) -> RagIndexStatus:
        with self._connect() as db:
            row = db.execute("SELECT * FROM rag_status WHERE workspace_id=?", (workspace.id,)).fetchone()
        if row is None:
            return RagIndexStatus(workspace_id=workspace.id, state="not_indexed")
        data = dict(row)
        data.pop("workspace_generation")
        data.pop("active_workspace_generation")
        data.pop("building_generation")
        for field in ("index_generation", "job_id", "updated_at", "error_code"):
            data[field] = data[field] or None
        if row["active_workspace_generation"] != workspace.generation and row["state"] == "ready":
            data["state"] = "stale"
        return RagIndexStatus(**data)

    def queue(self, workspace) -> RagIndexStatus:
        job_id = uuid4().hex
        building_generation = uuid4().hex
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM rag_status WHERE workspace_id=?", (workspace.id,)).fetchone()
            if current is not None and current["state"] in {"queued", "building"}:
                raise RuntimeError("RAG rebuild is already running")
            active_generation = current["index_generation"] if current else ""
            active_workspace_generation = current["active_workspace_generation"] if current else ""
            file_count = current["file_count"] if current else 0
            chunk_count = current["chunk_count"] if current else 0
            db.execute("""
                INSERT INTO rag_status(workspace_id,workspace_generation,active_workspace_generation,state,index_generation,job_id,building_generation,repository_count,file_count,chunk_count,updated_at,error_code)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id) DO UPDATE SET workspace_generation=excluded.workspace_generation,state=excluded.state,
                    job_id=excluded.job_id,building_generation=excluded.building_generation,repository_count=excluded.repository_count,
                    updated_at=excluded.updated_at,error_code=''
            """, (workspace.id, workspace.generation, active_workspace_generation, "queued", active_generation, job_id, building_generation, len(workspace.repositories), file_count, chunk_count, _now(), ""))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.status(workspace)

    def mark_building(self, workspace_id: str, job_id: str):
        with self._connect() as db:
            cursor = db.execute("UPDATE rag_status SET state='building',updated_at=? WHERE workspace_id=? AND job_id=? AND state='queued'", (_now(), workspace_id, job_id))
            if cursor.rowcount != 1:
                raise RuntimeError("RAG job is not queued")

    def active_manifest(self, workspace_id: str, workspace_generation: str) -> dict[tuple[str, str], tuple[str, int]]:
        with self._connect() as db:
            status = db.execute("SELECT index_generation,state,active_workspace_generation FROM rag_status WHERE workspace_id=?", (workspace_id,)).fetchone()
            if not status or not status["index_generation"] or status["active_workspace_generation"] != workspace_generation:
                return {}
            rows = db.execute("SELECT repository,path,sha256,size FROM rag_files WHERE workspace_id=? AND workspace_generation=? AND index_generation=?", (workspace_id, workspace_generation, status["index_generation"])).fetchall()
        return {(row["repository"], row["path"]): (row["sha256"], row["size"]) for row in rows}

    def load_file(self, workspace_id: str, workspace_generation: str, index_generation: str, repository: str, path: str) -> IndexedFile | None:
        if not index_generation:
            return None
        with self._connect() as db:
            file = db.execute(
                "SELECT sha256,size FROM rag_files WHERE workspace_id=? AND workspace_generation=? AND index_generation=? AND repository=? AND path=?",
                (workspace_id, workspace_generation, index_generation, repository, path),
            ).fetchone()
            if not file:
                return None
            rows = db.execute(
                "SELECT * FROM rag_chunks WHERE workspace_id=? AND workspace_generation=? AND index_generation=? AND repository=? AND path=? ORDER BY ordinal",
                (workspace_id, workspace_generation, index_generation, repository, path),
            ).fetchall()
        chunks = tuple(
            SourceChunk(repository, path, row["ordinal"], row["start_line"], row["end_line"], row["symbol"], row["kind"], row["text"], row["content_sha256"])
            for row in rows
        )
        return IndexedFile(repository, path, file["sha256"], file["size"], chunks)

    def install(self, workspace, job_id: str, files: list[IndexedFile]):
        with self._connect() as db:
            status = db.execute("SELECT index_generation,building_generation FROM rag_status WHERE workspace_id=? AND job_id=? AND state='building'", (workspace.id, job_id)).fetchone()
            if not status:
                raise RuntimeError("RAG job is not building")
            generation = status["building_generation"]
            for file in files:
                db.execute("INSERT INTO rag_files VALUES(?,?,?,?,?,?,?)", (workspace.id, workspace.generation, generation, file.repository, file.path, file.sha256, file.size))
                for chunk in file.chunks:
                    cursor = db.execute("""
                        INSERT INTO rag_chunks(workspace_id,workspace_generation,index_generation,repository,path,ordinal,start_line,end_line,symbol,kind,text,content_sha256)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (workspace.id, workspace.generation, generation, file.repository, file.path, chunk.ordinal, chunk.start_line, chunk.end_line, chunk.symbol, chunk.kind, chunk.text, chunk.content_sha256))
                    db.execute("INSERT INTO rag_chunks_fts(rowid,text,workspace_id,index_generation,chunk_id) VALUES(?,?,?,?,?)", (cursor.lastrowid, chunk.text, workspace.id, generation, cursor.lastrowid))
            count = db.execute("SELECT COUNT(*) FROM rag_chunks WHERE workspace_id=? AND index_generation=?", (workspace.id, generation)).fetchone()[0]
            db.execute("UPDATE rag_status SET state='ready',active_workspace_generation=?,index_generation=?,building_generation='',file_count=?,chunk_count=?,updated_at=?,error_code='' WHERE workspace_id=? AND job_id=?", (workspace.generation, generation, len(files), count, _now(), workspace.id, job_id))
            old_ids = [row[0] for row in db.execute("SELECT id FROM rag_chunks WHERE workspace_id=? AND index_generation<>?", (workspace.id, generation))]
            if old_ids:
                db.executemany("DELETE FROM rag_chunks_fts WHERE rowid=?", ((item,) for item in old_ids))
            db.execute("DELETE FROM rag_chunks WHERE workspace_id=? AND index_generation<>?", (workspace.id, generation))
            db.execute("DELETE FROM rag_files WHERE workspace_id=? AND index_generation<>?", (workspace.id, generation))

    def fail(self, workspace_id: str, job_id: str, code: str):
        with self._connect() as db:
            db.execute("UPDATE rag_status SET state='failed',building_generation='',error_code=?,updated_at=? WHERE workspace_id=? AND job_id=?", (code, _now(), workspace_id, job_id))

    def query(self, workspace, query: str, top_k: int):
        status = self.status(workspace)
        if status.state == "stale" or not status.index_generation:
            raise RuntimeError("RAG index is not ready")
        with self._connect() as db:
            active = db.execute(
                "SELECT active_workspace_generation FROM rag_status WHERE workspace_id=?",
                (workspace.id,),
            ).fetchone()
        if not active or active["active_workspace_generation"] != workspace.generation:
            raise RuntimeError("RAG index is not ready")
        tokens = [token for token in __import__("re").findall(r"[\w-]+", query, flags=__import__("re").UNICODE) if token]
        if not tokens:
            return status, []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:32])
        with self._connect() as db:
            rows = db.execute("""
                SELECT c.*, bm25(rag_chunks_fts) AS score FROM rag_chunks_fts
                JOIN rag_chunks c ON c.id=rag_chunks_fts.rowid
                WHERE rag_chunks_fts MATCH ? AND c.workspace_id=? AND c.workspace_generation=? AND c.index_generation=?
                ORDER BY score, c.repository, c.path, c.start_line LIMIT ?
            """, (expression, workspace.id, workspace.generation, status.index_generation, top_k)).fetchall()
        citations = []
        for rank, row in enumerate(rows, 1):
            virtual = f"{row['repository']}/{row['path']}"
            citation_id = __import__("hashlib").sha256(f"{workspace.id}:{status.index_generation}:{row['id']}".encode()).hexdigest()[:24]
            citations.append(RagCitation(citation_id=citation_id, rank=rank, repository=row["repository"], virtual_path=virtual, start_line=row["start_line"], end_line=row["end_line"], excerpt=row["text"][:2000], content_sha256=row["content_sha256"]))
        return status, citations
