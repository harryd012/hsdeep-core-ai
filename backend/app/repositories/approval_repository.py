"""
Repository for the existing ApprovalRequest / ApprovalHistory models
(app/models/approval.py). Fills a gap: the Approval Engine models existed
but had no repository seam - the Device Action Plane is their first
service-grade consumer, so the persistence access lives here (one seam,
same convention as every other repository).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalHistory, ApprovalRequest


class ApprovalRequestRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, approval_id: uuid.UUID, tenant_id: uuid.UUID) -> ApprovalRequest | None:
        """Tenant-scoped fetch."""
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_entity(
        self, entity_type: str, entity_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ApprovalRequest | None:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.entity_type == entity_type,
            ApprovalRequest.entity_id == entity_id,
            ApprovalRequest.tenant_id == tenant_id,
        ).order_by(ApprovalRequest.requested_at.desc())
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        approval_type: str,
        requested_by: uuid.UUID | None,
        required_role: str | None,
        context: dict,
        deadline: datetime | None = None,
    ) -> ApprovalRequest:
        row = ApprovalRequest(
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            approval_type=approval_type,
            status="pending",
            requested_by=requested_by,
            requested_at=datetime.now(timezone.utc),
            required_role=required_role,
            context=context or {},
            deadline=deadline,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def resolve(
        self,
        approval: ApprovalRequest,
        *,
        status: str,
        resolved_by: uuid.UUID | None,
        note: str | None,
    ) -> ApprovalRequest:
        """Move an approval to a terminal status (approved/denied) and
        stamp the resolver. History rows are written by the caller via
        record_history so the note/context land in the audit trail."""
        approval.status = status
        approval.resolved_by = resolved_by
        approval.resolved_at = datetime.now(timezone.utc)
        approval.resolution_note = note
        await self.db.flush()
        return approval

    async def record_history(
        self,
        *,
        approval: ApprovalRequest,
        actor_id: uuid.UUID | None,
        actor_name: str | None,
        action_type: str,
        note: str | None,
        context: dict | None = None,
    ) -> ApprovalHistory:
        history = ApprovalHistory(
            tenant_id=approval.tenant_id,
            approval_request_id=approval.id,
            actor_id=actor_id,
            actor_name=actor_name,
            action_type=action_type,
            note=note,
            context=context or {},
        )
        self.db.add(history)
        await self.db.flush()
        return history
