"""
Repository Pattern: read-only SQL aggregates for the Incident Analytics read.

Every method is a single GROUP BY / aggregate statement. Nothing here loads
incident rows into Python - the estate can hold millions of incidents, so the
database does the counting and only small result sets cross the wire.

Index usage (see Incident.__table_args__ / migration a4c7e2f91b36):
  * opened_at predicates  -> ix_incidents_tenant_opened_active
  * resolved_at predicates -> ix_incidents_tenant_resolved_active
  * open-status aging      -> ix_incidents_tenant_status_site_active (covering,
                              INCLUDE opened_at/resolved_at; migration e8c1d4f7a2b9)
Each statement spells the soft-delete filter as ``is_deleted.is_(False)``
(rendered ``IS false``) because that exact spelling is what lets PostgreSQL
prove the partial-index predicate; ``= false`` would silently full-scan.

Tenant scope: ``tenant_id`` is a required positional argument on every method
and is applied in SQL (including on the Site join), never filtered afterwards.

Time handling: all calendar bucketing is done in UTC so results do not depend
on the DB session zone.

SQL-RENDERING RULES (load-bearing - these are what keeps the statements valid
on PostgreSQL through asyncpg):
  * EXTRACT must be built with ``sqlalchemy.extract()`` so it renders
    ``EXTRACT(epoch FROM x)``. ``func.extract("epoch", x)`` renders
    ``extract($1, x)``, which is a syntax error.
  * Any expression that appears in BOTH the SELECT list and GROUP BY must
    render identical text. Bind parameters are numbered per occurrence, so
    ``date_trunc($1, ...)`` / ``CASE ... THEN $2`` in SELECT and GROUP BY are
    different expressions to PostgreSQL ("must appear in the GROUP BY clause").
    Those literals are therefore inlined with ``literal_column``. Every inlined
    value is an internal constant defined in this module - never user input.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import case, extract, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.models.site import Site

#: Matches ResponseKpis.open_incidents ("active = open + acknowledged +
#: investigating") so the analytics aging chart and the Command Center KPI
#: never disagree about what "open" means.
ACTIVE_STATUSES = ("open", "acknowledged", "investigating")

#: Ordered (key, upper-bound seconds) edges; the last bucket is unbounded.
AGING_EDGES: tuple[tuple[str, int], ...] = (
    ("lt_1h", 3_600),
    ("1_4h", 4 * 3_600),
    ("4_24h", 24 * 3_600),
    ("1_3d", 3 * 86_400),
)
AGING_OVERFLOW_KEY = "gt_3d"

DURATION_EDGES: tuple[tuple[str, int], ...] = (
    ("lt_5m", 5 * 60),
    ("5_30m", 30 * 60),
    ("30m_2h", 2 * 3_600),
    ("2h_8h", 8 * 3_600),
    ("8h_24h", 24 * 3_600),
)
DURATION_OVERFLOW_KEY = "gt_24h"


def _utc(column):
    """The column as a UTC wall-clock timestamp (session-zone independent)."""
    return func.timezone(literal_column("'UTC'"), column)


def _bucket_case(expr, edges: tuple[tuple[str, int], ...], overflow_key: str):
    """CASE with every threshold and label inlined (see module docstring)."""
    return case(
        *[(expr < literal_column(str(int(limit))), literal_column(f"'{key}'")) for key, limit in edges],
        else_=literal_column(f"'{overflow_key}'"),
    )


@dataclass(frozen=True)
class IncidentScope:
    """Optional narrowing of every aggregate (ECC-1 dashboard filters).

    ``None`` fields apply no predicate, so an empty scope renders exactly the
    statements this module produced before the filters existed. Both values
    are bound parameters, never inlined.
    """

    site_id: uuid.UUID | None = None
    severity: str | None = None


class IncidentAnalyticsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _base(self, tenant_id: uuid.UUID, scope: IncidentScope | None = None):
        clauses = [Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False)]
        if scope is not None and scope.site_id is not None:
            clauses.append(Incident.site_id == scope.site_id)
        if scope is not None and scope.severity is not None:
            clauses.append(Incident.severity == scope.severity)
        return tuple(clauses)

    async def opened_by_day(self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None) -> dict[dt.date, int]:
        day = func.date_trunc(literal_column("'day'"), _utc(Incident.opened_at))
        stmt = (
            select(day.label("d"), func.count().label("n"))
            .where(*self._base(tenant_id, scope), Incident.opened_at >= since)
            .group_by(day)
        )
        rows = (await self.db.execute(stmt)).all()
        return {r.d.date(): int(r.n) for r in rows}

    async def resolved_by_day(self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None) -> dict[dt.date, int]:
        day = func.date_trunc(literal_column("'day'"), _utc(Incident.resolved_at))
        stmt = (
            select(day.label("d"), func.count().label("n"))
            .where(*self._base(tenant_id, scope), Incident.resolved_at.is_not(None), Incident.resolved_at >= since)
            .group_by(day)
        )
        rows = (await self.db.execute(stmt)).all()
        return {r.d.date(): int(r.n) for r in rows}

    async def severity_counts(self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None) -> dict[str, int]:
        stmt = (
            select(Incident.severity, func.count().label("n"))
            .where(*self._base(tenant_id, scope), Incident.opened_at >= since)
            .group_by(Incident.severity)
        )
        rows = (await self.db.execute(stmt)).all()
        return {str(r.severity): int(r.n) for r in rows}

    async def open_aging(self, tenant_id: uuid.UUID, scope: IncidentScope | None = None) -> list[tuple[str, str, int]]:
        """(age_bucket_key, severity, count) for currently-open incidents."""
        age_seconds = extract("epoch", func.now() - Incident.opened_at)
        bucket = _bucket_case(age_seconds, AGING_EDGES, AGING_OVERFLOW_KEY)
        stmt = (
            select(bucket.label("b"), Incident.severity, func.count().label("n"))
            .where(
                *self._base(tenant_id, scope),
                Incident.resolved_at.is_(None),
                Incident.status.in_(ACTIVE_STATUSES),
            )
            .group_by(bucket, Incident.severity)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(str(r.b), str(r.severity), int(r.n)) for r in rows]

    async def opened_heatmap(self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None) -> list[tuple[int, int, int]]:
        """(iso_weekday 1=Mon..7=Sun, hour 0..23, count) in UTC."""
        ts = _utc(Incident.opened_at)
        dow = extract("isodow", ts)
        hour = extract("hour", ts)
        stmt = (
            select(dow.label("dow"), hour.label("hr"), func.count().label("n"))
            .where(*self._base(tenant_id, scope), Incident.opened_at >= since)
            .group_by(dow, hour)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(int(r.dow), int(r.hr), int(r.n)) for r in rows]

    async def resolved_duration_buckets(self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None) -> dict[str, int]:
        duration = extract("epoch", Incident.resolved_at - Incident.opened_at)
        bucket = _bucket_case(duration, DURATION_EDGES, DURATION_OVERFLOW_KEY)
        stmt = (
            select(bucket.label("b"), func.count().label("n"))
            .where(*self._resolved_in_window(tenant_id, since, scope))
            .group_by(bucket)
        )
        rows = (await self.db.execute(stmt)).all()
        return {str(r.b): int(r.n) for r in rows}

    async def resolved_duration_percentiles(
        self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None
    ) -> tuple[int, float | None, float | None, float | None]:
        """(resolved_count, p50, p90, p95) of resolution time in SECONDS."""
        duration = extract("epoch", Incident.resolved_at - Incident.opened_at)
        stmt = select(
            func.count().label("n"),
            func.percentile_cont(literal_column("0.5")).within_group(duration).label("p50"),
            func.percentile_cont(literal_column("0.9")).within_group(duration).label("p90"),
            func.percentile_cont(literal_column("0.95")).within_group(duration).label("p95"),
        ).where(*self._resolved_in_window(tenant_id, since, scope))
        row = (await self.db.execute(stmt)).one()
        n = int(row.n or 0)
        if n == 0:
            return 0, None, None, None
        return (
            n,
            float(row.p50) if row.p50 is not None else None,
            float(row.p90) if row.p90 is not None else None,
            float(row.p95) if row.p95 is not None else None,
        )

    def _resolved_in_window(
        self, tenant_id: uuid.UUID, since: dt.datetime, scope: IncidentScope | None = None
    ):
        return (
            *self._base(tenant_id, scope),
            Incident.resolved_at.is_not(None),
            Incident.resolved_at >= since,
            # Guard against clock-skew rows that would produce a negative duration.
            Incident.resolved_at >= Incident.opened_at,
        )

    async def top_sites(
        self,
        tenant_id: uuid.UUID,
        since: dt.datetime,
        limit: int,
        scope: IncidentScope | None = None,
    ) -> list[tuple[uuid.UUID | None, str | None, int]]:
        """(site_id, site_name, count) for incidents opened in the window.

        The Site join repeats the tenant predicate so a mis-attributed
        site_id can never leak another tenant's site name.
        """
        stmt = (
            select(Incident.site_id, Site.name, func.count().label("n"))
            .select_from(Incident)
            .outerjoin(Site, (Site.id == Incident.site_id) & (Site.tenant_id == tenant_id))
            .where(*self._base(tenant_id, scope), Incident.opened_at >= since)
            .group_by(Incident.site_id, Site.name)
            .order_by(func.count().desc(), Site.name.asc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(r.site_id, r.name, int(r.n)) for r in rows]
