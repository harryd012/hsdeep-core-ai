"""
WAN failover API (/api/failover) — isolation and permission behaviour.

Uses the REAL app + database (project convention). Each case pins one of the
four things this surface must not get wrong:

  * authentication is required (401 without a token, never 404),
  * authorization is enforced per route (403 without the right permission),
  * tenant isolation holds (another tenant's episode is a 404, never data),
  * the write endpoint writes ONLY a verification, and the read-only POSTs
    (evaluate / simulate) write nothing at all.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.repositories.failover_event_repository import FailoverEventRepository
# Reuse the DB suite's site-edge seeder: it builds the same realistic topology
# the detector sees, so the API tests assert against real collected shapes.
from tests.test_failover_intelligence_db import seed_edge, set_link_state

pytestmark = pytest.mark.asyncio

BASE = "/api/failover"
READ_PERMISSIONS = frozenset({"devices.read"})
WRITE_PERMISSIONS = frozenset({"devices.read", "incidents.write"})


def token(tenant_id: uuid.UUID, permissions=READ_PERMISSIONS) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="failover-test@hsdeep.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


def client(tenant_id: uuid.UUID | None, permissions=READ_PERMISSIONS):
    headers = (
        {"Authorization": f"Bearer {token(tenant_id, permissions)}"}
        if tenant_id is not None
        else {}
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers=headers)


async def seed_committed_edge(tenant_id: uuid.UUID):
    """Seed a site edge in a COMMITTED session (the API runs its own session)."""
    async with AsyncSessionLocal() as db:
        seeded = await seed_edge(db, tenant_id)
        await db.commit()
        return seeded


async def seed_committed_failover(tenant_id: uuid.UUID):
    """Seed an edge, run the detector for real, and leave one OPEN episode."""
    from app.services.failover_intelligence.detector import FailoverDetectorService

    async with AsyncSessionLocal() as db:
        seeded = await seed_edge(db, tenant_id)
        await db.commit()

        await FailoverDetectorService(db, publish=False).run_for_tenant(tenant_id)
        await set_link_state(db, tenant_id, seeded["links"][0], oper="down")
        await FailoverDetectorService(db, publish=False).run_for_tenant(tenant_id)
        await db.commit()

        rows, _total = await FailoverEventRepository(db).list(tenant_id)
        seeded["event"] = rows[0]
        return seeded


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [f"{BASE}/summary", f"{BASE}/events", f"{BASE}/policies"],
)
async def test_unauthenticated_reads_are_401_not_404(path):
    async with client(None) as http:
        response = await http.get(path)
    assert response.status_code == 401


async def test_reads_require_devices_read(tenant):
    async with client(tenant.id, permissions=frozenset()) as http:
        for path in (f"{BASE}/summary", f"{BASE}/events", f"{BASE}/policies"):
            response = await http.get(path)
            assert response.status_code == 403, path


async def test_read_only_posts_require_devices_read(tenant):
    async with client(tenant.id, permissions=frozenset()) as http:
        assert (await http.post(f"{BASE}/evaluate", json={})).status_code == 403
        assert (
            await http.post(
                f"{BASE}/simulate", json={"site_id": str(uuid.uuid4())}
            )
        ).status_code == 403


async def test_manual_verification_requires_incidents_write(tenant):
    seeded = await seed_committed_failover(tenant.id)
    event_id = str(seeded["event"].id)

    async with client(tenant.id, permissions=READ_PERMISSIONS) as http:
        response = await http.post(f"{BASE}/events/{event_id}/verify", json={})
    assert response.status_code == 403

    async with client(tenant.id, permissions=WRITE_PERMISSIONS) as http:
        response = await http.post(f"{BASE}/events/{event_id}/verify", json={})
    assert response.status_code == 200


async def test_unauthenticated_verify_is_401(tenant):
    async with client(None) as http:
        response = await http.post(f"{BASE}/events/{uuid.uuid4()}/verify", json={})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
async def test_episodes_are_invisible_to_another_tenant(tenant):
    seeded = await seed_committed_failover(tenant.id)
    event_id = str(seeded["event"].id)
    other_tenant = uuid.uuid4()

    async with client(other_tenant) as http:
        listing = await http.get(f"{BASE}/events")
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

        detail = await http.get(f"{BASE}/events/{event_id}")
        assert detail.status_code == 404

        verification = await http.get(f"{BASE}/events/{event_id}/verification")
        assert verification.status_code == 404

    async with client(tenant.id) as http:
        assert (await http.get(f"{BASE}/events/{event_id}")).status_code == 200


async def test_verify_cannot_touch_another_tenants_episode(tenant):
    seeded = await seed_committed_failover(tenant.id)
    async with client(uuid.uuid4(), permissions=WRITE_PERMISSIONS) as http:
        response = await http.post(
            f"{BASE}/events/{seeded['event'].id}/verify", json={}
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Reads: list, detail, replay, verification
# ---------------------------------------------------------------------------
async def test_event_list_shape_and_status_filter(tenant):
    seeded = await seed_committed_failover(tenant.id)
    async with client(tenant.id) as http:
        response = await http.get(f"{BASE}/events", params={"site_id": str(seeded["site"].id)})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        event = body["events"][0]
        assert event["status"] == "failed_over"
        assert event["link_from_name"] == "wan-1"
        assert event["route_transition_count"] >= 0
        assert event["evidence_count"] >= 1
        assert event["blast_radius_total"] >= 1
        assert "route_transitions" not in event  # arrays are detail-only

        open_only = await http.get(f"{BASE}/events", params={"open_only": "true"})
        assert open_only.json()["total"] == 1

        closed = await http.get(
            f"{BASE}/events", params={"status": "recovered"}
        )
        assert closed.json()["total"] == 0


async def test_invalid_filters_are_rejected_not_ignored(tenant):
    async with client(tenant.id) as http:
        assert (
            await http.get(f"{BASE}/events", params={"status": "exploded"})
        ).status_code == 422
        assert (
            await http.get(f"{BASE}/policies", params={"metric": "vibes"})
        ).status_code == 422
        assert (
            await http.get(f"{BASE}/policies", params={"scope": "galaxy"})
        ).status_code == 422


async def test_event_detail_carries_a_provenance_tagged_replay(tenant):
    seeded = await seed_committed_failover(tenant.id)
    async with client(tenant.id) as http:
        response = await http.get(f"{BASE}/events/{seeded['event'].id}")
    assert response.status_code == 200
    body = response.json()
    event = body["event"]
    replay = body["replay"]

    assert event["trigger"]["path_failure"] is True
    assert event["decision"]["decision"] == "FORWARD"
    assert event["route_transitions"] is not None
    assert event["blast_radius"]["source"] == "resource_relationships"

    provenance = {entry["provenance"] for entry in replay["timeline"]}
    assert provenance <= {"captured", "derived", "context"}
    assert "captured" in provenance
    assert replay["gaps"]  # routing/BGP/alerts awareness is stated, not hidden
    assert replay["coverage"]["still_open"] is True


async def test_verification_endpoint_separates_stored_from_current(tenant):
    seeded = await seed_committed_failover(tenant.id)
    async with client(tenant.id) as http:
        response = await http.get(f"{BASE}/events/{seeded['event'].id}/verification")
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is None  # nothing recorded yet
    assert body["current"]["status"] in {"pass", "fail", "unknown"}
    assert body["current"]["checks"]
    # The failed path is still down, so recovery cannot be claimed.
    assert body["current"]["status"] == "fail"


# ---------------------------------------------------------------------------
# Manual verification (the ONLY write)
# ---------------------------------------------------------------------------
async def test_manual_verification_records_the_result_and_links_an_incident(tenant):
    from datetime import datetime, timezone

    from app.models.incident import Incident

    seeded = await seed_committed_failover(tenant.id)
    event_id = str(seeded["event"].id)
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        incident = Incident(
            tenant_id=tenant.id,
            site_id=seeded["site"].id,
            title="WAN outage",
            severity="high",
            opened_at=now,
            last_alert_at=now,
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        incident_id = incident.id

    async with client(tenant.id, permissions=WRITE_PERMISSIONS) as http:
        response = await http.post(
            f"{BASE}/events/{event_id}/verify", json={"incident_id": str(incident_id)}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["verification"]["trigger"] == "manual"
    assert body["verification"]["status"] == "fail"  # still down: no green paint
    assert body["incident_id"] == str(incident_id)
    assert body["status"] == "failed_over"  # verification never rewrites the FSM

    async with client(tenant.id) as http:
        stored = await http.get(f"{BASE}/events/{event_id}/verification")
    assert stored.json()["stored"]["trigger"] == "manual"

    # Linking an incident that is not visible to this tenant is a 404.
    async with client(tenant.id, permissions=WRITE_PERMISSIONS) as http:
        foreign = await http.post(
            f"{BASE}/events/{event_id}/verify", json={"incident_id": str(uuid.uuid4())}
        )
    assert foreign.status_code == 404


# ---------------------------------------------------------------------------
# Read-only POSTs
# ---------------------------------------------------------------------------
async def test_evaluate_dry_run_returns_a_report_and_writes_nothing(tenant):
    seeded = await seed_committed_edge(tenant.id)
    async with AsyncSessionLocal() as db:
        before = await FailoverEventRepository(db).list(tenant.id)

    async with client(tenant.id) as http:
        response = await http.post(
            f"{BASE}/evaluate", json={"site_id": str(seeded["site"].id)}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["scopes_evaluated"] == 1
    report = body["scopes"][0]
    assert report["action"] == "baseline"
    assert len(report["candidate_paths"]) == 2
    assert report["decision"]["selected_path"]

    async with AsyncSessionLocal() as db:
        after = await FailoverEventRepository(db).list(tenant.id)
    assert after[1] == before[1] == 0


async def test_evaluate_without_a_site_sweeps_the_tenants_scopes(tenant):
    await seed_committed_edge(tenant.id)
    async with client(tenant.id) as http:
        response = await http.post(f"{BASE}/evaluate", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["scopes_evaluated"] >= 1
    assert isinstance(body["would_open"], int)


async def test_simulate_is_read_only_and_says_it_is_a_counterfactual(tenant):
    seeded = await seed_committed_edge(tenant.id)
    primary = seeded["links"][0].name

    async with client(tenant.id) as http:
        response = await http.post(
            f"{BASE}/simulate",
            json={
                "site_id": str(seeded["site"].id),
                "perturbation": {"links": {primary: {"available": False}}},
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert "counterfactual" in body["method"]
    assert body["changed"] is True
    assert body["delta"]["paths_lost"] == [primary]
    assert body["delta"]["selected_path"]["after"] != primary
    assert body["note"]
    assert body["candidate_paths"]

    async with AsyncSessionLocal() as db:
        assert (await FailoverEventRepository(db).list(tenant.id))[1] == 0


async def test_simulate_reports_targets_it_could_not_match(tenant):
    seeded = await seed_committed_edge(tenant.id)
    async with client(tenant.id) as http:
        response = await http.post(
            f"{BASE}/simulate",
            json={
                "site_id": str(seeded["site"].id),
                "perturbation": {"links": {"no-such-circuit": {"available": False}}},
            },
        )
    assert response.status_code == 200
    assert response.json()["unresolved_targets"] == ["no-such-circuit"]


# ---------------------------------------------------------------------------
# Blast radius (live topology)
# ---------------------------------------------------------------------------
async def test_blast_radius_needs_exactly_one_seed(tenant):
    async with client(tenant.id) as http:
        assert (await http.get(f"{BASE}/blast-radius")).status_code == 422
        both = await http.get(
            f"{BASE}/blast-radius",
            params={"resource_id": str(uuid.uuid4()), "link_id": str(uuid.uuid4())},
        )
        assert both.status_code == 422


async def test_blast_radius_follows_the_real_topology(tenant):
    seeded = await seed_committed_edge(tenant.id)
    async with client(tenant.id) as http:
        response = await http.get(
            f"{BASE}/blast-radius", params={"link_id": str(seeded["links"][0].id)}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "resource_relationships"
    assert body["total"] >= 1
    assert any(node["resource_id"] == str(seeded["host"].id) for node in body["nodes"])

    async with client(tenant.id) as http:
        missing = await http.get(
            f"{BASE}/blast-radius", params={"link_id": str(uuid.uuid4())}
        )
    assert missing.status_code == 404


async def test_policies_endpoint_reports_the_operational_thresholds(tenant):
    from app.repositories.operational_sla_policy_repository import (
        OperationalSLAPolicyRepository,
    )

    async with AsyncSessionLocal() as db:
        await OperationalSLAPolicyRepository(db).create(
            tenant.id,
            name="wan packet loss",
            metric="packet_loss_pct",
            warning_threshold=1.0,
            critical_threshold=5.0,
            clear_threshold=0.5,
        )
        await db.commit()

    async with client(tenant.id) as http:
        response = await http.get(f"{BASE}/policies")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    policy = body["policies"][0]
    assert policy["metric"] == "packet_loss_pct"
    assert policy["clear_threshold"] == 0.5
    assert policy["min_consecutive_samples"] >= 1


async def test_summary_reports_open_episodes(tenant):
    seeded = await seed_committed_failover(tenant.id)
    async with client(tenant.id) as http:
        response = await http.get(f"{BASE}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["open"] == 1
    assert body["by_status"]["failed_over"] == 1
    assert [item["id"] for item in body["open_episodes"]] == [str(seeded["event"].id)]
    assert "aborted" in body["terminal_statuses"]
