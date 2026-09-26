"""Tenant repository — read-only access to the top-level tenant boundary.

Minimal by design (P0 tenant-context milestone): the platform's interim
authorization model already defines tenant access (a user is pinned to their
`users.tenant_id`; a superuser may act on any tenant via the validated
`X-Tenant-Id` override — see app/core/auth.py). This repository therefore only
serves the authoritative tenant rows that model needs: existence checks for
override validation and the accessible-tenant listing for /api/auth/me.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_existing(self, tenant_id: uuid.UUID) -> Tenant | None:
        """Return the tenant if it exists and is not soft-deleted.

        Used by the auth override validation: an X-Tenant-Id target must be a
        REAL tenant. Suspended (is_active=False) tenants are still returned —
        superusers must be able to administer suspended tenants (reads stay
        open by design; see the write-guard in app/api/routes/auth.py).
        """
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.id == tenant_id,
                Tenant.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Tenant]:
        """Every live tenant (including suspended ones, flagged via is_active).

        Serves the superuser accessible-tenant list: per the interim MSP model
        a superuser is authorized for any tenant, so "accessible" = all live
        rows. Normal users never hit this path (their access is their own
        tenant row, resolved authoritatively from users.tenant_id).
        """
        result = await self.session.execute(
            select(Tenant)
            .where(Tenant.is_deleted.is_(False))
            .order_by(Tenant.name.asc())
        )
        return list(result.scalars().all())