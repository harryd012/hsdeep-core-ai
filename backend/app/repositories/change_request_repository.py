"""Change Request repository for database access."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest


class ChangeRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, tenant_id: uuid.UUID, change_number: str, title: str,
                     description: str, change_type: str = "config", risk_level: str = "medium",
                     priority: str = "normal", business_justification: str | None = None,
                     implementation_plan: str | None = None, backout_plan: str | None = None,
                     test_plan: str | None = None, validation_plan: str | None = None,
                     security_impact: str | None = None, affected_devices: list | None = None,
                     affected_services: list | None = None, downtime_expected: bool = False,
                     downtime_duration_minutes: int = 0,
                     maintenance_window_start: datetime | None = None,
                     maintenance_window_end: datetime | None = None,
                     requested_by: uuid.UUID | None = None) -> ChangeRequest:
        change = ChangeRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            change_number=change_number,
            title=title,
            description=description,
            change_type=change_type,
            risk_level=risk_level,
            priority=priority,
            business_justification=business_justification,
            implementation_plan=implementation_plan,
            backout_plan=backout_plan,
            test_plan=test_plan,
            validation_plan=validation_plan,
            security_impact=security_impact,
            affected_devices=affected_devices or [],
            affected_services=affected_services or [],
            downtime_expected=downtime_expected,
            downtime_duration_minutes=downtime_duration_minutes,
            maintenance_window_start=maintenance_window_start,
            maintenance_window_end=maintenance_window_end,
            requested_by=requested_by,
        )
        self.session.add(change)
        await self.session.flush()
        return change

    async def get(self, change_id: uuid.UUID, tenant_id: uuid.UUID) -> ChangeRequest | None:
        result = await self.session.execute(
            select(ChangeRequest).where(ChangeRequest.id == change_id, ChangeRequest.tenant_id == tenant_id, ChangeRequest.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def list(self, tenant_id: uuid.UUID, *, status: str | None = None,
                   change_type: str | None = None, risk_level: str | None = None,
                   requested_by: uuid.UUID | None = None, limit: int = 100, offset: int = 0) -> list[ChangeRequest]:
        stmt = select(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.is_deleted.is_(False))
        if status:
            stmt = stmt.where(ChangeRequest.status == status)
        if change_type:
            stmt = stmt.where(ChangeRequest.change_type == change_type)
        if risk_level:
            stmt = stmt.where(ChangeRequest.risk_level == risk_level)
        if requested_by:
            stmt = stmt.where(ChangeRequest.requested_by == requested_by)
        stmt = stmt.order_by(ChangeRequest.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.is_deleted.is_(False))
        if status:
            stmt = stmt.where(ChangeRequest.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)
        by_status = {}
        for status in ["draft", "pending_approval", "approved", "scheduled", "in_progress", "implemented", "verifying", "completed", "rejected", "rolled_back", "cancelled"]:
            by_status[status] = await self.count(tenant_id, status=status)
        by_type = {}
        for change_type in ["config", "software", "hardware", "security", "network", "cloud", "database", "application", "other"]:
            stmt = select(func.count()).select_from(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.is_deleted.is_(False), ChangeRequest.change_type == change_type)
            result = await self.session.execute(stmt)
            by_type[change_type] = result.scalar_one()
        by_risk = {}
        for risk in ["low", "medium", "high", "critical"]:
            stmt = select(func.count()).select_from(ChangeRequest).where(ChangeRequest.tenant_id == tenant_id, ChangeRequest.is_deleted.is_(False), ChangeRequest.risk_level == risk)
            result = await self.session.execute(stmt)
            by_risk[risk] = result.scalar_one()
        return {"total_changes": total, "by_status": by_status, "by_type": by_type, "by_risk_level": by_risk}

    async def update(self, change: ChangeRequest, **fields) -> ChangeRequest:
        for key, value in fields.items():
            setattr(change, key, value)
        change.updated_at = datetime.utcnow()
        await self.session.flush()
        return change

    async def delete(self, change: ChangeRequest) -> None:
        change.is_deleted = True
        change.deleted_at = datetime.utcnow()
        change.updated_at = datetime.utcnow()
        await self.session.flush()
