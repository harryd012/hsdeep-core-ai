"""A failed audit write never breaks the caller's transaction.

Regression: the event was added to the session before its savepoint, so after
the savepoint rolled back the caller's commit tried to insert it again and
failed (an unknown actor_id is the usual cause).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.discovery_scope import DiscoveryScope
from app.models.knowledge_audit import AuditEvent
from app.services.audit_service import AuditService


async def test_failed_audit_write_leaves_the_business_change_committable(tenant):
    async with AsyncSessionLocal() as db:
        scope = DiscoveryScope(tenant_id=tenant.id, name="audit-sp", scope_type="private_cidr", target="10.8.0.0/30")
        db.add(scope)
        await db.flush()
        await AuditService(db).record_entity(
            tenant_id=tenant.id, action="discovery_scope.created", entity_type="discovery_scope",
            entity_id=scope.id, actor_id=uuid.uuid4(),  # no such user: the FK rejects it
        )
        await db.commit()
        scope_id = scope.id

    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(DiscoveryScope).where(DiscoveryScope.id == scope_id))).scalar_one()
        assert (await db.execute(select(AuditEvent).where(AuditEvent.entity_id == scope_id))).first() is None
