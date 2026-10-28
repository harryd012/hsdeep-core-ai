"""
Hosts & Devices Explorer service (GET /api/explorer/hosts).

ONE tenant-scoped, read-only payload: the filtered/paginated host table plus
facet counts for every filterable dimension (the Dynatrace-Hosts interaction
model: a facet sidebar with counts next to a health-sorted table).

Query budget is fixed and independent of estate size:
    1x count   1x page   6x facets (health, status, device_type, vendor,
                                    criticality, environment)
= 8 statements, run sequentially (one AsyncSession cannot run concurrently).
Nothing is estimated; no device rows beyond the requested page cross the wire.
"""
from __future__ import annotations

import datetime as dt
import uuid

from app.repositories.explorer_hosts_repository import (
    FACET_DIMENSIONS,
    HEALTH_ORDER,
    ExplorerHostsRepository,
    HostFilters,
)
from app.schemas.explorer_hosts import (
    FacetValue,
    HostAlertCounts,
    HostFacets,
    HostRow,
    HostsExplorer,
)

MAX_PAGE_SIZE = 200


class ExplorerHostsService:
    def __init__(self, repo: ExplorerHostsRepository):
        self.repo = repo

    async def snapshot(
        self,
        tenant_id: uuid.UUID,
        filters: HostFilters,
        limit: int = 50,
        offset: int = 0,
        now: dt.datetime | None = None,
    ) -> HostsExplorer:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        total = await self.repo.count_hosts(tenant_id, filters)
        raw_rows = await self.repo.list_hosts(tenant_id, filters, limit, offset)

        facet_values: dict[str, list[FacetValue]] = {}
        for dimension in FACET_DIMENSIONS:
            counts = await self.repo.facet_counts(tenant_id, filters, dimension)
            if dimension == "health":
                by_value = dict(counts)
                # Always the three buckets, severity-ordered; a bucket with no
                # hosts is a measured zero, not a missing facet.
                facet_values[dimension] = [
                    FacetValue(value=v, count=by_value.get(v, 0)) for v in HEALTH_ORDER
                ]
            else:
                facet_values[dimension] = [FacetValue(value=v, count=n) for v, n in counts]

        return HostsExplorer(
            generated_at=now or dt.datetime.now(dt.timezone.utc),
            total=total,
            limit=limit,
            offset=offset,
            rows=[self._row(r) for r in raw_rows],
            facets=HostFacets(**facet_values),
        )

    @staticmethod
    def _row(r: dict) -> HostRow:
        return HostRow(
            id=r["id"],
            name=r["name"],
            hostname=r.get("hostname"),
            ip_address=r.get("ip_address"),
            device_type=r["device_type"],
            vendor=r.get("vendor"),
            provider=r.get("provider"),
            status=r["status"],
            criticality=r.get("criticality"),
            environment=r.get("environment"),
            lifecycle_state=r["lifecycle_state"],
            site_id=r.get("site_id"),
            site_name=r.get("site_name"),
            health=r["health"],
            alerts=HostAlertCounts(
                critical=int(r.get("alert_critical") or 0),
                warning=int(r.get("alert_warning") or 0),
                info=int(r.get("alert_info") or 0),
            ),
            last_seen_at=r.get("last_seen_at"),
            last_telemetry_at=r.get("last_telemetry_at"),
        )
