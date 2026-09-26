"""
AuditService — central audit-trail writer for security-sensitive actions.

REUSES the existing `audit_events` table (app/models/knowledge_audit.py::AuditEvent)
- this is NOT a second audit system. Domain services (tickets, SLA, ITSM) already
write AuditEvent rows directly; this service gives the auth/IAM/API-key/entitlement
paths a single, consistent seam so routes don't hand-roll INSERTs.

Design rules:
  * NEVER log passwords, raw API keys, tokens, or other secrets. Callers pass
    only metadata (names, ids, prefixes, permission codes).
  * Best-effort: an audit failure must never break the business request. The
    caller's transaction is used when provided; on error we log and swallow.
  * The caller owns the commit (same convention as the rest of the services).
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_audit import AuditEvent

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        action: str,
        actor_id: uuid.UUID | None = None,
        actor_email: str | None = None,
        actor_type: str = "user",
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        description: str | None = None,
        old_values: dict | None = None,
        new_values: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        context: dict | None = None,
    ) -> None:
        """Write one audit event. Best-effort: failures are logged, never raised."""
        try:
            event = AuditEvent(
                tenant_id=tenant_id,
                actor_id=actor_id if (actor_id is not None and actor_id != uuid.UUID(int=0)) else None,
                actor_email=actor_email,
                actor_type=actor_type,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                description=description,
                old_values=old_values or {},
                new_values=new_values or {},
                ip_address=ip_address,
                user_agent=user_agent,
                context=context or {},
            )
            self.db.add(event)
            # SAVEPOINT: if the insert fails (e.g. an unknown actor_id FK),
            # only the audit write rolls back - the caller's business
            # transaction stays intact instead of being poisoned by a
            # PendingRollbackError.
            async with self.db.begin_nested():
                await self.db.flush()
        except Exception:  # noqa: BLE001 - auditing must never break the request
            logger.exception("Failed to write audit event action=%s tenant=%s", action, tenant_id)

    # -- Convenience wrappers for common security-sensitive actions ---------

    async def record_auth(
        self,
        *,
        tenant_id: uuid.UUID,
        action: str,  # "auth.login" | "auth.login_failed" | "auth.logout"
        email: str,
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
    ) -> None:
        await self.record(
            tenant_id=tenant_id,
            action=action,
            actor_id=user_id,
            actor_email=email,
            entity_type="auth",
            description=f"Login {'succeeded' if success else 'failed'} for {email}",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def record_entity(
        self,
        *,
        tenant_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        actor_id: uuid.UUID | None = None,
        actor_email: str | None = None,
        description: str | None = None,
        old_values: dict | None = None,
        new_values: dict | None = None,
    ) -> None:
        await self.record(
            tenant_id=tenant_id,
            action=action,
            actor_id=actor_id,
            actor_email=actor_email,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            old_values=old_values,
            new_values=new_values,
        )