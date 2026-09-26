"""
WAN failover intelligence â€” database suite.

Exercises the real Postgres schema (partial unique index included) through the
repositories and the detector service, then the whole detect -> trace -> verify
lifecycle over a realistic site edge. The assertions focus on the invariants
that make the feature trustworthy:

  * the FSM cannot be written backwards (illegal transitions raise),
  * a flapping path extends ONE episode instead of opening a storm of rows,
  * route/BGP transitions are actually captured (they cannot be rebuilt later),
  * restart-resume works from the persisted baseline,
  * recovery is only recorded after telemetry verification, and
  * one tenant can never observe another tenant's episodes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.monitoring_source import MonitoringSource
from app.models.site import Site
from app.repositories.bgp_neighbor_repository import BgpNeighborRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.failover_detector_state_repository import (
    FailoverDetectorStateRepository,
)
from app.repositories.failover_event_repository import (
    FailoverEventRepository,
    IllegalFailoverTransition,
)
from app.repositories.network_route_repository import NetworkRouteRepository
from app.repositories.operational_sla_policy_repository import (
    OperationalSLAPolicyRepository,
)
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.topology_link_repository import TopologyLinkRepository
from app.services.failover_intelligence import hook
from app.services.failover_intelligence.detector import FailoverDetectorService
from app.services.failover_intelligence.loader import load_scope
from app.services.failover_intelligence.verify import RecoveryVerificationService

pytestmark = pytest.mark.asyncio

SITE_EDGE = "firewall"


async def seed_edge(db, tenant_id, *, wan_links: int = 2):
    """A site with one edge device, `wan_links` WAN circuits, routes and BGP."""
    site = Site(
        tenant_id=tenant_id, name="Branch", code=f"BR-{uuid.uuid4().hex[:4]}"
    )
    db.add(site)
    await db.flush()

    source = MonitoringSource(
        tenant_id=tenant_id,
        name=f"src-{uuid.uuid4().hex[:6]}",
        source_type="prtg",
        is_active=True,
        poll_interval_seconds=60,
        status="connected",
    )
    db.add(source)
    await db.flush()

    edge, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant_id,
        monitoring_source_id=source.id,
        external_id=f"edge-{uuid.uuid4().hex[:6]}",
        name="branch-fw-01",
        device_type=SITE_EDGE,
        status="up",
        site_id=site.id,
        provider="fortigate",
    )
    peer, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant_id,
        monitoring_source_id=source.id,
        external_id=f"peer-{uuid.uuid4().hex[:6]}",
        name="carrier-pe",
        device_type="router",
        status="up",
        provider="cisco",
    )
    branch_host, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant_id,
        monitoring_source_id=source.id,
        external_id=f"host-{uuid.uuid4().hex[:6]}",
        name="branch-lan-sw",
        device_type="switch",
        status="up",
        site_id=site.id,
        provider="cisco",
    )

    links = []
    for index in range(wan_links):
        link, _ = await TopologyLinkRepository(db).upsert(
            tenant_id,
            device_a_id=edge.id,
            device_b_id=peer.id,
            name=f"wan-{index + 1}",
            link_type="wan",
            bandwidth_mbps=100.0,
            latency_ms=8.0 + index * 4,
            packet_loss_pct=0.0,
            oper_status="up",
            admin_status="up",
            redundancy_group="wan-rg",
            provider="acme",
        )
        links.append(link)

    # A dependent resource, so the blast radius has something real to find.
    # NOTE: `resource_relationships` FKs both endpoints to `devices.id`, so the
    # edge is written device -> device. That is also why `compute_for_link`
    # seeds the link's ENDPOINTS: the link itself is never a graph node.
    await ResourceRelationshipRepository(db).upsert(
        tenant_id=tenant_id,
        source_resource_id=branch_host.id,
        target_resource_id=edge.id,
        relationship_type="depends_on",
        label="branch LAN depends on the site edge",
    )

    await NetworkRouteRepository(db).upsert(
        tenant_id=tenant_id,
        resource_id=edge.id,
        destination="0.0.0.0/0",
        protocol="bgp",
        vrf="default",
        next_hop="192.0.2.1",
        outgoing_interface="wan-1",
        state="up",
        is_best=True,
        provider="fortigate",
    )
    await BgpNeighborRepository(db).upsert(
        tenant_id=tenant_id,
        resource_id=edge.id,
        neighbor_ip="192.0.2.1",
        vrf="default",
        state="Established",
        admin_status="up",
        local_as="65001",
        remote_as="65002",
        provider="fortigate",
    )
    # Second tenant so isolation is provable, not assumed.
    other_tenant_id = uuid.uuid4()
    await db.flush()
    return {
        "site": site,
        "edge": edge,
        "peer": peer,
        "host": branch_host,
        "links": links,
        # A different tenant id: reads scoped to it must see nothing. No FK check
        # happens on a read, so this is a valid isolation probe.
        "other_tenant_id": uuid.uuid4(),
    }


# ---------------------------------------------------------------------------
# Policy repository
# ---------------------------------------------------------------------------
async def test_policy_repository_roundtrip_and_soft_delete(tenant, db):
    repo = OperationalSLAPolicyRepository(db)
    policy = await repo.create(
        tenant.id,
        name="wan loss",
        metric="packet_loss_pct",
        warning_threshold=1.0,
        critical_threshold=5.0,
        clear_threshold=0.5,
    )
    assert await repo.count(tenant.id) == 1
    assert [p.name for p in await repo.list_active(tenant.id)] == ["wan loss"]

    await repo.update(policy, critical_threshold=3.0, is_active=False)
    assert await repo.list_active(tenant.id) == []
    assert await repo.count(tenant.id) == 1

    await repo.delete(policy)
    assert await repo.count(tenant.id) == 0
    assert await repo.get(policy.id, tenant.id) is None


async def test_policy_is_tenant_scoped(tenant, db):
    repo = OperationalSLAPolicyRepository(db)
    policy = await repo.create(tenant.id, name="loss", metric="packet_loss_pct")
    assert await repo.get(policy.id, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# Event repository: FSM integrity and dedup
# ---------------------------------------------------------------------------
async def test_event_transitions_are_forward_only(tenant, db):
    repo = FailoverEventRepository(db)
    now = datetime.now(timezone.utc)
    event = await repo.create(
        tenant.id,
        scope_key="site:x",
        dedup_key="site:x|edge|link",
        status="detected",
        detected_at=now,
    )

    advanced = await repo.apply_transition(
        event, status="failed_over", reason="path down"
    )
    assert advanced.status == "failed_over"
    assert [step["status"] for step in advanced.transition_log] == [
        "detected",
        "failed_over",
    ]

    # Backwards and self transitions are rejected, not silently written.
    with pytest.raises(IllegalFailoverTransition):
        await repo.apply_transition(advanced, status="detected")
    with pytest.raises(IllegalFailoverTransition):
        await repo.apply_transition(advanced, status="failed_over")
    with pytest.raises(IllegalFailoverTransition):
        await repo.apply_transition(advanced, status="not-a-status")


async def test_append_only_transition_arrays_never_lose_history(tenant, db):
    repo = FailoverEventRepository(db)
    event = await repo.create(
        tenant.id,
        scope_key="site:y",
        dedup_key="site:y|edge|link",
        status="failed_over",
        detected_at=datetime.now(timezone.utc),
        route_transitions=[{"field": "is_best", "to": True}],
    )
    advanced = await repo.apply_transition(
        event,
        status="recovering",
        append_routes=[{"field": "is_best", "to": False}],
        append_bgp=[{"field": "state", "to": "Idle"}],
    )
    assert len(advanced.route_transitions) == 2
    assert len(advanced.bgp_transitions) == 1


async def test_sequence_is_monotonic_per_scope(tenant, db):
    repo = FailoverEventRepository(db)
    assert await repo.next_sequence(tenant.id, "site:z") == 1
    await repo.create(
        tenant.id,
        scope_key="site:z",
        dedup_key="site:z|a|b",
        status="recovered",
        detected_at=datetime.now(timezone.utc),
        sequence_no=1,
    )
    assert await repo.next_sequence(tenant.id, "site:z") == 2
    assert await repo.next_sequence(tenant.id, "site:other") == 1


async def test_only_one_open_episode_per_dedup_key(tenant, db):
    """The partial unique index is what stops a flapping path creating a storm."""
    repo = FailoverEventRepository(db)
    now = datetime.now(timezone.utc)
    first = await repo.create(
        tenant.id,
        scope_key="site:d",
        dedup_key="site:d|edge|link",
        status="failed_over",
        detected_at=now,
    )
    assert await repo.find_open(tenant.id, "site:d|edge|link") is not None
    assert await repo.find_open_for_scope(tenant.id, "site:d") is not None

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await repo.create(
                tenant.id,
                scope_key="site:d",
                dedup_key="site:d|edge|link",
                status="detected",
                detected_at=now,
            )

    # A terminal (closed) episode frees the key for the next incident.
    await repo.apply_transition(first, status="recovering")
    await repo.apply_transition(first, status="recovered")
    assert await repo.find_open(tenant.id, "site:d|edge|link") is None
    second = await repo.create(
        tenant.id,
        scope_key="site:d",
        dedup_key="site:d|edge|link",
        status="detected",
        detected_at=now,
    )
    assert second.id != first.id


# ---------------------------------------------------------------------------
# Loader: scope ownership
# ---------------------------------------------------------------------------
async def test_a_site_owned_circuit_is_not_evaluated_under_the_siteless_scope(
    tenant, db
):
    seeded = await seed_edge(db, tenant.id)
    site_scope = await load_scope(db, tenant.id, site_id=seeded["site"].id)
    assert {link.name for link in site_scope.links} == {"wan-1", "wan-2"}
    assert site_scope.scope_key == f"site:{seeded['site'].id}"
    assert site_scope.edge_device.id == seeded["edge"].id

    siteless_scope = await load_scope(db, tenant.id, site_id=None)
    assert siteless_scope.links == []  # owned by the site, not double-counted
    assert siteless_scope.skipped_reason


async def test_loader_uses_the_same_snapshot_shape_for_every_consumer(
    tenant, db
):
    seeded = await seed_edge(db, tenant.id)
    snapshot = await load_scope(db, tenant.id, site_id=seeded["site"].id)
    # Snapshots are persisted as JSONB, so any datetime must be serialised. A
    # never-changed link legitimately has no last_state_change_at (NULL), which
    # must stay None rather than become a fabricated timestamp.
    for row in snapshot.link_snapshot().values():
        assert isinstance(row["updated_at"], str)
        assert row["last_state_change_at"] is None or isinstance(
            row["last_state_change_at"], str
        )
    assert snapshot.route_snapshot()
    assert snapshot.bgp_snapshot()
    assert snapshot.link_by_id(seeded["links"][0].id) is not None


# ---------------------------------------------------------------------------
# Detector service: the whole lifecycle
# ---------------------------------------------------------------------------
async def set_link_state(db, tenant_id, link, *, oper="up", latency=None, loss=None):
    updated, _ = await TopologyLinkRepository(db).upsert(
        tenant_id,
        device_a_id=link.device_a_id,
        device_b_id=link.device_b_id,
        name=link.name,
        link_type=link.link_type,
        oper_status=oper,
        latency_ms=latency,
        packet_loss_pct=loss,
    )
    return updated


async def test_detector_captures_detects_and_verifies_one_full_episode(
    tenant, db
):
    seeded = await seed_edge(db, tenant.id)
    primary, backup = seeded["links"]
    edge = seeded["edge"]
    detector = FailoverDetectorService(db, publish=False)
    repo = FailoverEventRepository(db)
    state_repo = FailoverDetectorStateRepository(db)

    # -- 1. first run only establishes a baseline --------------------------
    first = await detector.run_for_tenant(tenant.id)
    assert first["evaluated"] == 1
    assert first["skipped"] == 1  # the "no site" scope owns nothing
    assert first["opened"] == 0
    assert first["actions"] == {"baseline": 1}
    assert (await repo.list(tenant.id))[1] == 0
    state = await state_repo.get(tenant.id, f"site:{seeded['site'].id}")
    assert state is not None and state.consecutive_breaches == 0

    # -- 2. the primary circuit fails, the route and BGP session move ------
    await set_link_state(db, tenant.id, primary, oper="down", latency=250.0)
    await NetworkRouteRepository(db).upsert(
        tenant_id=tenant.id,
        resource_id=edge.id,
        destination="0.0.0.0/0",
        protocol="bgp",
        vrf="default",
        next_hop="198.51.100.1",
        outgoing_interface="wan-2",
        state="up",
        is_best=True,
    )
    await BgpNeighborRepository(db).upsert(
        tenant_id=tenant.id,
        resource_id=edge.id,
        neighbor_ip="192.0.2.1",
        vrf="default",
        state="Active",
        admin_status="up",
    )

    second = await detector.run_for_tenant(tenant.id)
    assert second["opened"] == 1
    rows, total = await repo.list(tenant.id)
    assert total == 1
    event = rows[0]
    assert event.status == "failed_over"
    assert event.sequence_no == 1
    assert event.link_from_name == "wan-1"
    assert event.link_to_name == "wan-2"
    assert event.link_from_id == primary.id
    assert event.failed_over_at is not None
    assert event.is_open

    # Transitions are CAPTURED here - they cannot be rebuilt later, because the
    # route/BGP tables only ever hold the current state.
    assert event.route_transitions, "route transitions must be captured"
    assert event.bgp_transitions, "BGP transitions must be captured"
    assert event.trigger["path_failure"] is True
    assert event.trigger["path_switched"] is True
    assert event.trigger["unavailable_paths"] == ["wan-1"]
    assert any(
        transition["field"] == "state" and transition["to"] == "Active"
        for transition in event.bgp_transitions
    )

    # Blast radius comes from the real topology graph.
    assert event.blast_radius["total"] >= 1
    assert event.blast_radius["by_severity"]["critical"] >= 1
    assert event.blast_radius["source"] == "resource_relationships"
    assert any(
        node["resource_id"] == str(seeded["host"].id)
        for node in event.blast_radius["nodes"]
    )
    assert event.evidence
    assert any(item["finding"] == "supports" for item in event.evidence)

    # -- 3. still down: the SAME episode is extended, not duplicated -------
    third = await detector.run_for_tenant(tenant.id)
    assert third["opened"] == 0
    rows, total = await repo.list(tenant.id)
    assert total == 1
    assert rows[0].id == event.id
    assert rows[0].status == "failed_over"
    state = await state_repo.get(tenant.id, f"site:{seeded['site'].id}")
    assert state.active_episode_id == event.id

    # -- 4-6. the circuit comes back: two clear rounds, then verification ---
    await set_link_state(db, tenant.id, primary, oper="up", latency=9.0)
    await NetworkRouteRepository(db).upsert(
        tenant_id=tenant.id,
        resource_id=edge.id,
        destination="0.0.0.0/0",
        protocol="bgp",
        vrf="default",
        next_hop="192.0.2.1",
        outgoing_interface="wan-1",
        state="up",
        is_best=True,
    )
    await BgpNeighborRepository(db).upsert(
        tenant_id=tenant.id,
        resource_id=edge.id,
        neighbor_ip="192.0.2.1",
        vrf="default",
        state="Established",
        admin_status="up",
    )

    clearing = await detector.run_for_tenant(tenant.id)
    assert clearing["opened"] == 0
    assert clearing["details"][0]["action"] == "none"  # 1 of 2 required rounds
    state = await state_repo.get(tenant.id, f"site:{seeded['site'].id}")
    assert state.consecutive_clear == 1

    recovering = await detector.run_for_tenant(tenant.id)
    assert recovering["details"][0]["action"] == "begin_recovery"
    event = await repo.get(event.id, tenant.id)
    assert event.status == "recovering"
    assert event.recovering_at is not None

    verified = await detector.run_for_tenant(tenant.id)
    assert verified["recovered"] == 1
    event = await repo.get(event.id, tenant.id)
    assert event.status == "recovered"
    assert event.recovered_at is not None
    assert event.verification is not None
    assert event.verification["status"] == "pass"
    assert [step["status"] for step in event.transition_log] == [
        "failed_over",
        "recovering",
        "recovered",
    ]
    assert not event.is_open
    # The restart-safe resume handle is cleared once the episode is closed.
    state = await state_repo.get(tenant.id, f"site:{seeded['site'].id}")
    assert state.active_episode_id is None

    # -- tenant isolation --------------------------------------------------
    assert (await repo.list(seeded["other_tenant_id"]))[1] == 0
    assert await repo.get(event.id, seeded["other_tenant_id"]) is None
    assert await state_repo.get(seeded["other_tenant_id"], event.scope_key) is None


async def test_a_path_failure_right_after_recovery_opens_a_NEW_episode(
    tenant, db
):
    """Terminal episodes are final: a second incident is a second episode."""
    seeded = await seed_edge(db, tenant.id)
    primary = seeded["links"][0]
    detector = FailoverDetectorService(db, publish=False)
    repo = FailoverEventRepository(db)

    await detector.run_for_tenant(tenant.id)
    await set_link_state(db, tenant.id, primary, oper="down")
    await detector.run_for_tenant(tenant.id)

    await set_link_state(db, tenant.id, primary, oper="up")
    for _ in range(3):  # clear window + verification
        await detector.run_for_tenant(tenant.id)

    rows, _total = await repo.list(tenant.id)
    assert [row.status for row in rows] == ["recovered"]

    await set_link_state(db, tenant.id, primary, oper="down")
    await detector.run_for_tenant(tenant.id)

    rows, total = await repo.list(tenant.id)
    assert total == 2
    assert [row.status for row in rows] == ["failed_over", "recovered"]
    assert rows[0].sequence_no == 2


async def test_repeated_failures_of_one_path_do_not_create_an_event_storm(
    tenant, db
):
    """The partial unique index turns flapping into ONE episode."""
    seeded = await seed_edge(db, tenant.id)
    primary = seeded["links"][0]
    detector = FailoverDetectorService(db, publish=False)
    repo = FailoverEventRepository(db)

    await detector.run_for_tenant(tenant.id)
    for _ in range(4):
        await set_link_state(db, tenant.id, primary, oper="down")
        await detector.run_for_tenant(tenant.id)
        await set_link_state(db, tenant.id, primary, oper="up")
        await detector.run_for_tenant(tenant.id)

    rows, total = await repo.list(tenant.id)
    assert total == 1
    assert rows[0].status in {"detected", "failed_over", "recovering"}
    assert rows[0].evidence


# ---------------------------------------------------------------------------
# Dry run + manual verification
# ---------------------------------------------------------------------------
async def test_dry_run_reports_the_round_without_writing_anything(tenant, db):
    seeded = await seed_edge(db, tenant.id)
    detector = FailoverDetectorService(db, publish=False)
    repo = FailoverEventRepository(db)

    report = await detector.dry_run_scope(tenant.id, seeded["site"].id)
    assert report["evaluated_at"]
    assert len(report["candidate_paths"]) == 2
    assert report["decision"]["selected_path"] in {"wan-1", "wan-2"}
    assert report["action"] in {"baseline", "none", "open_failover", "open_breach"}

    assert (await repo.list(tenant.id))[1] == 0
    assert await FailoverDetectorStateRepository(db).get(
        tenant.id, report["scope"]
    ) is None

    # A dry run after real detection must be repeatable without side effects.
    detector_live = FailoverDetectorService(db, publish=False)
    await detector_live.run_for_tenant(tenant.id)
    before = (await repo.list(tenant.id))[1]
    await detector_live.dry_run_scope(tenant.id, seeded["site"].id)
    assert (await repo.list(tenant.id))[1] == before


async def test_manual_verification_records_who_asked_and_is_tenant_scoped(
    tenant, db
):
    seeded = await seed_edge(db, tenant.id)
    detector = FailoverDetectorService(db, publish=False)
    await detector.run_for_tenant(tenant.id)
    await set_link_state(db, tenant.id, seeded["links"][0], oper="down")
    await detector.run_for_tenant(tenant.id)
    event = (await FailoverEventRepository(db).list(tenant.id))[0][0]

    service = RecoveryVerificationService(db)
    assert await service.verify_by_id(seeded["other_tenant_id"], event.id) is None

    result = await service.verify_by_id(tenant.id, event.id, trigger="manual")
    assert result is not None
    assert result["status"] in {"pass", "fail", "unknown"}
    # Recovery is NOT faked: the failed path is still down here.
    assert result["status"] == "fail"

    refreshed = await FailoverEventRepository(db).get(event.id, tenant.id)
    assert refreshed.status == "failed_over"  # verification never rewrites the FSM
    assert refreshed.verification["trigger"] == "manual"
    assert refreshed.attributes["last_verification_trigger"] == "manual"


# ---------------------------------------------------------------------------
# Post-collection hook: bounded, isolated, never raises
# ---------------------------------------------------------------------------
async def test_hook_skips_a_tenant_with_no_topology(tenant, db):
    report = await hook.run_after_collection(db, tenant.id)
    assert report["status"] == "skipped"
    assert "no topology links" in report["reason"]


async def test_hook_runs_and_reports_for_a_real_tenant(tenant, db):
    await seed_edge(db, tenant.id)
    report = await hook.run_after_collection(db, tenant.id)
    assert report["status"] == "ok"
    assert report["scopes"] >= 1
    assert report["errors"] == 0


async def test_hook_never_propagates_a_detector_failure(tenant, db, monkeypatch):
    await seed_edge(db, tenant.id)

    async def boom(self, tenant_id):  # noqa: ARG001
        raise RuntimeError("simulated detector failure")

    monkeypatch.setattr(FailoverDetectorService, "run_for_tenant", boom)
    report = await hook.run_after_collection(db, tenant.id)
    assert report["status"] == "error"
    assert "simulated detector failure" in report["reason"]


async def test_hook_precheck_failure_is_a_report_not_an_exception(
    tenant, db, monkeypatch
):
    async def boom(db, tenant_id):  # noqa: ARG001
        raise RuntimeError("simulated precheck failure")

    monkeypatch.setattr(hook, "has_wan_topology", boom)
    report = await hook.run_after_collection(db, tenant.id)
    assert report["status"] == "error"
    assert "simulated precheck failure" in report["reason"]
