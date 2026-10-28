"""
Site Health (GET /api/dashboard/site-health, ECC-1 Sites honeycomb).

Guards: the per-site buckets use the SAME rule as the brief's estate donut;
a site with no devices is ``unknown`` (never "healthy"); worst-first ordering;
unattributed devices are reported, not dropped; the SQL is tenant
scoped on every statement and stays within its 3-statement budget.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401
from app.repositories.site_health_repository import SiteHealthRepository
from app.schemas.site_health import SiteHealth
from app.services.operational_intelligence_service import device_health_buckets
from app.services.site_health_service import SiteHealthService

TENANT = uuid.uuid4()


def _site(code, name=None):
    return SimpleNamespace(id=uuid.uuid4(), code=code, name=name or code, region=None, criticality=None)


class _FakeRepo:
    def __init__(self, sites, statuses, incidents):
        self._sites, self._statuses, self._incidents = sites, statuses, incidents
        self.tenants: list = []

    async def sites(self, tenant_id):
        self.tenants.append(tenant_id)
        return self._sites

    async def device_status_by_site(self, tenant_id):
        self.tenants.append(tenant_id)
        return self._statuses

    async def active_incidents_by_site(self, tenant_id, site_ids):
        self.tenants.append(tenant_id)
        self.site_ids = site_ids
        return self._incidents


async def test_buckets_ordering_and_unassigned():
    nyc, lon, fra, sng = _site("NYC"), _site("LON"), _site("FRA"), _site("SNG")
    repo = _FakeRepo(
        [fra, lon, nyc, sng],
        {
            nyc.id: {"up": 10, "down": 2},
            lon.id: {"up": 5, "warning": 1},
            fra.id: {"up": 7},
            None: {"up": 3, "weird": 1},
        },
        {nyc.id: (4, 1), lon.id: (2, 0)},
    )
    result = await SiteHealthService(repo).snapshot(TENANT)
    SiteHealth.model_validate(result.model_dump())

    assert [s.code for s in result.sites] == ["NYC", "LON", "FRA", "SNG"]
    by_code = {s.code: s for s in result.sites}
    assert by_code["NYC"].worst_health == "down"
    assert by_code["NYC"].devices == device_health_buckets({"up": 10, "down": 2})
    assert (by_code["NYC"].open_incidents, by_code["NYC"].critical_incidents) == (4, 1)
    assert by_code["LON"].worst_health == "warning"
    assert by_code["FRA"].worst_health == "healthy"
    # No devices: unknown, not healthy; zero incidents is a measured zero.
    assert by_code["SNG"].worst_health == "unknown"
    assert by_code["SNG"].devices.total == 0 and by_code["SNG"].open_incidents == 0
    assert result.unassigned_devices == 4
    assert sorted(repo.site_ids) == sorted([fra.id, lon.id, nyc.id, sng.id])
    assert set(repo.tenants) == {TENANT}


async def test_ties_break_on_open_incidents_then_code():
    a, b, c = _site("AAA"), _site("BBB"), _site("CCC")
    repo = _FakeRepo(
        [a, b, c],
        {a.id: {"up": 1}, b.id: {"up": 1}, c.id: {"up": 1}},
        {b.id: (3, 0)},
    )
    result = await SiteHealthService(repo).snapshot(TENANT)
    assert [s.code for s in result.sites] == ["BBB", "AAA", "CCC"]


class _Session:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(all=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: []))


async def test_sql_is_tenant_scoped_on_every_statement_and_budgeted():
    session = _Session()
    repo = SiteHealthRepository(session)
    await repo.sites(TENANT)
    await repo.device_status_by_site(TENANT)
    await repo.active_incidents_by_site(TENANT, [uuid.uuid4()])
    assert len(session.statements) == 3
    sqls = [str(s.compile(dialect=postgresql.dialect())) for s in session.statements]
    assert "sites.tenant_id = %(tenant_id_1)s" in sqls[0]
    assert "devices.tenant_id = %(tenant_id_1)s" in sqls[1]
    assert "devices.is_deleted IS false" in sqls[1]
    assert "incidents.tenant_id = %(tenant_id_1)s" in sqls[2]
    assert "incidents.is_deleted IS false" in sqls[2]
    assert "incidents.site_id IN" in sqls[2]


async def test_no_sites_issues_no_incident_statement():
    session = _Session()
    assert await SiteHealthRepository(session).active_incidents_by_site(TENANT, []) == {}
    assert session.statements == []
