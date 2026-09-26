"""
Infrastructure service layer.

Powers the Infrastructure page's Site cards and Site detail view. All
aggregation is backed exclusively by existing repository queries (per Clean
Architecture, same convention as dashboard_service.py) - this service never
issues its own SQLAlchemy queries, and no new repository classes/methods
were added for this task.

Documented judgment call (mirrors dashboard_service.py's docstring style):

devices_critical in SiteSummary is always 0. Device.status only has
up|down|warning|unknown values - there is no distinct "critical" signal
anywhere in the repository layer. Rather than fabricate a critical count by
aliasing "down" devices as also "critical" (which would double-count the
same devices under two different labels), devices_critical is left at 0
with this explicit note. See schemas/site.py's SiteSummary docstring for
the same call.
"""
from __future__ import annotations

import uuid
from collections import Counter

from app.models.site import Site
from app.repositories.device_repository import DeviceRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.site import SiteSummary
from app.services.infrastructure_overview_service import (
    HEALTH_CRITICAL,
    HEALTH_HEALTHY,
    HEALTH_UNKNOWN,
    HEALTH_WARNING,
    device_health_bucket,
)

# Matches dashboard_service.py's _DEVICE_FETCH_LIMIT convention - used here
# only to fetch a single site's devices (to compute the best-effort "most
# common vendor" in Python), not a tenant-wide fetch, but still bounded
# generously so a large site's device list is never silently truncated.
_DEVICE_FETCH_LIMIT = 100_000


class InfrastructureService:
    def __init__(self, site_repo: SiteRepository, device_repo: DeviceRepository):
        self.site_repo = site_repo
        self.device_repo = device_repo

    async def list_site_summaries(self, tenant_id: uuid.UUID) -> list[SiteSummary]:
        sites = await self.site_repo.list(tenant_id)

        summaries: list[SiteSummary] = []
        for site in sites:
            status_counts = await self.device_repo.count_by_status_for_site(site.id)
            devices = await self.device_repo.list(
                tenant_id, site_id=site.id, limit=_DEVICE_FETCH_LIMIT
            )

            # Bucket the RAW stored statuses through the canonical vocabulary in
            # infrastructure_overview_service, so this endpoint and the
            # canonical /infrastructure/overview can never disagree.
            #
            # This fixes a real defect: the counts used to be read with
            # `status_counts.get("up", 0)` while the live database ALSO stores
            # "online" for a healthy device (93 rows platform-wide), so every
            # `online` device was invisible to site rollups. The same canonical
            # bucket now also feeds devices_offline, which catches
            # down|offline|critical|failed|error rather than only "down".
            buckets = {
                HEALTH_HEALTHY: 0, HEALTH_WARNING: 0,
                HEALTH_CRITICAL: 0, HEALTH_UNKNOWN: 0,
            }
            for status, count in status_counts.items():
                buckets[device_health_bucket(status)] += count

            summaries.append(
                SiteSummary(
                    id=site.id,
                    name=site.name,
                    code=site.code,
                    address=site.address,
                    vendor=self._most_common_vendor(devices),
                    devices_total=sum(status_counts.values()),
                    devices_online=buckets[HEALTH_HEALTHY],
                    devices_offline=buckets[HEALTH_CRITICAL],
                    devices_warning=buckets[HEALTH_WARNING],
                    # The canonical critical bucket is exposed as
                    # devices_offline above; this stays 0 so the four fields
                    # never double-count the same device.
                    devices_critical=0,
                    # New: makes the four buckets plus unknown sum exactly to
                    # devices_total, so the UI can always reconcile the parts.
                    devices_unknown=buckets[HEALTH_UNKNOWN],
                )
            )
        return summaries

    @staticmethod
    def _most_common_vendor(devices: list) -> str | None:
        """Most common non-null Device.vendor among the given devices, or
        None if there are no devices with a vendor set, or there's a tie for
        most common (no single, unambiguous most-common vendor)."""
        vendors = [d.vendor for d in devices if d.vendor]
        if not vendors:
            return None
        counts = Counter(vendors)
        top_vendor, top_count = counts.most_common(1)[0]
        # A tie means there's no single "most common" vendor - be honest
        # about that instead of arbitrarily picking one.
        tied = [v for v, c in counts.items() if c == top_count]
        if len(tied) > 1:
            return None
        return top_vendor

    async def get_site_detail(self, site_id: uuid.UUID, tenant_id: uuid.UUID) -> Site | None:
        return await self.site_repo.get(site_id, tenant_id)
