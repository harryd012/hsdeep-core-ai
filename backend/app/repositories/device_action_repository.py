"""
Repository for DeviceActionRequest - all direct SQLAlchemy access for the
action-request lifecycle lives here (same pattern as DeviceRepository).
The service layer talks to this class only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_action import DeviceActionRequest


class DeviceActionRequestRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, request_id: uuid.UUID, tenant_id: uuid.UUID) -> DeviceActionRequest | None:
        """Tenant-scoped fetch - a cross-tenant id simply does not resolve."""
        stmt = select(DeviceActionRequest).where(
            DeviceActionRequest.id == request_id,
            DeviceActionRequest.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def claim_status(
        self,
        request_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        from_status: str,
        to_status: str,
    ) -> bool:
        """Atomically move a request from one status to another, or report refusal.

        A single conditional UPDATE ... RETURNING IS the claim: PostgreSQL evaluates
        the status predicate and the write as one atomic operation, so when two
        approvers act on the same request concurrently exactly one wins and the
        loser gets False, which must never execute anything. Tenant scope is part
        of the predicate, so a foreign tenant can never claim the row.
        """
        stmt = (
            update(DeviceActionRequest)
            .where(
                DeviceActionRequest.id == request_id,
                DeviceActionRequest.tenant_id == tenant_id,
                DeviceActionRequest.status == from_status,
            )
            .values(status=to_status)
            .returning(DeviceActionRequest.id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_correlation_id(self, correlation_id: str, tenant_id: uuid.UUID) -> DeviceActionRequest | None:
        stmt = select(DeviceActionRequest).where(
            DeviceActionRequest.correlation_id == correlation_id,
            DeviceActionRequest.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DeviceActionRequest]:
        stmt = (
            select(DeviceActionRequest)
            .where(DeviceActionRequest.tenant_id == tenant_id)
            .order_by(desc(DeviceActionRequest.requested_at))
            .limit(limit)
            .offset(offset)
        )
        if device_id is not None:
            stmt = stmt.where(DeviceActionRequest.device_id == device_id)
        if status is not None:
            stmt = stmt.where(DeviceActionRequest.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_tenant(self, tenant_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(DeviceActionRequest).where(
            DeviceActionRequest.tenant_id == tenant_id
        )
        result = await self.db.execute(stmt)
        return int(result.scalar() or 0)

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        action: str,
        classification: str,
        provider_kind: str | None,
        params: dict,
        motivation: dict,
        policy_decision: dict,
        status: str,
        requested_by: uuid.UUID | None,
        correlation_id: str,
        incident_id: uuid.UUID | None = None,
    ) -> DeviceActionRequest:
        request = DeviceActionRequest(
            tenant_id=tenant_id,
            device_id=device_id,
            action=action,
            classification=classification,
            provider_kind=provider_kind,
            params=params or {},
            motivation=motivation or {},
            policy_decision=policy_decision or {},
            status=status,
            requested_by=requested_by,
            requested_at=datetime.now(timezone.utc),
            correlation_id=correlation_id,
            incident_id=incident_id,
        )
        self.db.add(request)
        await self.db.flush()
        return request
