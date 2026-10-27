# @author lxy
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3

from engine.core.workspace.mutations import MutationProposal


@dataclass(frozen=True, slots=True)
class StoredProposal:
    state: str
    mutation: MutationProposal
    validation_profile: str
    expires_at: float


class SqliteProposalStore:
    """Durable storage boundary for prepared workspace changes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def save(
        self,
        *,
        workspace_id: str,
        generation: str,
        mutation: MutationProposal,
        validation_profile: str,
        expires_at: float,
        max_pending: int,
    ) -> tuple[str, ...]:
        payload = asdict(mutation)
        payload["repository_identity"] = list(mutation.repository_identity)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO prepared_changes (
                    workspace_id, generation, proposal_id, patch_sha256,
                    state, payload_json, validation_profile, expires_at, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, unixepoch('subsec'))
                """,
                (
                    workspace_id,
                    generation,
                    mutation.approval_id,
                    mutation.patch_sha256,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    validation_profile,
                    expires_at,
                ),
            )
            rows = connection.execute(
                """
                SELECT proposal_id FROM prepared_changes
                WHERE workspace_id = ? AND generation = ? AND state = 'pending'
                ORDER BY created_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
                """,
                (workspace_id, generation, max_pending),
            ).fetchall()
            evicted = tuple(str(row[0]) for row in rows)
            if evicted:
                connection.executemany(
                    """
                    UPDATE prepared_changes SET state = 'evicted'
                    WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                    """,
                    ((workspace_id, generation, proposal_id) for proposal_id in evicted),
                )
            self._prune_consumed(connection, workspace_id, generation, max_pending)
            connection.commit()
        return evicted

    def load(
        self,
        workspace_id: str,
        generation: str,
        proposal_id: str,
        *,
        now: float,
    ) -> StoredProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state, payload_json, validation_profile, expires_at
                FROM prepared_changes
                WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                """,
                (workspace_id, generation, proposal_id),
            ).fetchone()
            if row is None:
                return None
            state, payload_json, validation_profile, expires_at = row
            if state == "pending" and now >= float(expires_at):
                connection.execute(
                    """
                    UPDATE prepared_changes SET state = 'expired'
                    WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                    """,
                    (workspace_id, generation, proposal_id),
                )
                connection.commit()
                state = "expired"
        payload = json.loads(payload_json)
        payload["repository_identity"] = tuple(payload["repository_identity"])
        return StoredProposal(
            state=str(state),
            mutation=MutationProposal(**payload),
            validation_profile=str(validation_profile),
            expires_at=float(expires_at),
        )

    def consume(
        self,
        workspace_id: str,
        generation: str,
        proposal_id: str,
        patch_sha256: str,
        *,
        now: float,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE prepared_changes SET state = 'consumed'
                WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                  AND patch_sha256 = ? AND state = 'pending' AND expires_at > ?
                """,
                (workspace_id, generation, proposal_id, patch_sha256, now),
            )
            connection.commit()
            return cursor.rowcount == 1

    def discard(
        self, workspace_id: str, generation: str, proposal_id: str, state: str
    ) -> None:
        if state not in {"evicted", "expired"}:
            raise ValueError("invalid proposal terminal state")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE prepared_changes SET state = ?
                WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                  AND state = 'pending'
                """,
                (state, workspace_id, generation, proposal_id),
            )
            connection.commit()

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prepared_changes (
                    workspace_id TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    validation_profile TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (workspace_id, generation, proposal_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_prepared_changes_scope_state
                ON prepared_changes (workspace_id, generation, state, created_at)
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _prune_consumed(
        connection: sqlite3.Connection,
        workspace_id: str,
        generation: str,
        max_records: int,
    ) -> None:
        connection.execute(
            """
            DELETE FROM prepared_changes
            WHERE rowid IN (
                SELECT rowid FROM prepared_changes
                WHERE workspace_id = ? AND generation = ? AND state != 'pending'
                ORDER BY created_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (workspace_id, generation, max_records),
        )
