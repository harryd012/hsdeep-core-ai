"""
Site Health service (ECC-1): per-site device health + active incidents.

Device health reuses ``device_health_buckets`` from the Operational
Intelligence service - the single device-health rule - so the honeycomb and
the estate donut always agree. Query budget: at most 3 statements (see repository).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.repositories.site_health_repository import SiteHealthRepository
from app.schemas.dashboard import DeviceHealthBuckets
from app.schemas.site_health import SiteHealth, SiteHealthItem
from app.services.operational_intelligence_service import device_health_buckets

_WORST_ORDER = {"down": 0, "critical": 1, "warning": 2, "healthy": 3, "unknown": 4}


def worst_health(buckets: DeviceHealthBuckets) -> str:
    """Worst non-empty bucket. Only-unknown or no devices -> ``unknown``."""
    for key in ("down", "critical", "warning", "healthy"):
        if getattr(buckets, key) > 0:
            return key
    return "unknown"


class SiteHealthService:
    def __init__(self, repo: SiteHealthRepository):
        self.repo = repo

    async def snapshot(self, tenant_id: uuid.UUID) -> SiteHealth:
        sites = await self.repo.sites(tenant_id)
        statuses = await self.repo.device_status_by_site(tenant_id)
        incidents = await self.repo.active_incidents_by_site(tenant_id, [s.id for s in sites])

        items: list[SiteHealthItem] = []
        for site in sites:
            buckets = device_health_buckets(statuses.get(site.id, {}))
            active, critical = incidents.get(site.id, (0, 0))
            items.append(
                SiteHealthItem(
                    site_id=site.id,
                    code=site.code,
                    name=site.name,
                    region=site.region,
                    criticality=site.criticality,
                    devices=buckets,
                    open_incidents=active,
                    critical_incidents=critical,
                    worst_health=worst_health(buckets),
                )
            )
        items.sort(key=lambda i: (_WORST_ORDER[i.worst_health], -i.open_incidents, i.code))

        return SiteHealth(
            generated_at=datetime.now(timezone.utc),
            sites=items,
            unassigned_devices=sum(statuses.get(None, {}).values()),
        )
