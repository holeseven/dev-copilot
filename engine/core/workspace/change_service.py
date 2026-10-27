# @author lxy
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Callable, Dict

from engine.core.execution.test_runner import SafeTestRunner
from engine.core.workspace.models import Workspace
from engine.core.workspace.mutations import MutationProposal, WorkspaceMutator
from engine.core.workspace.path_guard import GuardedRepository, WorkspacePathGuard
from engine.core.workspace.proposal_store import SqliteProposalStore


class ProposalUnavailableError(RuntimeError):
    """A proposal cannot be approved or applied safely."""


@dataclass(frozen=True, slots=True)
class ChangeApprovalRequest:
    approval_id: str
    action: str
    payload: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PreparedChange:
    mutation: MutationProposal
    validation_profile: str
    expires_at: float


class ControlledChangeService:
    """Own server-side proposals from preview through apply and validation."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        path_guard: WorkspacePathGuard,
        runner_factory: Callable[[GuardedRepository], Any] = SafeTestRunner,
        clock: Callable[[], float] = time.time,
        proposal_ttl_seconds: float = 1800,
        max_pending: int = 100,
        max_diff_bytes: int = 256 * 1024,
        proposal_store: SqliteProposalStore | None = None,
    ) -> None:
        if proposal_ttl_seconds <= 0:
            raise ValueError("proposal_ttl_seconds must be positive")
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        if max_diff_bytes <= 0:
            raise ValueError("max_diff_bytes must be positive")
        self.workspace = workspace
        self.path_guard = path_guard
        self.runner_factory = runner_factory
        self.clock = clock
        self.proposal_ttl_seconds = proposal_ttl_seconds
        self.max_pending = max_pending
        self.max_diff_bytes = max_diff_bytes
        self.proposal_store = proposal_store
        self.mutator = WorkspaceMutator(workspace=workspace, path_guard=path_guard)
        self._repositories = {
            repository.name: repository for repository in workspace.repositories
        }
        self._pending: OrderedDict[str, _PreparedChange] = OrderedDict()
        self._consumed: OrderedDict[str, None] = OrderedDict()
        self._repository_locks = {
            name: asyncio.Lock() for name in self._repositories
        }

    def preview_replace(
        self,
        virtual_path: str,
        old_text: str,
        new_text: str,
        validation_profile: str,
    ) -> Dict[str, Any]:
        guarded = self._guarded_repository(virtual_path)
        self.runner_factory(guarded).validate_profile(validation_profile)
        mutation = self.mutator.preview_replace(virtual_path, old_text, new_text)
        return self._remember(mutation, validation_profile)

    def preview_create(
        self,
        virtual_path: str,
        content: str,
        validation_profile: str,
    ) -> Dict[str, Any]:
        guarded = self._guarded_repository(virtual_path)
        self.runner_factory(guarded).validate_profile(validation_profile)
        mutation = self.mutator.preview_write(virtual_path, content)
        return self._remember(mutation, validation_profile)

    def approval_request(
        self, proposal_id: str, patch_sha256: str
    ) -> ChangeApprovalRequest:
        prepared = self._require_pending(proposal_id, patch_sha256)
        self.mutator.validate(prepared.mutation)
        guarded = self._guarded_repository(prepared.mutation.virtual_path)
        self.runner_factory(guarded).validate_profile(prepared.validation_profile)
        mutation = prepared.mutation
        return ChangeApprovalRequest(
            approval_id=mutation.approval_id,
            action="apply_workspace_change",
            payload={
                "tool_name": "apply_workspace_change",
                "operation": mutation.operation,
                "workspace_id": mutation.workspace_id,
                "repository": mutation.repository,
                "virtual_path": mutation.virtual_path,
                "diff": mutation.diff,
                "before_sha256": mutation.before_sha256,
                "after_sha256": mutation.after_sha256,
                "patch_sha256": mutation.patch_sha256,
                "validation_profile": prepared.validation_profile,
            },
        )

    async def apply_approved(
        self,
        proposal_id: str,
        patch_sha256: str,
        approval_id: str,
    ) -> Dict[str, Any]:
        if approval_id != proposal_id:
            raise ProposalUnavailableError("approval does not match proposal")
        prepared = self._require_pending(proposal_id, patch_sha256)
        lock = self._repository_locks[prepared.mutation.repository]
        async with lock:
            prepared = self._require_pending(proposal_id, patch_sha256)
            if self.proposal_store is not None and not self.proposal_store.consume(
                self.workspace.id,
                self.workspace.generation,
                proposal_id,
                patch_sha256,
                now=self.clock(),
            ):
                self._raise_persisted_state(proposal_id, patch_sha256)
            self._pending.pop(proposal_id, None)
            self._mark_consumed(proposal_id)
            try:
                mutation_result = self.mutator.apply(prepared.mutation)
            finally:
                self.mutator.discard(proposal_id)

            guarded = self._guarded_repository(prepared.mutation.virtual_path)
            runner = self.runner_factory(guarded)
            try:
                runner.validate_profile(prepared.validation_profile)
                validation = await runner.run(prepared.validation_profile)
            except Exception:
                validation = {
                    "status": "error",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "Test runner could not complete safely.",
                    "truncated": False,
                    "profile": prepared.validation_profile,
                }

        validation_status = str(validation.get("status", "error"))
        overall_status = {
            "passed": "applied_and_tests_passed",
            "failed": "applied_tests_failed",
            "timed_out": "applied_tests_timed_out",
            "termination_failed": "applied_test_termination_failed",
        }.get(validation_status, "applied_test_error")
        return {
            "status": overall_status,
            "mutation": {
                "proposal_id": mutation_result.approval_id,
                "workspace_id": mutation_result.workspace_id,
                "virtual_path": mutation_result.virtual_path,
                "operation": mutation_result.operation,
                "before_sha256": mutation_result.before_sha256,
                "after_sha256": mutation_result.after_sha256,
            },
            "validation": dict(validation),
        }

    def _remember(
        self, mutation: MutationProposal, validation_profile: str
    ) -> Dict[str, Any]:
        if len(mutation.diff.encode("utf-8")) > self.max_diff_bytes:
            self.mutator.discard(mutation.approval_id)
            raise ValueError("mutation diff exceeds the size limit")
        self._prune_expired()
        while len(self._pending) >= self.max_pending:
            evicted_id, evicted = self._pending.popitem(last=False)
            self.mutator.discard(evicted.mutation.approval_id)
            if self.proposal_store is not None:
                self.proposal_store.discard(
                    self.workspace.id,
                    self.workspace.generation,
                    evicted_id,
                    "evicted",
                )
        expires_at = self.clock() + self.proposal_ttl_seconds
        prepared = _PreparedChange(mutation, validation_profile, expires_at)
        if self.proposal_store is not None:
            evicted_ids = self.proposal_store.save(
                workspace_id=self.workspace.id,
                generation=self.workspace.generation,
                mutation=mutation,
                validation_profile=validation_profile,
                expires_at=expires_at,
                max_pending=self.max_pending,
            )
            for evicted_id in evicted_ids:
                evicted = self._pending.pop(evicted_id, None)
                if evicted is not None:
                    self.mutator.discard(evicted.mutation.approval_id)
        self._pending[mutation.approval_id] = prepared
        return self._public_preview(prepared)

    def _require_pending(
        self, proposal_id: str, patch_sha256: str
    ) -> _PreparedChange:
        if proposal_id in self._consumed:
            raise ProposalUnavailableError("proposal already consumed")
        prepared = self._pending.get(proposal_id)
        if prepared is None and self.proposal_store is not None:
            stored = self.proposal_store.load(
                self.workspace.id,
                self.workspace.generation,
                proposal_id,
                now=self.clock(),
            )
            if stored is not None and stored.state == "pending":
                prepared = _PreparedChange(
                    stored.mutation,
                    stored.validation_profile,
                    stored.expires_at,
                )
                self.mutator.restore(stored.mutation)
                self._pending[proposal_id] = prepared
            elif stored is not None and stored.state == "consumed":
                raise ProposalUnavailableError("proposal already consumed")
            elif stored is not None and stored.state == "expired":
                raise ProposalUnavailableError("proposal has expired")
        if prepared is None:
            raise ProposalUnavailableError("proposal is unavailable")
        if self.clock() >= prepared.expires_at:
            self._pending.pop(proposal_id, None)
            self.mutator.discard(proposal_id)
            if self.proposal_store is not None:
                self.proposal_store.discard(
                    self.workspace.id,
                    self.workspace.generation,
                    proposal_id,
                    "expired",
                )
            raise ProposalUnavailableError("proposal has expired")
        if patch_sha256 != prepared.mutation.patch_sha256:
            raise ProposalUnavailableError("proposal hash does not match")
        return prepared

    def _prune_expired(self) -> None:
        now = self.clock()
        for proposal_id, prepared in list(self._pending.items()):
            if now >= prepared.expires_at:
                self._pending.pop(proposal_id, None)
                self.mutator.discard(proposal_id)
                if self.proposal_store is not None:
                    self.proposal_store.discard(
                        self.workspace.id,
                        self.workspace.generation,
                        proposal_id,
                        "expired",
                    )

    def _mark_consumed(self, proposal_id: str) -> None:
        self._consumed[proposal_id] = None
        while len(self._consumed) > self.max_pending:
            self._consumed.popitem(last=False)

    def _raise_persisted_state(self, proposal_id: str, patch_sha256: str) -> None:
        stored = self.proposal_store.load(
            self.workspace.id,
            self.workspace.generation,
            proposal_id,
            now=self.clock(),
        )
        if stored is not None and stored.state == "consumed":
            raise ProposalUnavailableError("proposal already consumed")
        if stored is not None and stored.state == "expired":
            raise ProposalUnavailableError("proposal has expired")
        if stored is not None and stored.mutation.patch_sha256 != patch_sha256:
            raise ProposalUnavailableError("proposal hash does not match")
        raise ProposalUnavailableError("proposal is unavailable")

    def _guarded_repository(self, virtual_path: str) -> GuardedRepository:
        normalized = virtual_path.replace("\\", "/")
        repository_name, separator, _ = normalized.partition("/")
        if not separator or repository_name not in self._repositories:
            raise PermissionError(
                f"path is outside the virtual workspace: {virtual_path}"
            )
        return self.path_guard.bind(self._repositories[repository_name])

    @staticmethod
    def _public_preview(prepared: _PreparedChange) -> Dict[str, Any]:
        mutation = prepared.mutation
        return {
            "status": "previewed",
            "proposal_id": mutation.approval_id,
            "workspace_id": mutation.workspace_id,
            "repository": mutation.repository,
            "virtual_path": mutation.virtual_path,
            "operation": mutation.operation,
            "before_sha256": mutation.before_sha256,
            "after_sha256": mutation.after_sha256,
            "patch_sha256": mutation.patch_sha256,
            "diff": mutation.diff,
            "validation_profile": prepared.validation_profile,
            "expires_at": datetime.fromtimestamp(
                prepared.expires_at, tz=timezone.utc
            ).isoformat(),
        }
