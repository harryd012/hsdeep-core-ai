"""
Health score service and the /api/assets health endpoints.

Regression cover for:
  - missing data reported as 100% healthy (empty tenant, devices with only
    unknown statuses, a site with no devices);
  - the sensor query, which averaged a VARCHAR column and failed on
    PostgreSQL as soon as a sensor existed;
  - TenantHealthSummary.sensors_count counting devices and alerts_active
    being a hard-coded 0;
  - a foreign or missing business service answering "down" (score 0)
    instead of 404.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.business_service import BusinessService
from app.models.sensor import Sensor
from app.models.site import Site
from app.repositories.device_repository import DeviceRepository
from app.services import health_score
from app.services.health_score import HealthScopeNotFound, _device_score, health_status

pytestmark = pytest.mark.usefixtures("seeded_subscription")


def _client_for(tenant_id: uuid.UUID) -> httpx.AsyncClient:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="health-test@hsdeep.local",
        is_superuser=False,
        permissions=frozenset(),
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _device(db, tenant, source, *, status: str, site_id=None, external_id=None, seen=True):
    device, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        external_id=external_id or f"dev-{uuid.uuid4().hex[:8]}",
        name=f"device-{uuid.uuid4().hex[:6]}",
        device_type="server",
        status=status,
        site_id=site_id,
    )
    device.last_seen_at = datetime.now(timezone.utc) if seen else None
    return device


def _sensor(tenant, source, device, status: str, *, read_at=None) -> Sensor:
    return Sensor(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        device_id=device.id,
        external_id=f"sensor-{uuid.uuid4().hex[:8]}",
        name="ping",
        metric_type="ping",
        status=status,
        last_value_at=read_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Pure scoring rules
# ---------------------------------------------------------------------------


def test_device_score_without_evidence_is_none():
    assert _device_score("unknown", []) is None
    assert _device_score(None, ["unknown", "paused"]) is None


def test_device_score_blends_known_sensors_with_device_status():
    assert _device_score("up", []) == 100
    assert _device_score("unknown", ["down", "up"]) == 50
    # 0.6 * avg(100, 0) + 0.4 * 100
    assert _device_score("up", ["down", "up", "unknown"]) == 70


def test_health_status_labels():
    assert health_status(None) == "unknown"
    assert health_status(80) == "healthy"
    assert health_status(50) == "degraded"
    assert health_status(49) == "down"


# ---------------------------------------------------------------------------
# Service against PostgreSQL
# ---------------------------------------------------------------------------


async def test_empty_tenant_is_unknown_not_healthy(tenant, db):
    rollup = await health_score.tenant_health_rollup(db, tenant.id)
    assert rollup.score is None
    assert rollup.status == "unknown"
    assert (rollup.scored, rollup.total) == (0, 0)


async def test_unknown_devices_do_not_count_as_healthy(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    await _device(db, tenant, source, status="unknown")
    await db.commit()

    rollup = await health_score.tenant_health_rollup(db, tenant.id)
    assert rollup.score is None
    assert (rollup.scored, rollup.total) == (0, 1)


async def test_sensor_statuses_are_scored_on_postgres(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    down = await _device(db, tenant, source, status="unknown")
    up = await _device(db, tenant, source, status="up")
    db.add_all([_sensor(tenant, source, down, "down"), _sensor(tenant, source, up, "up")])
    await db.commit()

    assert await health_score.device_health(db, down.id, tenant.id) == 0
    assert await health_score.device_health(db, up.id, tenant.id) == 100
    rollup = await health_score.tenant_health_rollup(db, tenant.id)
    assert (rollup.score, rollup.scored, rollup.total) == (50, 2, 2)


async def test_stale_or_unseen_evidence_does_not_count(tenant, db, monitoring_source_factory):
    """An 'up' status with no recent observation is not evidence of health."""
    source = await monitoring_source_factory()
    unseen = await _device(db, tenant, source, status="up", seen=False)
    stale_sensor_device = await _device(db, tenant, source, status="unknown")
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    db.add(_sensor(tenant, source, stale_sensor_device, "up", read_at=week_ago))
    await db.commit()

    assert await health_score.device_health(db, unseen.id, tenant.id) is None
    assert await health_score.device_health(db, stale_sensor_device.id, tenant.id) is None
    rollup = await health_score.tenant_health_rollup(db, tenant.id)
    assert (rollup.score, rollup.scored, rollup.total) == (None, 0, 2)


async def test_site_without_devices_is_unknown_and_foreign_site_not_found(tenant, db):
    site = Site(tenant_id=tenant.id, name="Empty site", code=f"s-{uuid.uuid4().hex[:6]}")
    db.add(site)
    await db.commit()

    assert await health_score.site_health(db, site.id, tenant.id) is None
    with pytest.raises(HealthScopeNotFound):
        await health_score.site_health(db, site.id, uuid.uuid4())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def test_tenant_health_endpoint_reports_real_counts(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    device = await _device(db, tenant, source, status="up")
    db.add_all([_sensor(tenant, source, device, "up"), _sensor(tenant, source, device, "warning")])
    await db.flush()
    now = datetime.now(timezone.utc)
    db.add(Alert(
        triggered_at=now,
        last_occurred_at=now,
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        device_id=device.id,
        severity="warning",
        status="open",
        title="CPU high",
    ))
    await db.commit()

    async with _client_for(tenant.id) as client:
        resp = await client.get(f"/api/assets/tenants/{tenant.id}/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["devices_count"] == 1
    assert body["sensors_count"] == 2
    assert body["alerts_active"] == 1
    assert body["devices_scored"] == 1
    # 0.6 * avg(100, 60) + 0.4 * 100
    assert body["health_score"] == 88
    assert body["status"] == "healthy"


async def test_tenant_health_endpoint_empty_tenant_is_null(tenant):
    async with _client_for(tenant.id) as client:
        resp = await client.get(f"/api/assets/tenants/{tenant.id}/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["health_score"] is None
    assert body["status"] == "unknown"


async def test_business_service_health_cross_tenant_is_404(tenant, db):
    service = BusinessService(tenant_id=tenant.id, name=f"svc-{uuid.uuid4().hex[:6]}")
    db.add(service)
    await db.commit()

    async with _client_for(tenant.id) as client:
        own = await client.get(f"/api/assets/business-services/{service.id}/health")
    assert own.status_code == 200, own.text
    assert own.json()["health_score"] is None
    assert own.json()["status"] == "unknown"

    async with _client_for(uuid.uuid4()) as client:
        foreign = await client.get(f"/api/assets/business-services/{service.id}/health")
    assert foreign.status_code == 404, foreign.text
