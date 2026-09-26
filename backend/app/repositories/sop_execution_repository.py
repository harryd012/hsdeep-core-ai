"""
Repository Pattern: all direct SQLAlchemy access for SOPExecution lives here.
Same convention as RunbookExecutionRepository — append-heavy lifecycle table.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sop_execution import SOPExecution


class SOPExecutionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> SOPExecution:
        execution = SOPExecution(**fields)
        self.db.add(execution)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def get(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> SOPExecution | None:
        stmt = select(SOPExecution).where(
            SOPExecution.id == execution_id, SOPExecution.tenant_id == tenant_id, SOPExecution.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_for_ticket(
        self, ticket_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> SOPExecution | None:
        """Return any in-progress (non-terminal) SOPExecution for this ticket."""
        stmt = select(SOPExecution).where(
            SOPExecution.ticket_id == ticket_id,
            SOPExecution.tenant_id == tenant_id,
            SOPExecution.is_deleted.is_(False),
            SOPExecution.status.in_("running", "awaiting_manual", "awaiting_approval"),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_ticket(
        self, ticket_id: uuid.UUID, tenant_id: uuid.UUID, *, limit: int = 50
    ) -> list[SOPExecution]:
        stmt = (
            select(SOPExecution)
            .where(
                SOPExecution.ticket_id == ticket_id,
                SOPExecution.tenant_id == tenant_id,
                SOPExecution.is_deleted.is_(False),
            )
            .order_by(SOPExecution.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(self, execution: SOPExecution, **fields) -> SOPExecution:
        for key, value in fields.items():
            if hasattr(execution, key):
                setattr(execution, key, value)
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def record_step_result(
        self, execution: SOPExecution, *, step_index: int, status: str,
        output: dict | None = None, error: str | None = None,
    ) -> SOPExecution:
        """Append a step result to the execution's step_results JSONB array."""
        entry = {
            "step_index": step_index,
            "status": status,
            "output": output or {},
            "error": error,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        execution.step_results = [*execution.step_results, entry]
        await self.db.flush()
        await self.db.refresh(execution)
        return execution

    async def complete(
        self, execution: SOPExecution, *, success: bool, result_notes: str | None = None,
        rollback_required: bool = False,
    ) -> SOPExecution:
        now = datetime.now(timezone.utc)
        started_at = execution.started_at or execution.created_at
        execution.status = "succeeded" if success else "failed"
        execution.completed_at = now
        execution.duration_seconds = max(0, int((now - started_at).total_seconds()))
        execution.result_notes = result_notes
        execution.rollback_required = rollback_required
        await self.db.flush()
        await self.db.refresh(execution)
        return execution
