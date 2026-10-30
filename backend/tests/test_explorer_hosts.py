"""
Hosts & Devices Explorer service (GET /api/explorer/hosts).

Pure-python over a fake repository. Guards:
  * the health facet always lists critical / warning / no_alerts in severity
    order, zero-filled (a bucket with no hosts is a measured zero);
  * every repository call is tenant-scoped and receives the SAME filters;
  * page size is clamped, offset is floored at 0;
  * rows map device columns + derived health + alert counts faithfully;
  * the payload validates against the served schema.
SQL validity is covered separately by test_analytics_sql_rendering.py.
"""
from __future__ import annotations

import datetime as dt
import uuid

from app.repositories.explorer_hosts_repository import FACET_DIMENSIONS, HostFilters
from app.schemas.explorer_hosts import HostsExplorer
from app.services.explorer_hosts_service import MAX_PAGE_SIZE, ExplorerHostsService

TENANT = uuid.uuid4()
NOW = dt.datetime(2025, 2, 5, 14, 32, tzinfo=dt.timezone.utc)


class _FakeRepo:
    def __init__(self, total=0, rows=None, facets=None):
        self.total = total
        self.rows = rows or []
        self.facets = facets or {}
        self.calls: list[tuple] = []

    async def count_hosts(self, tenant_id, filters):
        self.calls.append(("count", tenant_id, filters))
        return self.total

    async def list_hosts(self, tenant_id, filters, limit, offset):
        self.calls.append(("page", tenant_id, filters, limit, offset))
        return self.rows

    async def facet_counts(self, tenant_id, filters, dimension, limit=20):
        self.calls.append(("facet", tenant_id, filters, dimension))
        return self.facets.get(dimension, [])


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "core-sw-01",
        "hostname": "core-sw-01.corp",
        "ip_address": "10.0.0.1",
        "device_type": "switch",
        "vendor": "cisco",
        "provider": "prtg",
        "status": "up",
        "criticality": "high",
        "environment": "prod",
        "lifecycle_state": "active",
        "site_id": uuid.uuid4(),
        "site_name": "New York",
        "health": "critical",
        "alert_critical": 2,
        "alert_warning": 1,
        "alert_info": 0,
        "last_seen_at": NOW,
        "last_telemetry_at": None,
    }
    base.update(over)
    return base


async def _snap(repo, filters=None, **kw):
    return await ExplorerHostsService(repo).snapshot(
        TENANT, filters or HostFilters(), now=NOW, **kw
    )


async def test_empty_tenant_zero_fills_health_facet_and_returns_no_rows():
    result = await _snap(_FakeRepo())

    assert result.total == 0 and result.rows == []
    assert [(f.value, f.count) for f in result.facets.health] == [
        ("critical", 0),
        ("warning", 0),
        ("no_alerts", 0),
    ]
    assert result.facets.vendor == [] and result.facets.device_type == []


async def test_health_facet_is_severity_ordered_even_when_repo_order_differs():
    repo = _FakeRepo(facets={"health": [("no_alerts", 40), ("critical", 3)]})
    result = await _snap(repo)

    assert [(f.value, f.count) for f in result.facets.health] == [
        ("critical", 3),
        ("warning", 0),  # absent from the repo result -> measured zero
        ("no_alerts", 40),
    ]


async def test_other_facets_pass_through_in_repo_order():
    repo = _FakeRepo(
        facets={
            "vendor": [("cisco", 9), ("fortinet", 4)],
            "status": [("up", 11), ("down", 2)],
        }
    )
    result = await _snap(repo)
    assert [(f.value, f.count) for f in result.facets.vendor] == [("cisco", 9), ("fortinet", 4)]
    assert [(f.value, f.count) for f in result.facets.status] == [("up", 11), ("down", 2)]


async def test_every_repo_call_is_tenant_scoped_and_uses_the_same_filters():
    filters = HostFilters(search="core", vendor="cisco")
    repo = _FakeRepo()
    await _snap(repo, filters)

    assert all(call[1] == TENANT for call in repo.calls)
    assert all(call[2] == filters for call in repo.calls)
    facet_dims = [c[3] for c in repo.calls if c[0] == "facet"]
    assert facet_dims == list(FACET_DIMENSIONS)  # one query per dimension
    assert len(repo.calls) == 2 + len(FACET_DIMENSIONS)  # count + page + facets


async def test_page_size_is_clamped_and_offset_floored():
    repo = _FakeRepo()
    result = await _snap(repo, limit=10_000, offset=-5)

    assert result.limit == MAX_PAGE_SIZE and result.offset == 0
    page_call = next(c for c in repo.calls if c[0] == "page")
    assert page_call[3:] == (MAX_PAGE_SIZE, 0)

    low = await _snap(_FakeRepo(), limit=0)
    assert low.limit == 1


async def test_rows_map_columns_derived_health_and_alert_counts():
    repo = _FakeRepo(total=1, rows=[_row()])
    result = await _snap(repo)

    host = result.rows[0]
    assert (host.name, host.vendor, host.site_name) == ("core-sw-01", "cisco", "New York")
    assert host.health == "critical"
    assert (host.alerts.critical, host.alerts.warning, host.alerts.info) == (2, 1, 0)
    assert host.last_telemetry_at is None  # never invented
    assert result.total == 1


async def test_null_alert_counts_become_zero_not_missing():
    row = _row(health="no_alerts", alert_critical=None, alert_warning=None, alert_info=None)
    result = await _snap(_FakeRepo(total=1, rows=[row]))
    assert result.rows[0].alerts.model_dump() == {"critical": 0, "warning": 0, "info": 0}


async def test_payload_validates_against_the_served_schema():
    repo = _FakeRepo(total=1, rows=[_row()], facets={"vendor": [("cisco", 1)]})
    result = await _snap(repo)
    again = HostsExplorer.model_validate(result.model_dump(mode="json"))
    assert again.total == 1 and again.rows[0].health == "critical"
