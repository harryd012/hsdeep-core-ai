"""
NocCorrelationEngine tests (Phase 5 - topology-aware event correlation).

Covers:
  1. Signature grouping -> one NOC event, root cause = earliest availability
  2. Symptom suppression (same-group symptoms only, root cause untouched)
  3. Idempotency: second run returns [] (events already claimed)
  4. No grouping key -> left uncorrelated (honest heuristic)
  5. Device+category grouping when no signature
  6. Topology evidence from real stored edges; [] when none exist
  7. Tenant isolation (other tenant's events invisible)
  8. group_summaries counts
  9. API: POST /api/noc/correlation/run + GET /api/noc/correlation/groups,
     subscription gate (402)

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.models.normalized_event import NormalizedEvent
from app.models.tenant import Tenant
from app.repositories.normalized_event_repository import NormalizedEventRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.correlation import CorrelationRunRequest
from app.services.noc_correlation import NocCorrelationEngine

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jwt(tenant_id: uuid.UUID, permissions: set[str], actor_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=actor_id,
        tenant_id=tenant_id,
        email=f"test-{actor_id}@test.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


async def _seed_sub(db, tenant_id: uuid.UUID) -> None:
    plan = await PlanRepository(db).create(
        name=f"CORR-{uuid.uuid4().hex[:8]}",
        tier=f"corr-{uuid.uuid4().hex[:8]}",
        description="Correlation test plan",
        price_monthly=0.0,
        price_annual=0.0,
        device_limit=100,
        sensor_limit=1000,
        api_rate_limit=100000,
        collector_limit=10,
        seats=25,
        features_json={},
        is_public=False,
    )
    now = datetime.now(timezone.utc)
    await SubscriptionRepository(db).create(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        billing_interval="month",
    )
    await db.commit()


async def _make_device(db, tenant_id: uuid.UUID, name: str) -> Device:
    device = Device(
        tenant_id=tenant_id, name=name, device_type="switch", status="online"
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def _make_event(
    db,
    tenant_id: uuid.UUID,
    *,
    title: str,
    category: str = "performance",
    severity: str = "warning",
    signature: str | None = None,
    device_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    minutes_ago: int = 5,
) -> NormalizedEvent:
    # Anchor the reading to the moment this test runs, NOT to module import:
    # the engine only considers events inside `now - time_window_minutes`
    # (default 15), so an import-time constant silently ages out of the window
    # during a long session - the full suite runs ~18 minutes, which turned
    # these tests into "no group formed" failures that only reproduced in the
    # whole-suite run and never in isolation.
    now = datetime.now(timezone.utc)
    event = NormalizedEvent(
        tenant_id=tenant_id,
        event_signature=signature,
        event_category=category,
        event_type="threshold",
        severity=severity,
        title=title,
        device_id=device_id,
        site_id=site_id,
        event_timestamp=now - timedelta(minutes=minutes_ago),
        telemetry_type="metric",
        last_occurred_at=now - timedelta(minutes=minutes_ago),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def _make_tenant(db) -> Tenant:
    t = Tenant(name="Corr Other", slug=f"corr-other-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


class TestNocCorrelationEngine:
    async def test_signature_group_root_cause_and_suppression(self, tenant: Tenant, db):
        sig = f"link-down:{uuid.uuid4().hex[:6]}"
        a = await _make_device(db, tenant.id, "corr-a")
        e_symptom = await _make_event(
            db, tenant.id, title="High latency on uplink",
            category="performance", severity="warning",
            signature=sig, device_id=a.id, minutes_ago=2,
        )
        e_root = await _make_event(
            db, tenant.id, title="Link down on uplink",
            category="availability", severity="critical",
            signature=sig, device_id=a.id, minutes_ago=10,
        )

        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())

        assert len(results) == 1
        r = results[0]
        assert r.created is True
        assert r.noc_event_id is not None
        assert r.root_cause.event_id == e_root.id
        assert "availability" in r.root_cause.selection_reason
        # Symptom suppressed; root cause NOT suppressed.
        assert e_symptom.id in r.suppressed_symptom_ids
        assert e_root.id not in r.suppressed_symptom_ids
        assert e_root.is_suppressed is False
        assert e_symptom.is_suppressed is True
        # NOC event carries root-cause identity and max severity.
        from app.repositories.noc_event_repository import NocEventRepository
        noc = await NocEventRepository(db).get(r.noc_event_id, tenant.id)
        assert noc is not None
        assert noc.title.startswith("NOC Correlation: Link down")
        assert noc.severity == "critical"
        assert noc.device_id == a.id
        # Both events linked + stamped with the same correlation id.
        assert e_root.noc_event_id == noc.id
        assert e_symptom.noc_event_id == noc.id
        assert e_root.correlation_id == e_symptom.correlation_id

    async def test_idempotent_second_run_empty(self, tenant: Tenant, db):
        sig = f"dup-check:{uuid.uuid4().hex[:6]}"
        a = await _make_device(db, tenant.id, "idem-a")
        await _make_event(
            db, tenant.id, title="CPU spike", category="performance",
            severity="warning", signature=sig, device_id=a.id,
        )
        engine = NocCorrelationEngine(db)
        first = await engine.run(tenant.id, CorrelationRunRequest())
        assert len(first) == 1 and first[0].created is True
        # All events are now claimed -> nothing left to correlate.
        second = await engine.run(tenant.id, CorrelationRunRequest())
        assert second == []

    async def test_no_grouping_key_left_uncorrelated(self, tenant: Tenant, db):
        await _make_event(
            db, tenant.id, title="Orphan event", category="capacity",
            severity="info", signature=None, device_id=None, site_id=None,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())
        assert results == []
        # Event remains uncorrelated (never force-grouped).
        events = await NormalizedEventRepository(db).list(tenant.id)
        assert all(e.correlation_id is None for e in events)
        assert all(e.noc_event_id is None for e in events)
        assert all(e.is_suppressed is False for e in events)

    async def test_device_category_group_without_signature(self, tenant: Tenant, db):
        a = await _make_device(db, tenant.id, "devgrp-a")
        e1 = await _make_event(
            db, tenant.id, title="Latency high", category="performance",
            severity="warning", signature=None, device_id=a.id, minutes_ago=8,
        )
        e2 = await _make_event(
            db, tenant.id, title="Packet loss", category="performance",
            severity="critical", signature=None, device_id=a.id, minutes_ago=4,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())

        assert len(results) == 1
        r = results[0]
        ids = {ref.id for ref in r.correlated_events}
        assert ids == {e1.id, e2.id}
        assert r.noc_event_id is not None
        # No shared signature -> confidence lacks the signature bonus.
        assert r.confidence_score < 0.95

    async def test_topology_evidence_real_edges(self, tenant: Tenant, db):
        a = await _make_device(db, tenant.id, "topo-a")
        b = await _make_device(db, tenant.id, "topo-b")
        repo = ResourceRelationshipRepository(db)
        await repo.upsert(
            tenant.id, source_resource_id=a.id, target_resource_id=b.id,
            relationship_type="depends_on",
        )
        await db.commit()
        sig = f"topo-sig:{uuid.uuid4().hex[:6]}"
        e_root = await _make_event(
            db, tenant.id, title="Device down", category="availability",
            severity="critical", signature=sig, device_id=a.id,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())

        assert len(results) == 1
        r = results[0]
        assert len(r.topology_path) == 1
        edge = r.topology_path[0]
        assert edge.source_device_id == str(a.id)
        assert edge.target_device_id == str(b.id)
        assert edge.relationship_type == "depends_on"
        assert r.evidence_sufficiency == "insufficient"  # only 1 event
        # 0.4 group + 0.2*min(1,2)/2 topology + 0.2 shared signature
        assert r.confidence_score == pytest.approx(0.7)
        assert r.root_cause.event_id == e_root.id

    async def test_tenant_isolation(self, tenant: Tenant, db):
        other = await _make_tenant(db)
        sig = f"iso-sig:{uuid.uuid4().hex[:6]}"
        a = await _make_device(db, other.id, "iso-other-a")
        await _make_event(
            db, other.id, title="Other tenant event", category="availability",
            severity="critical", signature=sig, device_id=a.id,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())
        assert results == []
        # The other tenant's event is untouched and unclaimed.
        events = await NormalizedEventRepository(db).list(other.id)
        assert all(e.correlation_id is None for e in events)

    async def test_time_window_excludes_old_events(self, tenant: Tenant, db):
        sig = f"old-sig:{uuid.uuid4().hex[:6]}"
        a = await _make_device(db, tenant.id, "old-a")
        # 3 hours old - outside the 15-minute default window.
        await _make_event(
            db, tenant.id, title="Old event", category="availability",
            severity="critical", signature=sig, device_id=a.id,
            minutes_ago=180,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())
        assert results == []

    async def test_group_summaries(self, tenant: Tenant, db):
        sig = f"sum-sig:{uuid.uuid4().hex[:6]}"
        a = await _make_device(db, tenant.id, "sum-a")
        await _make_event(
            db, tenant.id, title="Down", category="availability",
            severity="critical", signature=sig, device_id=a.id, minutes_ago=9,
        )
        await _make_event(
            db, tenant.id, title="Slow", category="performance",
            severity="warning", signature=sig, device_id=a.id, minutes_ago=3,
        )
        engine = NocCorrelationEngine(db)
        results = await engine.run(tenant.id, CorrelationRunRequest())
        assert len(results) == 1

        summaries = await engine.group_summaries(tenant.id)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.noc_event_id == results[0].noc_event_id
        assert s.correlated_count == 2
        assert s.suppressed_count == 1
        assert s.root_cause_severity == "critical"
        assert s.latest_event_at is not None
        assert s.root_cause_device_id == a.id


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestNocCorrelationApi:
    async def test_run_and_groups_api(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            await _seed_sub(db, tenant.id)
            a = await _make_device(db, tenant.id, "api-corr-a")
            b = await _make_device(db, tenant.id, "api-corr-b")
            repo = ResourceRelationshipRepository(db)
            await repo.upsert(
                tenant.id, source_resource_id=a.id, target_resource_id=b.id,
                relationship_type="depends_on",
            )
            await db.commit()
            sig = f"api-sig:{uuid.uuid4().hex[:6]}"
            await _make_event(
                db, tenant.id, title="API root", category="availability",
                severity="critical", signature=sig, device_id=a.id, minutes_ago=10,
            )
            await _make_event(
                db, tenant.id, title="API symptom", category="performance",
                severity="warning", signature=sig, device_id=a.id, minutes_ago=4,
            )
            a_id = str(a.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/noc/correlation/run",
                headers=headers,
                json={"time_window_minutes": 15, "max_related_events": 200},
            )
            assert resp.status_code == 200, resp.text
            results = resp.json()
            assert len(results) == 1
            r = results[0]
            assert r["created"] is True
            assert r["root_cause"]["title"] == "API root"
            assert r["root_cause"]["event_category"] == "availability"
            assert r["evidence_sufficiency"] == "partial"
            assert any(e["source_device_id"] == a_id for e in r["topology_path"])
            noc_id = r["noc_event_id"]

            # Idempotent via API: already-claimed events are skipped.
            resp = await client.post(
                "/api/noc/correlation/run", headers=headers,
                json={"time_window_minutes": 15, "max_related_events": 200},
            )
            assert resp.status_code == 200
            assert resp.json() == []

            # Read-side groups.
            resp = await client.get("/api/noc/correlation/groups", headers=headers)
            assert resp.status_code == 200, resp.text
            groups = resp.json()
            assert len(groups) == 1
            assert groups[0]["noc_event_id"] == noc_id
            assert groups[0]["correlated_count"] == 2
            assert groups[0]["suppressed_count"] == 1

    async def test_run_requires_subscription(self, tenant: Tenant):
        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/noc/correlation/run",
                headers=headers,
                json={"time_window_minutes": 15, "max_related_events": 200},
            )
        assert resp.status_code == 402, resp.text

    async def test_run_tenant_isolated(self, tenant: Tenant):
        """Another tenant's events are never correlated by this tenant."""
        async with AsyncSessionLocal() as db:
            other = await _make_tenant(db)
            sig = f"api-iso:{uuid.uuid4().hex[:6]}"
            a = await _make_device(db, other.id, "api-iso-a")
            await _make_event(
                db, other.id, title="Foreign event", category="availability",
                severity="critical", signature=sig, device_id=a.id,
            )
            await _seed_sub(db, tenant.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/noc/correlation/run",
                headers=headers,
                json={"time_window_minutes": 15, "max_related_events": 200},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == []

            groups = await client.get("/api/noc/correlation/groups", headers=headers)
            assert groups.status_code == 200
            assert groups.json() == []
