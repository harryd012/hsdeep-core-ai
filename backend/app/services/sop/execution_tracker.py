"""
Execution Tracker.

Owns the RunbookExecution lifecycle transitions (the manual approval
workflow + execution history deliverable):

    pending_approval -> approved -> running -> succeeded | failed
                     \\-> rejected                     \\-> rolled_back

Every transition is a thin wrapper around RunbookExecutionRepository -
this class owns the VALID-TRANSITION business rule (e.g. you cannot start
an execution that was never approved), the repository owns the SQL, same
"service owns business rules, repository owns SQL" split as every other
service in this codebase.
"""
from __future__ import annotations

import uuid

from app.models.runbook_execution import RunbookExecution
from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.runbook_repository import RunbookRepository


class RunbookExecutionNotFound(Exception):
    pass


class InvalidExecutionTransition(Exception):
    pass


class ExecutionTracker:
    def __init__(self, execution_repo: RunbookExecutionRepository, runbook_repo: RunbookRepository):
        self.execution_repo = execution_repo
        self.runbook_repo = runbook_repo

    async def request_execution(
        self,
        *,
        tenant_id: uuid.UUID,
        runbook_id: uuid.UUID,
        incident_id: uuid.UUID | None,
        operator_id: uuid.UUID | None,
    ) -> RunbookExecution:
        """Creates a new RunbookExecution row. Starts at pending_approval
        even when the runbook's requires_approval is False - approve()
        can be called immediately by the same actor in that case, but the
        row still records that an approval step occurred (audit trail),
        rather than skipping straight to running and losing that record."""
        runbook = await self.runbook_repo.get(runbook_id, tenant_id)
        if runbook is None:
            raise RunbookExecutionNotFound(f"Runbook {runbook_id} not found")
        return await self.execution_repo.create(
            tenant_id=tenant_id,
            runbook_id=runbook_id,
            incident_id=incident_id,
            operator_id=operator_id,
            status="pending_approval",
        )

    async def approve(self, execution: RunbookExecution, *, approved_by: uuid.UUID | None) -> RunbookExecution:
        self._require_status(execution, "pending_approval", action="approve")
        return await self.execution_repo.approve(execution, approved_by=approved_by)

    async def reject(self, execution: RunbookExecution, *, rejected_by: uuid.UUID | None, reason: str) -> RunbookExecution:
        self._require_status(execution, "pending_approval", action="reject")
        return await self.execution_repo.reject(execution, rejected_by=rejected_by, reason=reason)

    async def start(self, execution: RunbookExecution, *, operator_id: uuid.UUID | None) -> RunbookExecution:
        self._require_status(execution, "approved", action="start")
        return await self.execution_repo.start(execution, operator_id=operator_id)

    async def complete(
        self, execution: RunbookExecution, *, success: bool, result_notes: str | None = None, rollback_required: bool = False
    ) -> RunbookExecution:
        self._require_status(execution, "running", action="complete")
        status = "succeeded" if success else "failed"
        return await self.execution_repo.complete(
            execution, status=status, result_notes=result_notes, rollback_required=rollback_required
        )

    async def rollback(self, execution: RunbookExecution, *, result_notes: str | None = None) -> RunbookExecution:
        if execution.status != "failed" or not execution.rollback_required:
            raise InvalidExecutionTransition(
                f"Execution {execution.id} is not eligible for rollback "
                f"(status={execution.status!r}, rollback_required={execution.rollback_required})"
            )
        return await self.execution_repo.mark_rolled_back(execution, result_notes=result_notes)

    @staticmethod
    def _require_status(execution: RunbookExecution, expected: str, *, action: str) -> None:
        if execution.status != expected:
            raise InvalidExecutionTransition(
                f"Cannot {action} execution {execution.id}: status is {execution.status!r}, expected {expected!r}"
            )
