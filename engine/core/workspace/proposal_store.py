# @author lxy
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
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


@dataclass(frozen=True, slots=True)
class StoredOutcome:
    approval_id: str
    state: str
    result: dict


_OUTCOME_STATES = {
    "applying",
    "rejected",
    "failed",
    "recovery_required",
    "applied_and_tests_passed",
    "applied_tests_failed",
    "applied_tests_timed_out",
    "applied_test_termination_failed",
    "applied_test_error",
    "applied_recovered",
}
_MUTATION_FIELDS = {
    "proposal_id",
    "workspace_id",
    "virtual_path",
    "operation",
    "before_sha256",
    "after_sha256",
}
_VALIDATION_FIELDS = {"status", "exit_code", "truncated", "profile"}
_RESULT_STATUSES = _OUTCOME_STATES | {"recovery_required"}


class SqliteProposalStore:
    """Durable storage boundary for prepared workspace changes."""

    def __init__(self, path: str | Path, *, max_outcomes: int = 100) -> None:
        if max_outcomes <= 0:
            raise ValueError("max_outcomes must be positive")
        self.path = Path(path).expanduser().resolve()
        self.max_outcomes = max_outcomes
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
                  AND patch_sha256 = ? AND state IN ('pending', 'claimed')
                  AND expires_at > ?
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

    def claim_outcome(
        self,
        workspace_id: str,
        generation: str,
        approval_id: str,
        session_id: str,
    ) -> tuple[StoredOutcome | None, bool]:
        """Atomically claim one real proposal for one hashed external session."""

        session_sha256 = self._session_sha256(session_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_claim = connection.execute(
                """
                UPDATE prepared_changes SET state = 'claimed'
                WHERE workspace_id = ? AND generation = ? AND proposal_id = ?
                  AND state = 'pending'
                """,
                (
                    workspace_id,
                    generation,
                    approval_id,
                ),
            )
            outcome_claimed = False
            if proposal_claim.rowcount == 1:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO approval_outcomes (
                        workspace_id, generation, approval_id, session_sha256,
                        state, result_json, updated_at
                    ) VALUES (?, ?, ?, ?, 'applying', '{}', unixepoch('subsec'))
                    """,
                    (workspace_id, generation, approval_id, session_sha256),
                )
                outcome_claimed = cursor.rowcount == 1
                self._prune_old_terminal_generations(
                    connection,
                    workspace_id,
                    generation,
                )
                self._prune_terminal_outcomes(
                    connection,
                    workspace_id,
                    generation,
                )
            row = connection.execute(
                """
                SELECT approval_id, state, result_json, session_sha256
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                """,
                (workspace_id, generation, approval_id),
            ).fetchone()
            connection.commit()
        if row is None or row[3] != session_sha256:
            return None, False
        return self._stored_outcome(row[:3]), outcome_claimed

    def get_outcome(
        self,
        workspace_id: str,
        generation: str,
        approval_id: str,
        session_id: str,
    ) -> StoredOutcome | None:
        session_sha256 = self._session_sha256(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT approval_id, state, result_json
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                  AND session_sha256 = ?
                """,
                (workspace_id, generation, approval_id, session_sha256),
            ).fetchone()
        return self._stored_outcome(row) if row is not None else None

    def list_outcomes(
        self, workspace_id: str, generation: str, session_id: str
    ) -> list[StoredOutcome]:
        session_sha256 = self._session_sha256(session_id)
        with self._connect() as connection:
            applying_rows = connection.execute(
                """
                SELECT approval_id, state, result_json, updated_at, rowid
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND session_sha256 = ?
                  AND state = 'applying'
                """,
                (workspace_id, generation, session_sha256),
            ).fetchall()
            terminal_rows = connection.execute(
                """
                SELECT approval_id, state, result_json, updated_at, rowid
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND session_sha256 = ?
                  AND state != 'applying'
                ORDER BY updated_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    workspace_id,
                    generation,
                    session_sha256,
                    self.max_outcomes,
                ),
            ).fetchall()
        rows = sorted(
            (*applying_rows, *terminal_rows),
            key=lambda row: (float(row[3]), int(row[4])),
        )
        return [self._stored_outcome(row[:3]) for row in rows]

    def complete_outcome(
        self,
        workspace_id: str,
        generation: str,
        approval_id: str,
        session_id: str,
        state: str,
        result: dict,
    ) -> StoredOutcome:
        if state not in _OUTCOME_STATES - {"applying"}:
            raise ValueError("invalid outcome state")
        session_sha256 = self._session_sha256(session_id)
        safe_result = self._sanitize_result(result)
        payload = json.dumps(safe_result, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE approval_outcomes
                SET state = ?, result_json = ?, updated_at = unixepoch('subsec')
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                  AND session_sha256 = ? AND state = 'applying'
                """,
                (
                    state,
                    payload,
                    workspace_id,
                    generation,
                    approval_id,
                    session_sha256,
                ),
            )
            row = connection.execute(
                """
                SELECT approval_id, state, result_json
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                  AND session_sha256 = ?
                """,
                (workspace_id, generation, approval_id, session_sha256),
            ).fetchone()
            self._prune_terminal_outcomes(
                connection,
                workspace_id,
                generation,
            )
            connection.commit()
        if row is None:
            raise LookupError("approval outcome not found")
        return self._stored_outcome(row)

    def reconcile_applying(self, workspace, path_guard) -> list[StoredOutcome]:
        """Resolve interrupted writes from hashes only; never replay a mutation."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT outcome.approval_id, change.payload_json
                FROM approval_outcomes AS outcome
                JOIN prepared_changes AS change
                  ON change.workspace_id = outcome.workspace_id
                 AND change.generation = outcome.generation
                 AND change.proposal_id = outcome.approval_id
                WHERE outcome.workspace_id = ? AND outcome.generation = ?
                  AND outcome.state = 'applying'
                ORDER BY outcome.updated_at, outcome.rowid
                """,
                (workspace.id, workspace.generation),
            ).fetchall()

        repositories = {item.name: item for item in workspace.repositories}
        reconciled: list[StoredOutcome] = []
        for approval_id, payload_json in rows:
            state = "recovery_required"
            status = "recovery_required"
            mutation: dict = {}
            try:
                payload = json.loads(payload_json)
                mutation = {
                    "proposal_id": payload["approval_id"],
                    "workspace_id": payload["workspace_id"],
                    "virtual_path": payload["virtual_path"],
                    "operation": payload["operation"],
                    "before_sha256": payload.get("before_sha256"),
                    "after_sha256": payload["after_sha256"],
                }
                repository = repositories[payload["repository"]]
                guarded = path_guard.bind(repository)
                target = guarded.resolve(payload["relative_path"], must_exist=False)
                current_sha256 = (
                    hashlib.sha256(target.read_bytes()).hexdigest()
                    if target.is_file()
                    else None
                )
                if current_sha256 == payload["after_sha256"]:
                    state = "applied_recovered"
                    status = "applied_recovered"
            except (KeyError, OSError, TypeError, ValueError):
                # Any malformed, moved, or unreadable target is intentionally
                # conservative: a human must inspect it before another write.
                pass
            reconciled.append(
                self._complete_outcome_by_approval(
                    workspace.id,
                    workspace.generation,
                    str(approval_id),
                    state,
                    {
                        "status": status,
                        **({"mutation": mutation} if state == "applied_recovered" else {}),
                    },
                )
            )
        return reconciled

    def _complete_outcome_by_approval(
        self,
        workspace_id: str,
        generation: str,
        approval_id: str,
        state: str,
        result: dict,
    ) -> StoredOutcome:
        """Internal restart path where only the persisted session hash exists."""

        safe_result = self._sanitize_result(result)
        payload = json.dumps(safe_result, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE approval_outcomes
                SET state = ?, result_json = ?, updated_at = unixepoch('subsec')
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                  AND state = 'applying'
                """,
                (state, payload, workspace_id, generation, approval_id),
            )
            row = connection.execute(
                """
                SELECT approval_id, state, result_json, session_sha256
                FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ? AND approval_id = ?
                """,
                (workspace_id, generation, approval_id),
            ).fetchone()
            if row is not None:
                self._prune_terminal_outcomes(
                    connection,
                    workspace_id,
                    generation,
                )
            connection.commit()
        if row is None:
            raise LookupError("approval outcome not found")
        return self._stored_outcome(row[:3])

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_outcomes (
                    workspace_id TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    session_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (workspace_id, generation, approval_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_approval_outcomes_session
                ON approval_outcomes (
                    workspace_id, generation, session_sha256, updated_at
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _session_sha256(session_id: str) -> str:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _stored_outcome(row) -> StoredOutcome:
        return StoredOutcome(
            approval_id=str(row[0]),
            state=str(row[1]),
            result=json.loads(row[2]),
        )

    @staticmethod
    def _sanitize_result(result: dict) -> dict:
        if not isinstance(result, dict):
            return {}
        safe: dict = {}
        status = str(result.get("status", ""))
        if status in _RESULT_STATUSES:
            safe["status"] = status
        mutation = result.get("mutation")
        if isinstance(mutation, dict):
            safe["mutation"] = {
                key: mutation[key] for key in _MUTATION_FIELDS if key in mutation
            }
        validation = result.get("validation")
        if isinstance(validation, dict):
            safe["validation"] = {
                key: validation[key] for key in _VALIDATION_FIELDS if key in validation
            }
        return safe

    def _prune_terminal_outcomes(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        generation: str,
    ) -> None:
        connection.execute(
            """
            DELETE FROM approval_outcomes
            WHERE rowid IN (
                SELECT rowid FROM approval_outcomes
                WHERE workspace_id = ? AND generation = ?
                  AND state != 'applying'
                ORDER BY updated_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (
                workspace_id,
                generation,
                self.max_outcomes,
            ),
        )

    @staticmethod
    def _prune_old_terminal_generations(
        connection: sqlite3.Connection,
        workspace_id: str,
        generation: str,
    ) -> None:
        # Claiming a proposal proves this is the active generation. Historical
        # terminal rows can be removed, while applying rows remain available
        # for conservative recovery and human review.
        connection.execute(
            """
            DELETE FROM approval_outcomes
            WHERE workspace_id = ? AND generation != ? AND state != 'applying'
            """,
            (workspace_id, generation),
        )

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
                  AND NOT EXISTS (
                    SELECT 1 FROM approval_outcomes
                    WHERE approval_outcomes.workspace_id = prepared_changes.workspace_id
                      AND approval_outcomes.generation = prepared_changes.generation
                      AND approval_outcomes.approval_id = prepared_changes.proposal_id
                      AND approval_outcomes.state = 'applying'
                  )
                ORDER BY created_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (workspace_id, generation, max_records),
        )
