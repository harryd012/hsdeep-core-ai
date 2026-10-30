"""
Break-glass support access (R1.2): grant lifecycle and the live-grant check
used by the X-Tenant-Id override in app/core/auth.py.

Every grant and revocation is written to the CUSTOMER tenant's audit log in
the same transaction, so the customer can see who entered, why and until
when. Grants cannot be extended: a new grant (with a new reason) is needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.support_access_grant import SupportAccessGrant
from app.repositories.tenant_repository import TenantRepository
from app.services.audit_service import AuditService

MIN_REASON_LENGTH = 10


class SupportAccessError(Exception):
    """Invalid grant request (unknown tenant, own tenant, bad reason/duration)."""


class SupportAccessService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        user_email: str,
        home_tenant_id: uuid.UUID,
        tenant_id: uuid.UUID,
        reason: str,
        duration_minutes: int | None,
        ip_address: str | None = None,
    ) -> SupportAccessGrant:
        cfg = get_settings()
        reason = (reason or "").strip()
        if len(reason) < MIN_REASON_LENGTH:
            raise SupportAccessError(f"State the reason for access (at least {MIN_REASON_LENGTH} characters).")
        minutes = duration_minutes or cfg.support_grant_default_minutes
        if not 1 <= minutes <= cfg.support_grant_max_minutes:
            raise SupportAccessError(f"Duration must be between 1 and {cfg.support_grant_max_minutes} minutes.")
        if tenant_id == home_tenant_id:
            raise SupportAccessError("Your own tenant needs no support grant.")
        if await TenantRepository(self.db).get_existing(tenant_id) is None:
            raise SupportAccessError("Tenant not found.")

        grant = SupportAccessGrant(
            tenant_id=tenant_id, user_id=user_id, user_email=user_email, reason=reason,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )
        self.db.add(grant)
        await self.db.flush()
        await AuditService(self.db).record(
            tenant_id=tenant_id,
            action="support_access.granted",
            actor_id=user_id,
            actor_email=user_email,
            entity_type="support_access_grant",
            entity_id=grant.id,
            description=f"Support access opened by {user_email} for {minutes} min: {reason}",
            new_values={"expires_at": grant.expires_at.isoformat(), "reason": reason, "minutes": minutes},
            ip_address=ip_address,
        )
        return grant

    async def revoke(
        self, grant: SupportAccessGrant, *, actor_id: uuid.UUID | None, actor_email: str, ip_address: str | None = None
    ) -> SupportAccessGrant:
        if grant.revoked_at is None:
            grant.revoked_at = datetime.now(timezone.utc)
            grant.revoked_by_email = actor_email
            await self.db.flush()
            await AuditService(self.db).record(
                tenant_id=grant.tenant_id,
                action="support_access.revoked",
                actor_id=actor_id,
                actor_email=actor_email,
                entity_type="support_access_grant",
                entity_id=grant.id,
                description=f"Support access for {grant.user_email} revoked by {actor_email}",
                ip_address=ip_address,
            )
        return grant

    async def get(self, grant_id: uuid.UUID) -> SupportAccessGrant | None:
        return await self.db.get(SupportAccessGrant, grant_id)

    async def list_for_user(self, user_id: uuid.UUID, *, active_only: bool) -> list[SupportAccessGrant]:
        stmt = select(SupportAccessGrant).where(SupportAccessGrant.user_id == user_id)
        if active_only:
            stmt = stmt.where(*_live())
        return list((await self.db.execute(stmt.order_by(SupportAccessGrant.created_at.desc()).limit(200))).scalars())

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[SupportAccessGrant]:
        stmt = (
            select(SupportAccessGrant)
            .where(SupportAccessGrant.tenant_id == tenant_id)
            .order_by(SupportAccessGrant.created_at.desc())
            .limit(200)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def live_grant(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> SupportAccessGrant | None:
        stmt = (
            select(SupportAccessGrant)
            .where(SupportAccessGrant.user_id == user_id, SupportAccessGrant.tenant_id == tenant_id, *_live())
            .order_by(SupportAccessGrant.expires_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()


def _live():
    return (SupportAccessGrant.revoked_at.is_(None), SupportAccessGrant.expires_at > datetime.now(timezone.utc))


def grant_status(grant: SupportAccessGrant) -> str:
    if grant.revoked_at is not None:
        return "revoked"
    return "active" if grant.expires_at > datetime.now(timezone.utc) else "expired"
