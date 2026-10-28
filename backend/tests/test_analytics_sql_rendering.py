"""
Compile-only guard for the SQL emitted by the Incident Analytics and Hosts
Explorer repositories.

Why this exists: the service tests use fake repositories, so they cannot see
invalid SQL. Two bug classes are easy to introduce with SQLAlchemy + PostgreSQL
and only fail at runtime:

  1. ``func.extract("epoch", x)`` renders ``extract(%(param)s, x)`` - a syntax
     error. EXTRACT must be built with ``sqlalchemy.extract`` so it renders
     ``EXTRACT(epoch FROM x)``.
  2. A CASE / date_trunc that is both SELECTed and GROUPed with *bound*
     literals renders different placeholders in each place, so PostgreSQL
     rejects it ("must appear in the GROUP BY clause"). The literals must be
     inlined, so the GROUP BY clause must contain no bind parameters at all.

Each repository method is executed against a recording session (no database),
and the statement it built is compiled for the PostgreSQL dialect and inspected.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (registers every mapper before statements are built)
from app.repositories.explorer_hosts_repository import (
    FACET_DIMENSIONS,
    ExplorerHostsRepository,
    HostFilters,
)
from app.repositories.incident_analytics_repository import IncidentAnalyticsRepository

TENANT = uuid.uuid4()
SINCE = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)


class _Result:
    def all(self):
        return []

    def one(self):
        return SimpleNamespace(n=0, p50=None, p90=None, p95=None)

    def scalar_one(self):
        return 0

    def mappings(self):
        return self


class _RecordingSession:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result()


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _assert_valid_for_postgres(stmt) -> None:
    sql = _sql(stmt)
    # (1) EXTRACT must use the FROM keyword form, never a bound field.
    assert not re.search(r"extract\s*\(\s*%\(", sql, re.I), sql
    for m in re.finditer(r"extract\s*\(", sql, re.I):
        assert re.match(r"extract\s*\(\s*\w+\s+FROM\b", sql[m.start():], re.I), sql
    # (2) No bind parameters may appear in the OUTER statement's GROUP BY.
    #     Checked structurally (each group-by element is compiled on its own):
    #     string-parsing would also match a sub-select's inner GROUP BY and then
    #     sweep in unrelated WHERE binds.
    for element in getattr(stmt, "_group_by_clauses", ()):
        compiled = element.compile(dialect=postgresql.dialect())
        assert not compiled.params, f"bind params in GROUP BY: {compiled} {compiled.params}"


async def _run_incident_repo() -> list:
    session = _RecordingSession()
    repo = IncidentAnalyticsRepository(session)
    await repo.opened_by_day(TENANT, SINCE)
    await repo.resolved_by_day(TENANT, SINCE)
    await repo.severity_counts(TENANT, SINCE)
    await repo.open_aging(TENANT)
    await repo.opened_heatmap(TENANT, SINCE)
    await repo.resolved_duration_buckets(TENANT, SINCE)
    await repo.resolved_duration_percentiles(TENANT, SINCE)
    await repo.top_sites(TENANT, SINCE, 7)
    return session.statements


async def test_incident_analytics_sql_is_valid_for_postgres():
    statements = await _run_incident_repo()
    assert len(statements) == 8  # the documented fixed query budget
    for stmt in statements:
        _assert_valid_for_postgres(stmt)


async def test_incident_analytics_uses_extract_from_and_inlined_buckets():
    sqls = [_sql(s) for s in await _run_incident_repo()]
    joined = "\n".join(sqls)
    assert "EXTRACT(epoch FROM" in joined
    assert "EXTRACT(isodow FROM" in joined
    assert "EXTRACT(hour FROM" in joined
    assert "date_trunc('day'" in joined
    assert "timezone('UTC'" in joined
    assert "'lt_1h'" in joined and "'gt_24h'" in joined  # CASE labels inlined


async def test_incident_analytics_statements_are_tenant_scoped_and_soft_delete_aware():
    for stmt in await _run_incident_repo():
        sql = _sql(stmt)
        assert "incidents.tenant_id" in sql, sql
        assert "incidents.is_deleted IS false" in sql, sql


async def _run_explorer_repo() -> list:
    session = _RecordingSession()
    repo = ExplorerHostsRepository(session)
    filters = HostFilters(
        search="core_%",
        health="critical",
        status="up",
        device_type="router",
        vendor="cisco",
        criticality="high",
        environment="prod",
        site_id=uuid.uuid4(),
    )
    await repo.count_hosts(TENANT, filters)
    for dimension in FACET_DIMENSIONS:
        await repo.facet_counts(TENANT, filters, dimension)
    await repo.list_hosts(TENANT, filters, 50, 0)
    return session.statements


async def test_explorer_hosts_sql_is_valid_for_postgres():
    statements = await _run_explorer_repo()
    assert len(statements) == 1 + len(FACET_DIMENSIONS) + 1
    for stmt in statements:
        _assert_valid_for_postgres(stmt)


async def test_explorer_hosts_statements_are_tenant_scoped():
    for stmt in await _run_explorer_repo():
        sql = _sql(stmt)
        assert "devices.tenant_id" in sql, sql
        assert "devices.is_deleted IS false" in sql, sql
        # The derived-health sub-select is tenant-scoped too.
        assert "alerts.tenant_id" in sql, sql


async def test_explorer_facet_excludes_its_own_filter():
    session = _RecordingSession()
    repo = ExplorerHostsRepository(session)
    filters = HostFilters(vendor="cisco", device_type="router")

    await repo.facet_counts(TENANT, filters, "vendor")
    vendor_sql = _sql(session.statements[-1])
    # Faceting vendor must NOT be narrowed by the vendor filter itself...
    assert "devices.vendor =" not in vendor_sql
    # ...but IS narrowed by the other active filters.
    assert "devices.device_type =" in vendor_sql


async def test_explorer_search_escapes_like_wildcards():
    session = _RecordingSession()
    await ExplorerHostsRepository(session).count_hosts(TENANT, HostFilters(search="50%_off"))
    stmt = session.statements[-1]
    compiled = stmt.compile(dialect=postgresql.dialect())
    patterns = [v for v in compiled.params.values() if isinstance(v, str) and "off" in v]
    assert patterns and all("\\%" in p and "\\_" in p for p in patterns), patterns


async def test_unknown_facet_dimension_is_rejected():
    repo = ExplorerHostsRepository(_RecordingSession())
    with pytest.raises(ValueError):
        await repo.facet_counts(TENANT, HostFilters(), "password")


async def test_incident_analytics_scope_adds_bound_site_and_severity_predicates():
    """ECC-1 filters: every statement gains the predicates, as BIND params."""
    from app.repositories.incident_analytics_repository import IncidentScope

    session = _RecordingSession()
    repo = IncidentAnalyticsRepository(session)
    scope = IncidentScope(site_id=uuid.uuid4(), severity="critical")
    await repo.opened_by_day(TENANT, SINCE, scope)
    await repo.resolved_by_day(TENANT, SINCE, scope)
    await repo.severity_counts(TENANT, SINCE, scope)
    await repo.open_aging(TENANT, scope)
    await repo.opened_heatmap(TENANT, SINCE, scope)
    await repo.resolved_duration_buckets(TENANT, SINCE, scope)
    await repo.resolved_duration_percentiles(TENANT, SINCE, scope)
    await repo.top_sites(TENANT, SINCE, 7, scope)
    assert len(session.statements) == 8
    for stmt in session.statements:
        _assert_valid_for_postgres(stmt)
        sql = _sql(stmt)
        assert "incidents.site_id = %(site_id_1)s" in sql, sql
        assert "incidents.severity = %(severity_1)s" in sql, sql
        assert "'critical'" not in sql  # never inlined


async def test_brief_window_stat_statements_are_valid_and_tenant_scoped():
    from app.repositories.incident_repository import IncidentRepository

    class _OneSession(_RecordingSession):
        async def execute(self, stmt):
            self.statements.append(stmt)
            return SimpleNamespace(one=lambda: (None, None, 0) if len(self.statements) == 1 else (0, None))

    session = _OneSession()
    repo = IncidentRepository(session)
    later = SINCE + dt.timedelta(hours=24)
    await repo.opened_window_stats(TENANT, previous_start=SINCE, current_start=later)
    await repo.resolution_stats_between(TENANT, since=SINCE, until=later)
    for stmt in session.statements:
        _assert_valid_for_postgres(stmt)
        sql = _sql(stmt)
        assert "incidents.tenant_id" in sql and "incidents.is_deleted IS false" in sql
