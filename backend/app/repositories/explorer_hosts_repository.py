"""
Repository Pattern: read-only SQL for the Hosts & Devices Explorer.

Every method is one aggregate / one page query; nothing loads the whole device
table into Python. Tenant scope is applied in SQL on every statement
(`tenant_id` is a required argument, and the Site join repeats the predicate).

Health model: a per-device sub-select counts that device's OPEN
(open + acknowledged), non-suppressed alerts by severity; the derived health
bucket is ``critical`` / ``warning`` / ``no_alerts`` (see schemas/explorer_hosts).

SQL-RENDERING RULE (same as incident_analytics_repository): any CASE that is
both selected and grouped has its literals inlined with ``literal_column`` so
both occurrences render identical text and PostgreSQL accepts the GROUP BY.
The inlined values are module constants, never user input. User-supplied
filter values are always bound parameters.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.site import Site

ACTIVE_ALERT_STATUSES = ("open", "acknowledged")

#: Facet dimension -> the Device column it filters on. ``health`` is derived.
DIMENSION_COLUMNS = {
    "status": Device.status,
    "device_type": Device.device_type,
    "vendor": Device.vendor,
    "criticality": Device.criticality,
    "environment": Device.environment,
}
FACET_DIMENSIONS = ("health", *DIMENSION_COLUMNS.keys())
HEALTH_ORDER = ("critical", "warning", "no_alerts")


@dataclass(frozen=True)
class HostFilters:
    search: str | None = None
    health: str | None = None
    status: str | None = None
    device_type: str | None = None
    vendor: str | None = None
    criticality: str | None = None
    environment: str | None = None
    site_id: uuid.UUID | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ExplorerHostsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- building blocks ---------------------------------------------------

    @staticmethod
    def _alerts_subquery(tenant_id: uuid.UUID):
        """Per-device open-alert counts by severity (one row per alerting device)."""
        return (
            select(
                Alert.device_id.label("device_id"),
                func.count().filter(Alert.severity == "critical").label("critical"),
                func.count().filter(Alert.severity == "warning").label("warning"),
                func.count().filter(Alert.severity == "info").label("info"),
            )
            .where(
                Alert.tenant_id == tenant_id,
                Alert.is_deleted.is_(False),
                Alert.status.in_(ACTIVE_ALERT_STATUSES),
                Alert.is_suppressed.is_(False),
                Alert.device_id.is_not(None),
            )
            .group_by(Alert.device_id)
            .subquery()
        )

    @staticmethod
    def _count_or_zero(column):
        """coalesce(count, 0) with the zero INLINED: a bound ``0`` would render a
        different placeholder in SELECT vs GROUP BY (see module docstring)."""
        return func.coalesce(column, literal_column("0"))

    @classmethod
    def _health_expr(cls, aa):
        zero = literal_column("0")
        return case(
            (cls._count_or_zero(aa.c.critical) > zero, literal_column("'critical'")),
            (cls._count_or_zero(aa.c.warning) > zero, literal_column("'warning'")),
            else_=literal_column("'no_alerts'"),
        )

    @classmethod
    def _severity_rank(cls, aa):
        zero = literal_column("0")
        return case(
            (cls._count_or_zero(aa.c.critical) > zero, literal_column("0")),
            (cls._count_or_zero(aa.c.warning) > zero, literal_column("1")),
            else_=literal_column("2"),
        )

    def _conditions(
        self,
        tenant_id: uuid.UUID,
        f: HostFilters,
        health_expr,
        exclude: str | None = None,
    ) -> list[Any]:
        conds: list[Any] = [Device.tenant_id == tenant_id, Device.is_deleted.is_(False)]
        if f.search:
            pattern = f"%{_escape_like(f.search.strip())}%"
            conds.append(
                or_(
                    Device.name.ilike(pattern, escape="\\"),
                    Device.hostname.ilike(pattern, escape="\\"),
                    Device.ip_address.ilike(pattern, escape="\\"),
                )
            )
        if f.site_id is not None:
            conds.append(Device.site_id == f.site_id)
        if f.health and exclude != "health":
            conds.append(health_expr == f.health)
        for dim, column in DIMENSION_COLUMNS.items():
            value = getattr(f, dim)
            if value and exclude != dim:
                conds.append(column == value)
        return conds

    # -- queries -----------------------------------------------------------

    async def count_hosts(self, tenant_id: uuid.UUID, f: HostFilters) -> int:
        aa = self._alerts_subquery(tenant_id)
        health = self._health_expr(aa)
        stmt = (
            select(func.count())
            .select_from(Device)
            .outerjoin(aa, aa.c.device_id == Device.id)
            .where(*self._conditions(tenant_id, f, health))
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def facet_counts(
        self, tenant_id: uuid.UUID, f: HostFilters, dimension: str, limit: int = 20
    ) -> list[tuple[str, int]]:
        """(value, count) for one facet, with that facet's own filter removed."""
        if dimension not in FACET_DIMENSIONS:
            raise ValueError(f"unknown facet dimension: {dimension}")
        aa = self._alerts_subquery(tenant_id)
        health = self._health_expr(aa)
        column = health if dimension == "health" else DIMENSION_COLUMNS[dimension]
        stmt = (
            select(column.label("v"), func.count().label("n"))
            .select_from(Device)
            .outerjoin(aa, aa.c.device_id == Device.id)
            .where(*self._conditions(tenant_id, f, health, exclude=dimension))
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(str(r.v), int(r.n)) for r in rows]

    async def list_hosts(
        self, tenant_id: uuid.UUID, f: HostFilters, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """One page of hosts, worst health first, then by name."""
        aa = self._alerts_subquery(tenant_id)
        health = self._health_expr(aa)
        stmt = (
            select(
                Device.id,
                Device.name,
                Device.hostname,
                Device.ip_address,
                Device.device_type,
                Device.vendor,
                Device.provider,
                Device.status,
                Device.criticality,
                Device.environment,
                Device.lifecycle_state,
                Device.site_id,
                Site.name.label("site_name"),
                Device.last_seen_at,
                Device.last_telemetry_at,
                health.label("health"),
                self._count_or_zero(aa.c.critical).label("alert_critical"),
                self._count_or_zero(aa.c.warning).label("alert_warning"),
                self._count_or_zero(aa.c.info).label("alert_info"),
            )
            .select_from(Device)
            .outerjoin(aa, aa.c.device_id == Device.id)
            .outerjoin(Site, (Site.id == Device.site_id) & (Site.tenant_id == tenant_id))
            .where(*self._conditions(tenant_id, f, health))
            .order_by(self._severity_rank(aa).asc(), Device.name.asc(), Device.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]
