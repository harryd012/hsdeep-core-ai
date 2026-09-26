"""
Repository Pattern: all direct SQLAlchemy access for RunbookExecution
lives here, same convention as AlertHistoryRepository (append-heavy
lifecycle table, no hard delete). New repository for the SOP & Runbook
Intelligence Engine sprint.

This table is the ONLY source of the historical-success-rate signal
SOPMatcher consumes - success_rate_for_sop() below is a real aggregate
over real rows, never fabricated, per sop.py's/runbook_execution.py's
docstrings and the standing "never invent data" project rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import Runbook
from app.models.runbook_execution import RunbookExecution

_TERMINAL_SUCCESS = "succeeded"
_TERMINAL_STATUSES = ("succeeded", "failed", "rolled_back")


class RunbookExecutionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> RunbookExecution:
        fields.setdefault("status", "pending_approval")
        execution = RunbookExecution(**fields)
        self.db.add(execution)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def get(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> RunbookExecution | None:
        stmt = select(RunbookExecution).where(
            RunbookExecution.id == execution_id, RunbookExecution.tenant_id == tenant_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        runbook_id: uuid.UUID | None = None,
        incident_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunbookExecution]:
        """Execution History deliverable - most recent first."""
        stmt = select(RunbookExecution).where(RunbookExecution.tenant_id == tenant_id)
        if runbook_id is not None:
            stmt = stmt.where(RunbookExecution.runbook_id == runbook_id)
        if incident_id is not None:
            stmt = stmt.where(RunbookExecution.incident_id == incident_id)
        if status is not None:
            stmt = stmt.where(RunbookExecution.status == status)
        stmt = stmt.order_by(RunbookExecution.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_pending_approval(self, tenant_id: uuid.UUID, *, limit: int = 100) -> list[RunbookExecution]:
        stmt = (
            select(RunbookExecution)
            .where(RunbookExecution.tenant_id == tenant_id, RunbookExecution.status == "pending_approval")
            .order_by(RunbookExecution.created_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -- lifecycle transitions -----------------------------------------------
    async def approve(self, execution: RunbookExecution, *, approved_by: uuid.UUID | None) -> RunbookExecution:
        execution.status = "approved"
        execution.approved_by = approved_by
        execution.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def reject(self, execution: RunbookExecution, *, rejected_by: uuid.UUID | None, reason: str) -> RunbookExecution:
        execution.status = "rejected"
        execution.approved_by = rejected_by
        execution.rejection_reason = reason
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def start(self, execution: RunbookExecution, *, operator_id: uuid.UUID | None) -> RunbookExecution:
        execution.status = "running"
        execution.operator_id = operator_id
        execution.started_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def complete(
        self,
        execution: RunbookExecution,
        *,
        status: str,
        result_notes: str | None = None,
        rollback_required: bool = False,
    ) -> RunbookExecution:
        """Terminal transition: succeeded | failed. Computes
        duration_seconds from started_at (falls back to created_at if the
        execution was somehow completed without an explicit start() call -
        never raises on a missing started_at)."""
        now = datetime.now(timezone.utc)
        started_at = execution.started_at or execution.created_at
        execution.status = status
        execution.completed_at = now
        execution.duration_seconds = max(0, int((now - started_at).total_seconds()))
        execution.result_notes = result_notes
        execution.rollback_required = rollback_required
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def mark_rolled_back(self, execution: RunbookExecution, *, result_notes: str | None = None) -> RunbookExecution:
        execution.status = "rolled_back"
        execution.rollback_performed = True
        if result_notes is not None:
            execution.result_notes = result_notes
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    # -- SOPMatcher historical signal ----------------------------------------
    async def success_rate_for_sop(self, tenant_id: uuid.UUID, sop_id: uuid.UUID) -> tuple[float | None, int]:
        """(success_rate, total_terminal_executions) across every Runbook
        under this SOP, aggregated from real RunbookExecution rows only.
        Returns (None, 0) when there is no terminal execution history yet -
        SOPMatcher must treat that as "no signal", not as a 0% success
        rate."""
        stmt = (
            select(RunbookExecution.status, func.count())
            .join(Runbook, Runbook.id == RunbookExecution.runbook_id)
            .where(
                RunbookExecution.tenant_id == tenant_id,
                Runbook.sop_id == sop_id,
                RunbookExecution.status.in_(_TERMINAL_STATUSES),
            )
            .group_by(RunbookExecution.status)
        )
        result = await self.db.execute(stmt)
        counts = {status: count for status, count in result.all()}
        total = sum(counts.values())
        if total == 0:
            return None, 0
        succeeded = counts.get(_TERMINAL_SUCCESS, 0)
        return round(succeeded / total, 3), total
