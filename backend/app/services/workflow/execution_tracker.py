"""
Workflow Execution Tracker - Enterprise Workflow Automation Engine sprint.

Owns the WorkflowExecution-level lifecycle actions an operator/API caller
triggers directly (approve/reject/pause/resume/cancel/rollback) - distinct
from WorkflowEngine, which owns NODE-level graph traversal. Same split as
SOP & Runbook's ExecutionTracker (RunbookExecution lifecycle) vs.
CollectionOrchestrator (per-run driving logic), applied one level up.

Approving/rejecting here specifically targets an `approval` node parked
at "waiting_approval" - it writes the real approval fact into
`execution.variables["_approvals"]` (read back by
ApprovalNodeExecutor.execute() on the next run_step() call) rather than
guessing which node is "the" pending approval; the caller must pass the
exact node_id currently parked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.workflow_execution import WorkflowExecution
from app.repositories.workflow_execution_repository import WorkflowExecutionRepository


class WorkflowExecutionNotFound(Exception):
    pass


class InvalidWorkflowExecutionTransition(Exception):
    pass


class WorkflowExecutionTracker:
    def __init__(self, execution_repo: WorkflowExecutionRepository):
        self.execution_repo = execution_repo

    async def approve(
        self, execution: WorkflowExecution, *, node_id: str, approved_by: uuid.UUID | None, comment: str | None = None
    ) -> WorkflowExecution:
        self._require_status(execution, "waiting_approval", action="approve")
        approvals = {**execution.variables.get("_approvals", {}), node_id: "approved"}
        variables = {**execution.variables, "_approvals": approvals}
        execution = await self.execution_repo.set_variables(execution, variables)
        execution = await self.execution_repo.transition(
            execution, status="running", approved_by=approved_by, approved_at=datetime.now(timezone.utc)
        )
        return await self.execution_repo.append_audit(
            execution,
            event_type="approved",
            message=f"Approval node '{node_id}' approved." + (f" Comment: {comment}" if comment else ""),
            actor_id=approved_by,
        )

    async def reject(
        self, execution: WorkflowExecution, *, node_id: str, rejected_by: uuid.UUID | None, reason: str
    ) -> WorkflowExecution:
        self._require_status(execution, "waiting_approval", action="reject")
        approvals = {**execution.variables.get("_approvals", {}), node_id: "rejected"}
        variables = {**execution.variables, "_approvals": approvals}
        execution = await self.execution_repo.set_variables(execution, variables)
        execution = await self.execution_repo.transition(execution, status="running")
        return await self.execution_repo.append_audit(
            execution, event_type="rejected", message=f"Approval node '{node_id}' rejected: {reason}", actor_id=rejected_by
        )

    async def pause(self, execution: WorkflowExecution, *, actor_id: uuid.UUID | None, reason: str | None = None) -> WorkflowExecution:
        if execution.status != "running":
            raise InvalidWorkflowExecutionTransition(
                f"Cannot pause execution {execution.id}: status is {execution.status!r}, expected 'running'"
            )
        execution = await self.execution_repo.transition(execution, status="paused")
        return await self.execution_repo.append_audit(
            execution, event_type="paused", message=reason or "Execution manually paused.", actor_id=actor_id
        )

    async def resume(self, execution: WorkflowExecution, *, actor_id: uuid.UUID | None) -> WorkflowExecution:
        if execution.status != "paused":
            raise InvalidWorkflowExecutionTransition(
                f"Cannot resume execution {execution.id}: status is {execution.status!r}, expected 'paused'"
            )
        execution = await self.execution_repo.transition(execution, status="running")
        return await self.execution_repo.append_audit(execution, event_type="resumed", message="Execution manually resumed.", actor_id=actor_id)

    async def cancel(self, execution: WorkflowExecution, *, actor_id: uuid.UUID | None, reason: str | None = None) -> WorkflowExecution:
        if execution.status in ("succeeded", "failed", "cancelled", "rolled_back"):
            raise InvalidWorkflowExecutionTransition(
                f"Cannot cancel execution {execution.id}: already terminal (status={execution.status!r})"
            )
        execution = await self.execution_repo.append_audit(
            execution, event_type="cancelled", message=reason or "Execution manually cancelled.", actor_id=actor_id
        )
        return await self.execution_repo.complete(execution, status="cancelled", failure_reason=reason)

    async def rollback(self, execution: WorkflowExecution, *, actor_id: uuid.UUID | None, notes: str | None = None) -> WorkflowExecution:
        if execution.status != "failed" or not execution.rollback_required:
            raise InvalidWorkflowExecutionTransition(
                f"Execution {execution.id} is not eligible for rollback "
                f"(status={execution.status!r}, rollback_required={execution.rollback_required})"
            )
        execution = await self.execution_repo.transition(execution, status="rolled_back", rollback_performed=True)
        return await self.execution_repo.append_audit(
            execution, event_type="rolled_back", message=notes or "Execution rolled back.", actor_id=actor_id
        )

    @staticmethod
    def _require_status(execution: WorkflowExecution, expected: str, *, action: str) -> None:
        if execution.status != expected:
            raise InvalidWorkflowExecutionTransition(
                f"Cannot {action} execution {execution.id}: status is {execution.status!r}, expected {expected!r}"
            )
