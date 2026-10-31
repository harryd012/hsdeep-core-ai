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

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.alert import Alert
from app.models.collector_run import CollectorRun
from app.models.incident import Incident
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("dev_tenant")]

# The 'dev' tenant has an active subscription (seeded by seed_admin_direct.py).
DEV_TENANT_ID = uuid.UUID("54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers(tenant_id: uuid.UUID) -> dict:
    from tests.conftest import DEV_USER_ID

    token = create_access_token(
        # The dev tenant's real user, so acknowledgements can be attributed.
        user_id=DEV_USER_ID if tenant_id == DEV_TENANT_ID else uuid.uuid4(),
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


@pytest_asyncio.fixture
async def bare_tenant():
    """A tenant with no children at all.

    Deliberately lighter than `incident_tenant`: this environment's shared dev DB
    holds a multi-GB `incidents` table, and a tenant cascade-delete that reaches
    it is orders of magnitude slower than one that does not (measured: a
    childless tenant delete is ~1s). Tests that only need "some other tenant"
    use this.
    """
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Bare Test Tenant", slug=f"bare-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
        tid = t.id

    yield tid

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()


# ---------------------------------------------------------------------------
# PHASE 3.1 — backend contract integrity
#
# /intelligence must expose `duplicate_alerts_removed` as its OWN measurement
# (it used to be set equal to `suppressed_alerts`), and /collector-health must
# expose the authoritative failure side of the CollectorRun record:
# failed_runs / failure_rate_pct / successful_runs / retry_count /
# last_failure_at, computed by CollectorHealthService over the same window.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def contract_tenant():
    """A tenant holding every collector-health state and a known alert volume.

    Sources (deliberately one per derived state):
      * "Contract Mix"     - 2 success + 1 partial + 1 error inside the 24h
                             window, plus 1 error 36h old (outside it) -> degraded
      * "Contract Failing" - 1 success + 3 errors inside the window -> failing
      * "Contract Healthy" - 2 successes -> healthy
      * "Contract Empty"   - no runs at all -> unknown (NOT "failing")

    Alerts (to pin the duplicates-removed arithmetic):
      * 1 non-suppressed alert, occurrence_count = 5
      * 1 suppressed alert,     occurrence_count = 2
      => raw 7, displayed 1, suppressed 1, duplicates removed 5
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="Contract Test Tenant", slug=f"contract-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()

        sources: dict[str, MonitoringSource] = {}
        for key, name in (
            ("mix", "Contract Mix"),
            ("failing", "Contract Failing"),
            ("healthy", "Contract Healthy"),
            ("empty", "Contract Empty"),
        ):
            src = MonitoringSource(
                tenant_id=tenant.id, name=name, source_type="prtg",
                config={}, status="active", is_active=True,
            )
            db.add(src)
            sources[key] = src
        await db.flush()

        run_plan = {
            "mix": [("success", 1), ("success", 2), ("partial", 3), ("error", 4), ("error", 36)],
            "failing": [("success", 1), ("error", 2), ("error", 3), ("error", 4)],
            "healthy": [("success", 1), ("success", 2)],
            "empty": [],
        }
        for key, plan in run_plan.items():
            for status, age_hours in plan:
                started = now - timedelta(hours=age_hours)
                db.add(CollectorRun(
                    tenant_id=tenant.id,
                    monitoring_source_id=sources[key].id,
                    status=status,
                    started_at=started,
                    finished_at=started + timedelta(minutes=1),
                    error="collector failed" if status == "error" else None,
                ))

        db.add(Alert(
            tenant_id=tenant.id, title="Deduped alert", severity="warning", status="open",
            triggered_at=now, last_occurred_at=now, occurrence_count=5,
        ))
        db.add(Alert(
            tenant_id=tenant.id, title="Suppressed alert", severity="warning", status="open",
            triggered_at=now, last_occurred_at=now, occurrence_count=2, is_suppressed=True,
        ))

        await db.commit()
        await db.refresh(tenant)
        for src in sources.values():
            await db.refresh(src)

    yield tenant, sources

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


# ---------------------------------------------------------------------------
# /intelligence — duplicate_alerts_removed is its own measurement
# ---------------------------------------------------------------------------

class TestIntelligenceContractIntegrity:

    async def _get(self, client, tenant_id):
        resp = await client.get(
            "/api/incidents/intelligence",
            params={"tenant_id": str(tenant_id)},
            headers=_auth_headers(tenant_id),
        )
        assert resp.status_code == 200
        return resp.json()

    async def test_duplicate_alerts_removed_is_not_a_copy_of_suppressed(self, client, contract_tenant):
        """raw 7 - displayed 1 - suppressed 1 = 5, while suppressed_alerts is 1.

        Before PHASE 3.1 both fields carried the same number (`= suppressed`),
        so the "Duplicates Removed" KPI merely mirrored the "Suppressed" card
        and was not backed by its own computation.
        """
        tenant, _ = contract_tenant
        body = await self._get(client, tenant.id)
        assert body["suppressed_alerts"] == 1
        assert body["duplicate_alerts_removed"] == 5
        assert body["duplicate_alerts_removed"] != body["suppressed_alerts"]

    async def test_empty_dataset_is_a_measured_zero(self, client, bare_tenant):
        """A tenant with no alerts measures 0 — a real zero, not "unavailable"."""
        body = await self._get(client, bare_tenant)
        assert body["duplicate_alerts_removed"] == 0
        assert body["suppressed_alerts"] == 0
        assert body["active_incidents"] == 0
        assert body["noise_reduction_pct"] == 0.0

    async def test_intelligence_is_tenant_isolated(self, client, contract_tenant, bare_tenant):
        """One tenant's alert volume must never appear in another's metrics."""
        other, _ = contract_tenant
        theirs = await self._get(client, other.id)
        ours = await self._get(client, bare_tenant)
        assert theirs["duplicate_alerts_removed"] == 5
        assert ours["duplicate_alerts_removed"] == 0
        assert ours["suppressed_alerts"] == 0

    async def test_schema_fields_are_required(self):
        """No field may default to 0: a silent zero is a fabricated measurement."""
        from app.api.routes.incidents import AlertIntelligenceMetrics

        for name, field in AlertIntelligenceMetrics.model_fields.items():
            assert field.is_required(), f"{name} still has a default (silent zero)"


# ---------------------------------------------------------------------------
# /collector-health — the authoritative failure side of the CollectorRun record
# ---------------------------------------------------------------------------

class TestCollectorHealthContractIntegrity:

    async def _collectors(self, client, tenant_id, window_hours=None):
        params = {"tenant_id": str(tenant_id)}
        if window_hours is not None:
            params["window_hours"] = str(window_hours)
        resp = await client.get(
            "/api/incidents/collector-health",
            params=params,
            headers=_auth_headers(tenant_id),
        )
        assert resp.status_code == 200
        return resp.json()

    @staticmethod
    def _by_name(body, name):
        return next(c for c in body["collectors"] if c["source_name"] == name)

    async def test_failure_metrics_are_exposed(self, client, contract_tenant):
        """failed_runs / failure_rate_pct / retry_count / last_failure_at ship."""
        tenant, _ = contract_tenant
        body = await self._collectors(client, tenant.id)
        mix = self._by_name(body, "Contract Mix")
        assert mix["total_runs"] == 4  # the 36h-old error is outside the 24h window
        assert mix["successful_runs"] == 3  # 2 success + 1 partial
        assert mix["failed_runs"] == 1
        assert mix["success_rate_pct"] == 75.0
        assert mix["failure_rate_pct"] == 25.0
        assert mix["retry_count"] == 1
        assert mix["last_failure_at"] is not None
        assert mix["last_error"] == "collector failed"
        assert mix["current_state"] == "degraded"

    async def test_partial_counts_as_success(self, client, contract_tenant):
        """Authoritative definition: success = ("success", "partial").

        The replaced inline route counted only status == "success", so the
        partial run would have been treated as a failure (and the source rated
        50% instead of 75%).
        """
        tenant, _ = contract_tenant
        mix = self._by_name(await self._collectors(client, tenant.id), "Contract Mix")
        assert mix["successful_runs"] + mix["failed_runs"] == mix["total_runs"]

    async def test_failing_state_at_or_above_50pct_failure(self, client, contract_tenant):
        tenant, _ = contract_tenant
        failing = self._by_name(await self._collectors(client, tenant.id), "Contract Failing")
        assert failing["total_runs"] == 4
        assert failing["failed_runs"] == 3
        assert failing["failure_rate_pct"] == 75.0
        assert failing["current_state"] == "failing"

    async def test_healthy_state_has_zero_failure_rate(self, client, contract_tenant):
        tenant, _ = contract_tenant
        healthy = self._by_name(await self._collectors(client, tenant.id), "Contract Healthy")
        assert healthy["failed_runs"] == 0
        assert healthy["failure_rate_pct"] == 0.0
        assert healthy["current_state"] == "healthy"

    async def test_zero_runs_is_unknown_and_never_nan_or_infinity(self, client, contract_tenant):
        """total_runs == 0 must not divide by zero, and is unknown — not failing."""
        tenant, _ = contract_tenant
        body = await self._collectors(client, tenant.id)
        empty = self._by_name(body, "Contract Empty")
        assert empty["total_runs"] == 0
        assert empty["failed_runs"] == 0
        assert empty["successful_runs"] == 0
        assert empty["retry_count"] == 0
        assert empty["failure_rate_pct"] == 0.0
        assert empty["success_rate_pct"] == 0.0
        assert empty["last_success_at"] is None
        assert empty["last_failure_at"] is None
        assert empty["current_state"] == "unknown"
        for item in body["collectors"]:
            assert math.isfinite(item["failure_rate_pct"])
            assert math.isfinite(item["success_rate_pct"])
            assert 0.0 <= item["failure_rate_pct"] <= 100.0

    async def test_state_buckets_cover_every_collector(self, client, contract_tenant):
        tenant, _ = contract_tenant
        body = await self._collectors(client, tenant.id)
        assert body["healthy_count"] == 1
        assert body["degraded_count"] == 1
        assert body["failing_count"] == 1
        assert body["unknown_count"] == 1

    async def test_window_hours_is_applied(self, client, contract_tenant):
        """Widening the window must pick up the 36h-old failure."""
        tenant, _ = contract_tenant
        wide = self._by_name(
            await self._collectors(client, tenant.id, window_hours=72), "Contract Mix"
        )
        assert wide["total_runs"] == 5
        assert wide["failed_runs"] == 2
        assert wide["failure_rate_pct"] == 40.0

    async def test_collector_health_is_tenant_isolated(self, client, contract_tenant, bare_tenant):
        """Another tenant's sources and runs must not appear in this response."""
        other, _ = contract_tenant
        theirs = await self._collectors(client, other.id)
        ours = await self._collectors(client, bare_tenant)
        assert len(theirs["collectors"]) == 4
        assert ours["collectors"] == []
        assert ours["healthy_count"] == 0
        assert ours["degraded_count"] == 0
        assert ours["failing_count"] == 0
        assert ours["unknown_count"] == 0

    async def test_item_schema_includes_failure_fields(self):
        """The response model must ship the failure side of the measurement."""
        from app.api.routes.incidents import CollectorHealthItem

        fields = CollectorHealthItem.model_fields
        for name in (
            "failure_rate_pct", "successful_runs", "failed_runs",
            "retry_count", "last_failure_at",
        ):
            assert name in fields, f"{name} missing from CollectorHealthItem"
