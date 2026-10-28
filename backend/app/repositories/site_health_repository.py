"""
Repository Pattern: read-only aggregates for the per-site health read (ECC-1).

At most three statements regardless of estate size - sites, device status by
site, active incidents for those sites (skipped when there are no sites). No device or incident rows are loaded into Python.
``tenant_id`` is a required argument and a SQL predicate on every statement.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.incident import Incident
from app.models.site import Site

#: Same active set as ResponseKpis.open_incidents and the analytics aging chart.
ACTIVE_INCIDENT_STATUSES = ("open", "acknowledged", "investigating")


class SiteHealthRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def sites(self, tenant_id: uuid.UUID) -> list[Site]:
        stmt = select(Site).where(Site.tenant_id == tenant_id).order_by(Site.code.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def device_status_by_site(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID | None, dict[str, int]]:
        """``{site_id | None: {raw status: count}}`` in ONE GROUP BY."""
        stmt = (
            select(Device.site_id, Device.status, func.count().label("n"))
            .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
            .group_by(Device.site_id, Device.status)
        )
        out: dict[uuid.UUID | None, dict[str, int]] = {}
        for site_id, status, n in (await self.db.execute(stmt)).all():
            bucket = out.setdefault(site_id, {})
            bucket[status] = bucket.get(status, 0) + int(n)
        return out

    async def active_incidents_by_site(
        self, tenant_id: uuid.UUID, site_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """``{site_id: (active, critical_active)}`` in ONE GROUP BY.

        Bounded to the tenant's own ``site_ids`` (an index range on
        ``ix_incidents_tenant_status_site_active``), so an estate with millions
        of unattributed incidents never scans them. No sites -> no statement.
        """
        if not site_ids:
            return {}
        stmt = (
            select(
                Incident.site_id,
                func.count(),
                func.count().filter(Incident.severity == "critical"),
            )
            .where(
                Incident.tenant_id == tenant_id,
                Incident.is_deleted.is_(False),
                Incident.site_id.in_(site_ids),
                Incident.status.in_(ACTIVE_INCIDENT_STATUSES),
            )
            .group_by(Incident.site_id)
        )
        return {
            site_id: (int(active), int(critical))
            for site_id, active, critical in (await self.db.execute(stmt)).all()
        }
