"""
Repository for OperationalSLAPolicy persistence.

These are the operational thresholds the failover detector evaluates live WAN
link telemetry against. Tenant-scoped at every query; soft-deleted rows are
never returned to the detector or the API.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operational_sla_policy import (
    SCOPE_ALL_WAN,
    OperationalSLAPolicy,
)


class OperationalSLAPolicyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        metric: str,
        scope: str = SCOPE_ALL_WAN,
        scope_id: uuid.UUID | None = None,
        description: str | None = None,
        warning_threshold: float | None = None,
        critical_threshold: float | None = None,
        clear_threshold: float | None = None,
        min_consecutive_samples: int = 2,
        is_active: bool = True,
        attributes: dict | None = None,
    ) -> OperationalSLAPolicy:
        row = OperationalSLAPolicy(
            tenant_id=tenant_id,
            name=name,
            metric=metric,
            scope=scope,
            scope_id=scope_id,
            description=description,
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
            clear_threshold=clear_threshold,
            min_consecutive_samples=max(1, int(min_consecutive_samples)),
            is_active=is_active,
            attributes=attributes or {},
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get(
        self, policy_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> OperationalSLAPolicy | None:
        stmt = select(OperationalSLAPolicy).where(
            OperationalSLAPolicy.id == policy_id,
            OperationalSLAPolicy.tenant_id == tenant_id,
            OperationalSLAPolicy.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        scope: str | None = None,
        metric: str | None = None,
        is_active: bool | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[OperationalSLAPolicy]:
        stmt = select(OperationalSLAPolicy).where(
            OperationalSLAPolicy.tenant_id == tenant_id,
            OperationalSLAPolicy.is_deleted.is_(False),
        )
        if scope is not None:
            stmt = stmt.where(OperationalSLAPolicy.scope == scope)
        if metric is not None:
            stmt = stmt.where(OperationalSLAPolicy.metric == metric)
        if is_active is not None:
            stmt = stmt.where(OperationalSLAPolicy.is_active.is_(is_active))
        stmt = (
            stmt.order_by(OperationalSLAPolicy.scope, OperationalSLAPolicy.metric)
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_active(self, tenant_id: uuid.UUID) -> list[OperationalSLAPolicy]:
        """Every policy the detector should evaluate this round."""
        return await self.list(tenant_id, is_active=True, limit=500)

    async def count(
        self, tenant_id: uuid.UUID, *, is_active: bool | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(OperationalSLAPolicy).where(
            OperationalSLAPolicy.tenant_id == tenant_id,
            OperationalSLAPolicy.is_deleted.is_(False),
        )
        if is_active is not None:
            stmt = stmt.where(OperationalSLAPolicy.is_active.is_(is_active))
        return int((await self.db.execute(stmt)).scalar_one())

    async def update(
        self, policy: OperationalSLAPolicy, **fields,
    ) -> OperationalSLAPolicy:
        """Apply the given fields verbatim (including explicit None)."""
        for key, value in fields.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        await self.db.flush()
        await self.db.refresh(policy)
        return policy

    async def delete(self, policy: OperationalSLAPolicy) -> None:
        policy.is_deleted = True
        policy.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
