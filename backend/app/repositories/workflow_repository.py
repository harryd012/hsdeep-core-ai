"""
Repository Pattern: all direct SQLAlchemy access for Workflow lives here,
same convention as SOPRepository. Single-table design (see
models/workflow.py) - version history is a JSONB array on the row itself,
so there is no separate create_version()/list_versions() pair against a
second table; instead, create_new_version() atomically appends the
current state to version_history and overwrites the row's own
definition/version_number/status fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow


class WorkflowRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Workflow:
        workflow = Workflow(**fields)
        self.db.add(workflow)
        await self.db.flush()
        await self.db.refresh(workflow)
        return workflow

    async def get(self, workflow_id: uuid.UUID, tenant_id: uuid.UUID) -> Workflow | None:
        stmt = select(Workflow).where(
            Workflow.id == workflow_id, Workflow.tenant_id == tenant_id, Workflow.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_number(self, tenant_id: uuid.UUID, workflow_number: str) -> Workflow | None:
        """Used for idempotent seeding/import, same convention as
        SOPRepository.get_by_number()."""
        stmt = select(Workflow).where(
            Workflow.tenant_id == tenant_id,
            Workflow.workflow_number == workflow_number,
            Workflow.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        workflow_type: str | None = None,
        trigger_type: str | None = None,
        is_active: bool | None = True,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Workflow]:
        stmt = select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.is_deleted.is_(False))
        if workflow_type is not None:
            stmt = stmt.where(Workflow.workflow_type == workflow_type)
        if trigger_type is not None:
            stmt = stmt.where(Workflow.trigger_type == trigger_type)
        if is_active is not None:
            stmt = stmt.where(Workflow.is_active == is_active)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                or_(Workflow.name.ilike(like), Workflow.description.ilike(like), Workflow.workflow_number.ilike(like))
            )
        stmt = stmt.order_by(Workflow.workflow_number).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_trigger(self, tenant_id: uuid.UUID, trigger_type: str) -> list[Workflow]:
        """Candidate pool for WorkflowSelector: active, approved workflows
        registered for this trigger_type. Matching against trigger_filter
        happens in Python (see workflow_selector.py) for the same reason
        SOPRepository.list_for_matching() defers criteria matching to
        Python - trigger_filter keys are optional/free-form, not
        expressible as a simple SQL equality filter."""
        stmt = select(Workflow).where(
            Workflow.tenant_id == tenant_id,
            Workflow.is_deleted.is_(False),
            Workflow.is_active.is_(True),
            Workflow.trigger_type == trigger_type,
            Workflow.status == "approved",
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, is_active: bool | None = True) -> int:
        stmt = select(func.count()).select_from(Workflow).where(
            Workflow.tenant_id == tenant_id, Workflow.is_deleted.is_(False)
        )
        if is_active is not None:
            stmt = stmt.where(Workflow.is_active == is_active)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    # -- inline versioning ---------------------------------------------------
    async def create_new_version(
        self, workflow: Workflow, *, definition: dict, change_summary: str | None = None
    ) -> Workflow:
        """Appends the CURRENT state to version_history, then overwrites
        the row with the new draft definition. The new version always
        starts at status="draft" (mirrors SOPVersion) - approve() must be
        called explicitly before it becomes selectable by WorkflowEngine/
        WorkflowSelector."""
        now = datetime.now(timezone.utc)
        history_entry = {
            "version_number": workflow.version_number,
            "definition": workflow.definition,
            "status": workflow.status,
            "change_summary": change_summary,
            "approved_by": str(workflow.approved_by) if workflow.approved_by else None,
            "approved_at": workflow.approved_at.isoformat() if workflow.approved_at else None,
            "archived_at": now.isoformat(),
        }
        workflow.version_history = [*workflow.version_history, history_entry]
        workflow.definition = definition
        workflow.version_number += 1
        workflow.status = "draft"
        workflow.approved_by = None
        workflow.approved_at = None
        await self.db.flush()
        await self.db.refresh(workflow)
        return workflow

    async def approve_current_version(self, workflow: Workflow, *, approved_by: uuid.UUID | None) -> Workflow:
        workflow.status = "approved"
        workflow.approved_by = approved_by
        workflow.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(workflow)
        return workflow

    async def soft_delete(self, workflow: Workflow) -> None:
        workflow.is_deleted = True
        workflow.deleted_at = datetime.now(timezone.utc)
        workflow.is_active = False
        await self.db.flush()
