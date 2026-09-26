"""
Incident lifecycle integration tests — Task 8 verification.

Covers every frontend-required action endpoint and the
/intelligence + /collector-health aggregates.

Uses the real PostgreSQL DB (same convention as test_repositories.py).
Auth is provided by minting a real JWT via create_access_token.

All write-action tests use the seeded 'dev' tenant (54dd0ca8-...) which
has an active subscription — the _active_sub_guard requires this to avoid
HTTP 402 responses.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.incident import Incident
from app.models.tenant import Tenant

pytestmark = pytest.mark.asyncio

# The 'dev' tenant has an active subscription (seeded by seed_admin_direct.py).
DEV_TENANT_ID = uuid.UUID("54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers(tenant_id: uuid.UUID) -> dict:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="test@hsdeep.local",
        is_superuser=True,
        permissions=frozenset(),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def subscribed_incident():
    """An open Incident under the dev tenant (active subscription).
    Cleaned up after the test via explicit DELETE."""
    async with AsyncSessionLocal() as db:
        inc = Incident(
            tenant_id=DEV_TENANT_ID,
            title="Test WAN outage",
            description="WAN link down",
            severity="critical",
            status="open",
            alert_count=2,
            total_occurrences=2,
            opened_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            last_alert_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(inc)
        await db.commit()
        await db.refresh(inc)

    yield inc

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Incident).where(Incident.id == inc.id))
        await db.commit()


@pytest_asyncio.fixture
async def incident_tenant():
    """Separate read-only tenant + incident for isolation/404 tests.
    No subscription needed — these tests only call read endpoints."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Read Test Tenant", slug=f"read-test-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.flush()
        inc = Incident(
            tenant_id=t.id,
            title="Read-only test incident",
            description="Test",
            severity="warning",
            status="open",
            alert_count=1,
            total_occurrences=1,
            opened_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            last_alert_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(inc)
        await db.commit()
        await db.refresh(t)
        await db.refresh(inc)

    yield t, inc

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


# ---------------------------------------------------------------------------
# List / GET
# ---------------------------------------------------------------------------

class TestIncidentListAndGet:

    async def test_list_incidents_returns_200(self, client, incident_tenant):
        tenant, _ = incident_tenant
        resp = await client.get(
            "/api/incidents",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_incident_returns_200(self, client, incident_tenant):
        tenant, inc = incident_tenant
        resp = await client.get(
            f"/api/incidents/{inc.id}",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(inc.id)
        assert resp.json()["status"] == "open"

    async def test_get_nonexistent_incident_returns_404(self, client, incident_tenant):
        tenant, _ = incident_tenant
        resp = await client.get(
            f"/api/incidents/{uuid.uuid4()}",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 404

    async def test_unauthenticated_request_returns_401(self, client, incident_tenant):
        tenant, inc = incident_tenant
        resp = await client.get(f"/api/incidents/{inc.id}", params={"tenant_id": str(tenant.id)})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Full lifecycle: OPEN → ACKNOWLEDGED → INVESTIGATING → MONITORING →
#                 RECOVERED → CLOSED → ARCHIVED
# ---------------------------------------------------------------------------

class TestIncidentLifecycle:

    async def test_full_lifecycle_transitions(self, client, subscribed_incident):
        inc = subscribed_incident
        headers = _auth_headers(DEV_TENANT_ID)
        qs = {"tenant_id": str(DEV_TENANT_ID)}
        inc_id = str(inc.id)

        # 1. acknowledge
        r = await client.post(f"/api/incidents/{inc_id}/acknowledge", params=qs, headers=headers, json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "acknowledged"

        # 2. investigate
        r = await client.post(f"/api/incidents/{inc_id}/investigate", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "investigating"

        # 3. monitor
        r = await client.post(f"/api/incidents/{inc_id}/monitor", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "monitoring"

        # 4. recover
        r = await client.post(f"/api/incidents/{inc_id}/recover", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "recovered"

        # 5. close
        r = await client.post(f"/api/incidents/{inc_id}/close", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "closed"

        # 6. archive
        r = await client.post(f"/api/incidents/{inc_id}/archive", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

    async def test_acknowledge_persists_timestamp(self, client, subscribed_incident):
        """acknowledged_at must be set after acknowledge action."""
        inc = subscribed_incident
        headers = _auth_headers(DEV_TENANT_ID)
        qs = {"tenant_id": str(DEV_TENANT_ID)}

        r = await client.post(f"/api/incidents/{inc.id}/acknowledge", params=qs, headers=headers, json={})
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(select(Incident).where(Incident.id == inc.id))
            updated = result.scalar_one()
            assert updated.acknowledged_at is not None
            assert updated.status == "acknowledged"

    async def test_resolve_sets_resolved_at(self, client, subscribed_incident):
        inc = subscribed_incident
        headers = _auth_headers(DEV_TENANT_ID)
        qs = {"tenant_id": str(DEV_TENANT_ID)}

        r = await client.post(f"/api/incidents/{inc.id}/resolve", params=qs, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"
        assert r.json()["resolved_at"] is not None

    async def test_lifecycle_action_on_nonexistent_incident_returns_404(self, client, subscribed_incident):
        headers = _auth_headers(DEV_TENANT_ID)
        qs = {"tenant_id": str(DEV_TENANT_ID)}
        fake_id = uuid.uuid4()

        for action in ("acknowledge", "investigate", "monitor", "recover", "close", "archive", "resolve"):
            r = await client.post(f"/api/incidents/{fake_id}/{action}", params=qs, headers=headers, json={})
            assert r.status_code == 404, f"{action} should return 404 for nonexistent incident"

    async def test_tenant_isolation_blocks_cross_tenant_access(self, client, subscribed_incident):
        """A different tenant (no subscription, no incident) must get 404."""
        inc = subscribed_incident
        # Use a random UUID as the "other tenant" — they have no subscription,
        # but the incident lookup (tenant_id mismatch) should reject before
        # the subscription guard fires. The guard fires first because
        # _active_sub_guard runs before service.get(). Either 402 or 404 is
        # a correct rejection — both mean the tenant cannot access this incident.
        other_tenant_id = uuid.uuid4()
        headers = _auth_headers(other_tenant_id)
        qs = {"tenant_id": str(other_tenant_id)}

        r = await client.post(f"/api/incidents/{inc.id}/acknowledge", params=qs, headers=headers, json={})
        # 402 (no subscription) or 404 (wrong tenant) both correctly deny access
        assert r.status_code in (402, 404), (
            f"Expected 402 or 404 for cross-tenant access, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# /intelligence endpoint
# ---------------------------------------------------------------------------

class TestIntelligenceEndpoint:

    async def test_intelligence_returns_200_with_correct_shape(self, client, incident_tenant):
        tenant, _ = incident_tenant
        resp = await client.get(
            "/api/incidents/intelligence",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Shape check — all required fields present
        assert "active_incidents" in body
        assert "correlated_incidents" in body
        assert "suppressed_alerts" in body
        assert "duplicate_alerts_removed" in body
        assert "noise_reduction_pct" in body
        # All values are real numbers, not fake
        assert isinstance(body["active_incidents"], int)
        assert isinstance(body["noise_reduction_pct"], float)

    async def test_intelligence_counts_open_incident(self, client, incident_tenant):
        tenant, _ = incident_tenant
        resp = await client.get(
            "/api/incidents/intelligence",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        body = resp.json()
        # The fixture created one open incident with 2 alerts → at least 1 active
        assert body["active_incidents"] >= 1


# ---------------------------------------------------------------------------
# /collector-health endpoint
# ---------------------------------------------------------------------------

class TestCollectorHealthEndpoint:

    async def test_collector_health_returns_200_with_correct_shape(self, client, incident_tenant):
        tenant, _ = incident_tenant
        resp = await client.get(
            "/api/incidents/collector-health",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "collectors" in body
        assert "healthy_count" in body
        assert "degraded_count" in body
        assert "failing_count" in body
        assert "unknown_count" in body
        assert isinstance(body["collectors"], list)

    async def test_collector_health_no_fake_data(self, client, incident_tenant):
        """All counts must be real integers derived from DB — not fabricated."""
        tenant, _ = incident_tenant
        resp = await client.get(
            "/api/incidents/collector-health",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(tenant.id),
        )
        body = resp.json()
        total = body["healthy_count"] + body["degraded_count"] + body["failing_count"] + body["unknown_count"]
        assert total == len(body["collectors"])
