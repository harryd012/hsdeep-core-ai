"""
Repository Pattern: all direct SQLAlchemy access for WorkflowExecution
lives here, same convention as RunbookExecutionRepository. step_history/
audit_log are JSONB arrays on the row (see models/workflow_execution.py's
"fewer tables" scoping decision) - append_step()/append_audit() do a
read-modify-write on those columns rather than an INSERT into a child
table.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow_execution import WorkflowExecution

_TERMINAL_SUCCESS = "succeeded"
_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "rolled_back")


class WorkflowExecutionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> WorkflowExecution:
        fields.setdefault("queued_at", datetime.now(timezone.utc))
        fields.setdefault("status", "queued")
        execution = WorkflowExecution(**fields)
        self.db.add(execution)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def get(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> WorkflowExecution | None:
        stmt = select(WorkflowExecution).where(
            WorkflowExecution.id == execution_id, WorkflowExecution.tenant_id == tenant_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        workflow_id: uuid.UUID | None = None,
        incident_id: uuid.UUID | None = None,
        alert_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowExecution]:
        """Execution History deliverable - most recent first."""
        stmt = select(WorkflowExecution).where(WorkflowExecution.tenant_id == tenant_id)
        if workflow_id is not None:
            stmt = stmt.where(WorkflowExecution.workflow_id == workflow_id)
        if incident_id is not None:
            stmt = stmt.where(WorkflowExecution.incident_id == incident_id)
        if alert_id is not None:
            stmt = stmt.where(WorkflowExecution.alert_id == alert_id)
        if status is not None:
            stmt = stmt.where(WorkflowExecution.status == status)
        stmt = stmt.order_by(WorkflowExecution.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_pending_approval(self, tenant_id: uuid.UUID, *, limit: int = 100) -> list[WorkflowExecution]:
        stmt = (
            select(WorkflowExecution)
            .where(WorkflowExecution.tenant_id == tenant_id, WorkflowExecution.status == "waiting_approval")
            .order_by(WorkflowExecution.created_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_running(self, tenant_id: uuid.UUID, *, limit: int = 100) -> list[WorkflowExecution]:
        stmt = (
            select(WorkflowExecution)
            .where(WorkflowExecution.tenant_id == tenant_id, WorkflowExecution.status.in_(("running", "retrying")))
            .order_by(WorkflowExecution.started_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -- statistics (Workflow Metrics deliverable) ---------------------------
    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        """Real aggregate counts - never fabricated. success_rate is None
        (not 0.0) when there are zero terminal executions yet, same
        "no signal is not a bad signal" convention as
        RunbookExecutionRepository.success_rate_for_sop()."""
        status_stmt = (
            select(WorkflowExecution.status, func.count())
            .where(WorkflowExecution.tenant_id == tenant_id)
            .group_by(WorkflowExecution.status)
        )
        result = await self.db.execute(status_stmt)
        counts = {status: count for status, count in result.all()}

        terminal_total = sum(counts.get(s, 0) for s in _TERMINAL_STATUSES)
        succeeded = counts.get(_TERMINAL_SUCCESS, 0)
        success_rate = round(succeeded / terminal_total, 3) if terminal_total > 0 else None

        avg_duration_stmt = select(func.avg(WorkflowExecution.duration_seconds)).where(
            WorkflowExecution.tenant_id == tenant_id, WorkflowExecution.duration_seconds.is_not(None)
        )
        avg_result = await self.db.execute(avg_duration_stmt)
        avg_duration = avg_result.scalar_one_or_none()

        return {
            "total_executions": sum(counts.values()),
            "queued": counts.get("queued", 0),
            "waiting_approval": counts.get("waiting_approval", 0),
            "running": counts.get("running", 0) + counts.get("retrying", 0),
            "succeeded": succeeded,
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "rolled_back": counts.get("rolled_back", 0),
            "success_rate": success_rate,
            "avg_duration_seconds": round(float(avg_duration), 1) if avg_duration is not None else None,
        }

    # -- lifecycle transitions -----------------------------------------------
    async def transition(self, execution: WorkflowExecution, *, status: str, **extra_fields) -> WorkflowExecution:
        """Generic status transition - the specific business rules for
        WHICH transitions are valid live in WorkflowEngine/
        WorkflowExecutionTracker, not here (repository owns SQL only,
        same split as every other repository in this codebase)."""
        execution.status = status
        for key, value in extra_fields.items():
            setattr(execution, key, value)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def complete(
        self, execution: WorkflowExecution, *, status: str, failure_reason: str | None = None
    ) -> WorkflowExecution:
        now = datetime.now(timezone.utc)
        started_at = execution.started_at or execution.created_at
        execution.status = status
        execution.completed_at = now
        execution.duration_seconds = max(0, int((now - started_at).total_seconds()))
        execution.failure_reason = failure_reason
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def append_step(self, execution: WorkflowExecution, step_entry: dict) -> WorkflowExecution:
        """Append-only write to step_history - read-modify-write since
        this is a JSONB array column, not a child table (see this
        module's docstring for the scoping decision)."""
        execution.step_history = [*execution.step_history, step_entry]
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def append_audit(
        self, execution: WorkflowExecution, *, event_type: str, message: str, actor_id: uuid.UUID | None = None, details: dict | None = None
    ) -> WorkflowExecution:
        entry = {
            "event_type": event_type,
            "message": message,
            "actor_id": str(actor_id) if actor_id else None,
            "details": details or {},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        execution.audit_log = [*execution.audit_log, entry]
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def set_variables(self, execution: WorkflowExecution, variables: dict) -> WorkflowExecution:
        execution.variables = variables
        await self.db.flush()
        await self.db.refresh(execution)
        return execution
