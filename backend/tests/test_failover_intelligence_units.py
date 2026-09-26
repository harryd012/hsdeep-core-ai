"""
WAN failover intelligence — pure-logic suite (no database).

Covers the pieces that must be exactly right and are cheap to pin down: the
threshold/hysteresis maths, scope precedence, the topology -> decision-engine
bridge, the FSM transition table, blast-radius traversal, simulation, recovery
verification, evidence findings and replay provenance.

Every case asserts a *reasoned* behaviour rather than a shape - e.g. "a missing
metric produces no verdict" or "a merely degraded path opens nothing" - because
those are the behaviours that keep the feature honest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.failover_intelligence import (
    blast_radius,
    evidence,
    paths,
    policies,
    replay,
    simulation,
    verify,
)
from app.services.failover_intelligence.detector import (
    analyze_round,
    diff_rows,
    evaluate_breaches,
    evaluate_scope_paths,
)
from app.services.failover_intelligence.loader import ScopeSnapshot

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def make_link(
    name: str,
    *,
    oper: str = "up",
    admin: str = "up",
    latency: float | None = 10.0,
    loss: float | None = 0.0,
    ltype: str = "wan",
    redundancy_group: str | None = "rg-1",
    jitter: float | None = None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        link_type=ltype,
        oper_status=oper,
        admin_status=admin,
        latency_ms=latency,
        packet_loss_pct=loss,
        utilization_pct=5.0,
        bandwidth_mbps=100.0,
        provider="acme",
        redundancy_group=redundancy_group,
        attributes={"jitter_ms": jitter} if jitter is not None else {},
        device_a_id=uuid.uuid4(),
        device_b_id=uuid.uuid4(),
        last_state_change_at=NOW,
        updated_at=NOW - timedelta(seconds=30),
        created_at=NOW,
    )


def make_policy(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "name": "policy",
        "metric": "packet_loss_pct",
        "scope": "all_wan",
        "scope_id": None,
        "warning_threshold": 1.0,
        "critical_threshold": 5.0,
        "clear_threshold": 0.5,
        "min_consecutive_samples": 2,
        "is_active": True,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_snapshot(*links, site_id=None) -> ScopeSnapshot:
    snapshot = ScopeSnapshot(
        tenant_id=uuid.uuid4(),
        site_id=site_id or uuid.uuid4(),
        scope_key="site:test",
    )
    snapshot.links = list(links)
    snapshot.edge_device = SimpleNamespace(
        id=uuid.uuid4(), name="edge-fw", device_type="firewall"
    )
    return snapshot


def previous_state(result, **overrides):
    """Build a stand-in detector-state row from a RoundResult."""
    fields = {
        "previous_decision": result.decision,
        "route_snapshot": result.route_snapshot,
        "bgp_snapshot": result.bgp_snapshot,
        "link_snapshot": result.link_snapshot,
        "attributes": {"levels": result.levels},
        "consecutive_breaches": result.consecutive_breaches,
        "consecutive_clear": result.consecutive_clear,
        "last_breach_at": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# Policies: thresholds and hysteresis
# ---------------------------------------------------------------------------
def test_higher_is_worse_metric_levels():
    policy = make_policy()
    assert policies.classify(policy, 9.0) == "critical"
    assert policies.classify(policy, 2.0) == "warning"
    assert policies.classify(policy, 0.2) is None


def test_lower_is_worse_metric_levels():
    availability = make_policy(
        metric="availability_pct",
        warning_threshold=99.0,
        critical_threshold=95.0,
        clear_threshold=99.9,
    )
    assert policies.classify(availability, 90.0) == "critical"
    assert policies.classify(availability, 98.0) == "warning"
    assert policies.classify(availability, 99.95) is None


def test_missing_telemetry_produces_no_verdict():
    """An absent sample is never 0 (perfect) and never critical (guessed)."""
    assert policies.evaluate_policy(make_policy(), None) is None
    assert policies.evaluate_policy(make_policy(), "not-a-number") is None
    assert policies.evaluate_policy(make_policy(), float("nan")) is None
    assert policies.evaluate_policy(make_policy(), True) is None


def test_clear_level_applies_hysteresis():
    policy = make_policy()
    hit = policies.evaluate_policy(policy, 0.1, previous_level="critical")
    assert hit is not None and hit.level == "clear"

    # Still above the warning level after a breach: reported as a warning
    # (a downgrade), never as "clear" - so the breach counter is not reset.
    warning = policies.evaluate_policy(policy, 3.0, previous_level="critical")
    assert warning is not None and warning.level == "warning"
    # Still critical: reports critical again.
    assert policies.evaluate_policy(policy, 9.0, previous_level="critical").level == (
        "critical"
    )


def test_clear_level_falls_back_to_warning_when_absent():
    policy = make_policy(clear_threshold=None)
    hit = policies.evaluate_policy(policy, 0.2, previous_level="critical")
    assert hit is not None and hit.level == "clear"
    assert hit.threshold == 1.0


def test_no_clear_verdict_when_just_monitoring():
    """Without a prior breach there is no 'clear' noise on every quiet round."""
    assert policies.evaluate_policy(make_policy(), 0.1, previous_level=None) is None


def test_metric_value_reads_columns_then_attributes():
    link = make_link("l1", jitter=7.5)
    assert policies.metric_value(link, "latency_ms") == 10.0
    assert policies.metric_value(link, "jitter_ms") == 7.5
    assert policies.metric_value(link, "not_collected") is None
    assert policies.metric_value({"utilization_pct": 42.0}, "utilization_pct") == 42.0


# ---------------------------------------------------------------------------
# Policies: scope precedence
# ---------------------------------------------------------------------------
def test_scope_precedence_link_beats_site_beats_all_wan():
    site_id = uuid.uuid4()
    link_id = uuid.uuid4()
    tenant_default = make_policy(name="default", scope="all_wan")
    site_policy = make_policy(name="site", scope="site", scope_id=site_id)
    link_policy = make_policy(name="link", scope="link", scope_id=link_id)

    resolved = policies.resolve_policies(
        [tenant_default, site_policy, link_policy], link_id=link_id, site_id=site_id
    )
    assert resolved["packet_loss_pct"].name == "link"

    resolved = policies.resolve_policies(
        [tenant_default, site_policy], link_id=link_id, site_id=site_id
    )
    assert resolved["packet_loss_pct"].name == "site"

    resolved = policies.resolve_policies(
        [tenant_default], link_id=link_id, site_id=site_id
    )
    assert resolved["packet_loss_pct"].name == "default"


def test_scope_resolution_ignores_other_sites_and_inactive_policies():
    other_site = uuid.uuid4()
    site_id = uuid.uuid4()
    foreign = make_policy(scope="site", scope_id=other_site)
    inactive = make_policy(scope="site", scope_id=site_id, is_active=False)
    resolved = policies.resolve_policies([foreign, inactive], site_id=site_id)
    assert resolved == {}


def test_scope_resolution_is_per_metric():
    site_id = uuid.uuid4()
    loss = make_policy(metric="packet_loss_pct", scope="all_wan")
    latency = make_policy(metric="latency_ms", scope="site", scope_id=site_id)
    resolved = policies.resolve_policies([loss, latency], site_id=site_id)
    assert set(resolved) == {"packet_loss_pct", "latency_ms"}


# ---------------------------------------------------------------------------
# Paths: real topology -> the existing decision engine
# ---------------------------------------------------------------------------
def test_link_types_and_availability():
    assert paths.is_candidate_link(make_link("wan1"))
    assert paths.is_candidate_link(make_link("odd", ltype="weird"))  # redundancy group
    assert not paths.is_candidate_link(
        make_link("lan1", ltype="lan", redundancy_group=None)
    )
    assert paths.is_available(make_link("up1"))
    assert not paths.is_available(make_link("down1", oper="down"))
    assert not paths.is_available(make_link("admin-down", admin="down"))
    assert not paths.is_available(None)


def test_missing_numerics_are_passed_through_not_zeroed():
    """Unknown telemetry must not score as a perfect path."""
    payload = paths.link_payload(make_link("l1", latency=None, loss=None))
    assert payload["latency_ms"] is None
    assert payload["packet_loss_pct"] is None
    worst_case = paths.to_wan_path(make_link("l1", latency=None, loss=None))
    assert worst_case.latency_ms > 100.0
    assert worst_case.packet_loss_pct > 0.0


def test_duplicate_link_names_resolve_deterministically():
    live = make_link("same", latency=5.0)
    dead = make_link("same", oper="down")
    unique, duplicates = paths.unique_links_by_name([dead, live])
    assert [link.name for link in unique] == ["same"]
    assert duplicates == ["same"]
    assert paths.is_available(unique[0])  # the live one wins


def test_decide_reuses_the_engine_and_maps_back_to_links():
    primary = make_link("primary", latency=5.0)
    backup = make_link("backup", latency=80.0)
    decision = paths.decide([primary, backup])
    assert decision["selected_path"] == "primary"
    assert decision["decision"] == "FORWARD"
    assert paths.selected_link(decision, [primary, backup]) is primary

    primary.oper_status = "down"
    decision = paths.decide([primary, backup])
    assert decision["selected_path"] == "backup"
    assert decision["failover"] is True
    assert paths.selected_link(decision, [primary, backup]) is backup


# ---------------------------------------------------------------------------
# Detector: baseline diffs
# ---------------------------------------------------------------------------
def test_diff_rows_needs_a_baseline_before_calling_anything_a_change():
    current = {"k": {"state": "up", "is_best": True}}
    assert diff_rows(None, current, kind="route", fields=("is_best",), at=NOW) == []
    assert diff_rows({}, current, kind="route", fields=("is_best",), at=NOW) == []


def test_diff_rows_records_field_changes():
    key = "a|vrf|0.0.0.0/0|bgp"
    previous = {
        key: {"state": "up", "is_best": False, "protocol": "bgp", "destination": "0.0.0.0/0"}
    }
    current = {
        key: {"state": "up", "is_best": True, "protocol": "bgp", "destination": "0.0.0.0/0"}
    }
    transitions = diff_rows(
        previous, current, kind="route", fields=("is_best", "state"), at=NOW
    )
    assert len(transitions) == 1
    assert transitions[0]["field"] == "is_best"
    assert transitions[0]["from"] is False and transitions[0]["to"] is True
    assert transitions[0]["label"] == "0.0.0.0/0 (bgp)"
    assert transitions[0]["at"] == NOW.isoformat()


def test_diff_rows_ignores_insignificant_appearances():
    """A new backup route is not news; a new BEST route is."""
    previous = {"a": {"state": "up", "is_best": True, "destination": "10.0.0.0/8"}}
    backup = {"b": {"state": "up", "is_best": False, "destination": "10.1.0.0/16"}}
    assert diff_rows(
        previous, {**previous, **backup}, kind="route", fields=("is_best",), at=NOW
    ) == []

    best = {"b": {"state": "up", "is_best": True, "destination": "10.1.0.0/16"}}
    transitions = diff_rows(
        previous, {**previous, **best}, kind="route", fields=("is_best",), at=NOW
    )
    assert len(transitions) == 1 and transitions[0]["field"] == "entry"


def test_diff_rows_records_bgp_state_change():
    sessions = {"a|vrf|10.0.0.1": {"state": "Established", "neighbor_ip": "10.0.0.1"}}
    transitions = diff_rows(
        sessions,
        {"a|vrf|10.0.0.1": {"state": "Idle", "neighbor_ip": "10.0.0.1"}},
        kind="bgp",
        fields=("state",),
        at=NOW,
    )
    assert len(transitions) == 1
    assert transitions[0]["from"] == "Established" and transitions[0]["to"] == "Idle"


# ---------------------------------------------------------------------------
# Detector: breach evaluation
# ---------------------------------------------------------------------------
def test_evaluate_breaches_counts_and_levels():
    snapshot = make_snapshot(make_link("l1", loss=9.0))
    policy = make_policy()
    hits, critical, required, levels = evaluate_breaches(snapshot, [policy])
    assert critical is True and required == 2
    assert hits[0]["metric"] == "packet_loss_pct" and hits[0]["level"] == "critical"
    assert levels[str(snapshot.links[0].id)]["packet_loss_pct"] == "critical"


def test_evaluate_breaches_without_policies_is_silent():
    snapshot = make_snapshot(make_link("l1", loss=99.0))
    hits, critical, required, levels = evaluate_breaches(snapshot, [])
    assert hits == [] and critical is False and required == 2
    assert levels[str(snapshot.links[0].id)] == {}


def test_evaluate_breaches_honours_previous_level_for_hysteresis():
    snapshot = make_snapshot(make_link("l1", loss=0.1))
    policy = make_policy()
    prior = {str(snapshot.links[0].id): {"packet_loss_pct": "critical"}}
    hits, critical, _required, levels = evaluate_breaches(
        snapshot, [policy], previous_levels=prior
    )
    assert hits == [] and critical is False
    assert levels[str(snapshot.links[0].id)]["packet_loss_pct"] == "clear"


# ---------------------------------------------------------------------------
# Detector: FSM
# ---------------------------------------------------------------------------
def test_first_round_is_a_baseline_and_opens_nothing():
    snapshot = make_snapshot(make_link("primary"), make_link("backup"))
    result = analyze_round(snapshot, policies=[make_policy()], previous=None, now=NOW)
    assert result.action == "baseline"
    assert result.route_transitions == []  # nothing can be called a change yet
    assert result.link_transitions == []
    assert result.next_status is None


def test_degraded_but_working_path_opens_no_episode():
    """The engine's failover flag is true for degradation; that is not an event."""
    snapshot = make_snapshot(
        make_link("primary", latency=400.0, loss=4.0), make_link("backup", latency=10.0)
    )
    first = analyze_round(snapshot, policies=[], previous=None, now=NOW)
    second = analyze_round(
        snapshot, policies=[], previous=previous_state(first), open_status=None, now=NOW
    )
    assert second.failover is True  # engine says degraded
    assert second.path_failure is False  # nothing actually stopped working
    assert second.action == "none"


def test_path_failure_opens_a_failed_over_episode():
    primary, backup = make_link("primary", latency=5.0), make_link("backup", latency=60.0)
    snapshot = make_snapshot(primary, backup)
    first = analyze_round(snapshot, policies=[make_policy()], previous=None, now=NOW)

    primary.oper_status = "down"
    result = analyze_round(
        snapshot, policies=[make_policy()], previous=previous_state(first),
        open_status=None, now=NOW,
    )
    assert result.action == "open_failover"
    assert result.next_status == "failed_over"
    assert result.failed_link is primary
    assert result.selected_link is backup
    assert result.path_failure is True and result.path_switched is True
    assert result.unavailable_labels == ["primary"]
    assert any(t["field"] == "oper_status" for t in result.link_transitions)


def test_confirmed_critical_breach_opens_a_detected_episode():
    link = make_link("only-path", loss=9.0)
    snapshot = make_snapshot(link)
    policy = make_policy()
    first = analyze_round(snapshot, policies=[policy], previous=None, now=NOW)
    # The baseline round already SAW the critical breach, so it counts as the
    # first observation (nothing is invented, nothing is ignored).
    assert first.consecutive_breaches == 1

    # 1 of 2 required rounds: not enough to open an episode yet.
    not_yet = analyze_round(
        snapshot, policies=[policy],
        previous=previous_state(first, consecutive_breaches=0),
        open_status=None, now=NOW,
    )
    assert not_yet.action == "none"
    assert "not yet confirmed" in not_yet.reason
    assert not_yet.consecutive_breaches == 1

    confirmed = analyze_round(
        snapshot, policies=[policy],
        previous=previous_state(first, consecutive_breaches=1),
        open_status=None, now=NOW,
    )
    assert confirmed.action == "open_breach"
    assert confirmed.next_status == "detected"
    assert confirmed.consecutive_breaches == 2


def test_failed_over_episode_escalates_from_detected_when_path_fails():
    primary, backup = make_link("primary"), make_link("backup", latency=60.0)
    snapshot = make_snapshot(primary, backup)
    first = analyze_round(snapshot, policies=[], previous=None, now=NOW)
    primary.oper_status = "down"
    result = analyze_round(
        snapshot, policies=[], previous=previous_state(first),
        open_status="detected", now=NOW,
    )
    assert result.action == "escalate" and result.next_status == "failed_over"


def test_recovery_requires_the_confirmation_window_then_verifies():
    primary, backup = make_link("primary"), make_link("backup", latency=60.0)
    snapshot = make_snapshot(primary, backup)
    baseline = analyze_round(snapshot, policies=[], previous=None, now=NOW)
    primary.oper_status = "down"
    failed = analyze_round(
        snapshot, policies=[], previous=previous_state(baseline), open_status=None, now=NOW
    )

    primary.oper_status = "up"
    first_clear = analyze_round(
        snapshot, policies=[], previous=previous_state(failed, consecutive_clear=0),
        open_status="failed_over", now=NOW,
    )
    assert first_clear.action == "none"  # 1 of 2 clear rounds
    assert first_clear.consecutive_clear == 1

    second_clear = analyze_round(
        snapshot, policies=[], previous=previous_state(failed, consecutive_clear=1),
        open_status="failed_over", now=NOW,
    )
    assert second_clear.action == "begin_recovery"
    assert second_clear.next_status == "recovering"

    verifying = analyze_round(
        snapshot, policies=[], previous=previous_state(second_clear, consecutive_clear=2),
        open_status="recovering", now=NOW,
    )
    assert verifying.action == "verify"


def test_breach_that_clears_without_a_path_switch_is_aborted():
    link = make_link("only-path", loss=9.0)
    snapshot = make_snapshot(link)
    policy = make_policy()
    first = analyze_round(snapshot, policies=[policy], previous=None, now=NOW)
    breached = analyze_round(
        snapshot, policies=[policy], previous=previous_state(first, consecutive_breaches=1),
        open_status=None, now=NOW,
    )
    assert breached.action == "open_breach"

    link.packet_loss_pct = 0.0
    cleared = analyze_round(
        snapshot, policies=[policy], previous=previous_state(breached, consecutive_clear=1),
        open_status="detected", now=NOW,
    )
    assert cleared.action == "abort" and cleared.next_status == "aborted"
    assert "not a failover" in cleared.reason


def test_all_paths_down_still_records_a_failure_without_raising():
    link = make_link("only-path")
    snapshot = make_snapshot(link)
    first = analyze_round(snapshot, policies=[], previous=None, now=NOW)
    link.oper_status = "down"
    result = analyze_round(
        snapshot, policies=[], previous=previous_state(first), open_status=None, now=NOW
    )
    assert result.action == "open_failover"
    assert result.selected_link is None
    assert result.decision["decision"] == "NO_AVAILABLE_PATH"
    assert evaluate_scope_paths(snapshot, first.decision).path_failure is True


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------
def test_blast_radius_walks_dependents_not_dependencies():
    failing, dependent, transitive, unrelated = (str(uuid.uuid4()) for _ in range(4))
    edges = [
        # `dependent` needs `failing` -> failing must affect it.
        {"source": dependent, "target": failing, "relationship_type": "depends_on"},
        # `failing` needs `unrelated` -> failing does NOT break it.
        {"source": failing, "target": unrelated, "relationship_type": "depends_on"},
        {"source": transitive, "target": dependent, "relationship_type": "connected_to"},
    ]
    result = blast_radius.traverse([failing], edges)
    ids = {node["resource_id"]: node for node in result["nodes"]}
    assert dependent in ids and unrelated not in ids
    assert ids[dependent]["severity"] == "critical"
    assert transitive in ids
    assert ids[transitive]["severity"] == "warning"  # hop 2
    # The path is recorded from the seed outwards, so the hop-1 relation leads.
    assert ids[transitive]["via"] == ["depends_on", "connected_to"]


def test_blast_radius_is_bounded_and_says_so():
    seed = str(uuid.uuid4())
    edges = [
        {"source": str(uuid.uuid4()), "target": seed, "relationship_type": "depends_on"}
        for _ in range(20)
    ]
    result = blast_radius.traverse([seed], edges, max_nodes=5)
    assert result["total"] == 5
    assert result["truncated"] is True
    assert result["truncated_reason"]


def test_blast_radius_depth_bound_is_honoured():
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    edges = [
        {"source": b, "target": a, "relationship_type": "depends_on"},
        {"source": c, "target": b, "relationship_type": "depends_on"},
    ]
    assert blast_radius.traverse([a], edges, max_depth=1)["total"] == 1
    assert blast_radius.traverse([a], edges, max_depth=2)["total"] == 2


def test_blast_radius_ignores_self_edges_and_empty_seeds():
    node = str(uuid.uuid4())
    assert blast_radius.traverse([node], [{"source": node, "target": node}])["total"] == 0
    assert blast_radius.traverse([], [])["total"] == 0


# ---------------------------------------------------------------------------
# Recovery verification
# ---------------------------------------------------------------------------
def _good_decision(**overrides):
    base = {
        "decision": "FORWARD",
        "selected_path": "backup",
        "score": 90.0,
        "failover": False,
        "paths": [],
    }
    base.update(overrides)
    return base


def test_verification_passes_on_healthy_telemetry():
    result = verify.verify_recovery(
        link_to=SimpleNamespace(
            name="backup", oper_status="up", admin_status="up", updated_at=NOW
        ),
        candidate_links=[
            SimpleNamespace(name="primary", oper_status="up", admin_status="up"),
            SimpleNamespace(name="backup", oper_status="up", admin_status="up"),
        ],
        decision=_good_decision(),
        breaches=[],
        bgp=[{"state": "Established", "neighbor_ip": "10.0.0.1"}],
        routes=[{"is_best": True}],
        consecutive_clear=2,
        required_samples=2,
        now=NOW,
    )
    assert result.status == "pass"
    assert result.to_dict()["counts"]["fail"] == 0


def test_verification_fails_when_a_core_check_is_contradicted():
    result = verify.verify_recovery(
        link_to=SimpleNamespace(
            name="primary", oper_status="up", admin_status="up", updated_at=NOW
        ),
        candidate_links=[
            SimpleNamespace(name="primary", oper_status="up", admin_status="up"),
            SimpleNamespace(name="backup", oper_status="down", admin_status="up"),
        ],
        decision=_good_decision(decision="NO_AVAILABLE_PATH", selected_path=None),
        breaches=[
            {"level": "critical", "metric": "packet_loss_pct", "observed": 22.0,
             "link_name": "primary"}
        ],
        bgp=[],
        routes=[],
        consecutive_clear=0,
        required_samples=2,
        now=NOW,
    )
    assert result.status == "fail"
    failed = {check["name"] for check in result.checks if check["status"] == "fail"}
    assert {"decision_not_failing_over", "no_critical_breach", "confirmation_window"} <= failed


def test_verification_reports_unknown_rather_than_guessing():
    """No BGP/routes collected: informative checks are unknown, not 'pass'."""
    result = verify.verify_recovery(
        link_to=SimpleNamespace(
            name="backup", oper_status="up", admin_status="up", updated_at=None
        ),
        candidate_links=[
            SimpleNamespace(name="primary", oper_status="up", admin_status="up"),
            SimpleNamespace(name="backup", oper_status="up", admin_status="up"),
        ],
        decision=_good_decision(),
        breaches=[],
        bgp=[],
        routes=[],
        consecutive_clear=2,
        required_samples=2,
        now=NOW,
    )
    assert result.status == "pass"  # core checks all passed
    by_name = {check["name"]: check["status"] for check in result.checks}
    assert by_name["bgp_sessions_established"] == "unknown"
    assert by_name["best_routes_present"] == "unknown"
    assert by_name["telemetry_fresh"] == "unknown"


def test_verification_is_unknown_when_the_confirmation_window_is_unmet():
    result = verify.verify_recovery(
        link_to=None,
        candidate_links=[],
        decision={"decision": "FORWARD", "selected_path": "backup"},
        breaches=[],
        consecutive_clear=0,
        required_samples=2,
        now=NOW,
    )
    assert result.status == "fail"  # unusable + unconfirmed
    assert "NOT verified" in result.summary


# ---------------------------------------------------------------------------
# Counterfactual simulation
# ---------------------------------------------------------------------------
def test_simulation_shows_the_path_the_engine_would_pick():
    primary = make_link("primary", latency=5.0)
    backup = make_link("backup", latency=60.0)
    report = simulation.simulate([primary, backup])
    assert report.changed is False
    assert report.baseline["selected_path"] == "primary"

    failover = simulation.simulate(
        [primary, backup], {"links": {"primary": {"available": False}}}
    )
    assert failover.changed is True
    assert failover.simulated["selected_path"] == "backup"
    assert failover.delta["paths_lost"] == ["primary"]
    assert failover.delta["path_switched"] is True
    assert failover.delta["no_path_left"] is False


def test_simulation_reports_unresolved_targets_instead_of_ignoring_them():
    report = simulation.simulate(
        [make_link("primary")], {"links": {"does-not-exist": {"available": False}}}
    )
    assert report.unresolved_targets == ["does-not-exist"]
    assert report.changed is False


def test_simulation_scale_multiplier_applies_to_measured_values():
    report = simulation.simulate(
        [make_link("primary", latency=10.0)], {"scale": {"latency_ms": 3.0}}
    )
    assert report.applied == []  # nothing targeted directly by name/id
    assert report.delta["selected_path"] == {"before": "primary", "after": "primary"}
    # A 3x latency multiplier leaves the ONLY path degraded, which the engine
    # reports as a failover condition. Recording that here documents the
    # distinction the detector relies on: the engine's flag is true for mere
    # degradation, so degradation alone must never open an episode.
    assert report.changed is True
    assert report.simulated["failover"] is True
    assert report.delta["paths_lost"] == []


def test_simulation_can_remove_all_paths():
    report = simulation.simulate(
        [make_link("only"), make_link("backup")],
        {"links": {"only": {"available": False}, "backup": {"available": False}}},
    )
    assert report.delta["no_path_left"] is True
    assert report.simulated["selected_path"] is None
    assert report.simulated["decision"] == "NO_AVAILABLE_PATH"


def test_simulation_reports_the_method_so_nobody_reads_it_as_a_prediction():
    assert "counterfactual" in simulation.simulate([make_link("l1")]).method


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
def test_evidence_sorts_supporting_first_and_marks_findings():
    failed = make_link("primary", oper="down")
    replacement = make_link("backup")
    items = evidence.collect(
        link_from=failed,
        link_to=replacement,
        candidate_links=[failed, replacement],
        decision={"selected_path": "backup", "score": 80.0, "decision": "FORWARD"},
        breaches=[
            {"level": "critical", "metric": "packet_loss_pct", "observed": 30.0,
             "policy_name": "loss", "scope": "all_wan", "threshold": 5.0}
        ],
        route_transitions=[{"field": "is_best", "to": True}],
        bgp_transitions=[{"to": "Idle"}],
        blast_radius={"total": 2, "by_severity": {"critical": 1}},
        now=NOW,
    )
    findings = [item["finding"] for item in items]
    assert findings == sorted(findings, key=lambda f: ["supports", "neutral", "contradicts"].index(f))
    assert findings[0] == "supports"
    summary = evidence.summarise(items)
    assert summary["total"] == len(items)
    assert summary["strongest_support"]


def test_evidence_says_when_a_signal_was_not_captured():
    items = evidence.collect(
        link_from=make_link("primary", oper="down"),
        candidate_links=[make_link("primary", oper="down")],
        route_transitions=[],
        bgp_transitions=[],
        now=NOW,
    )
    neutral_claims = " ".join(
        item["claim"] for item in items if item["finding"] == "neutral"
    )
    assert "no routing changes captured" in neutral_claims
    assert "no BGP session changes captured" in neutral_claims


def test_evidence_contradicts_when_the_replacement_path_is_also_down():
    primary, backup = make_link("primary", oper="down"), make_link("backup", oper="down")
    items = evidence.collect(
        link_from=primary,
        link_to=backup,
        candidate_links=[primary, backup],
        decision={"selected_path": None, "decision": "NO_AVAILABLE_PATH"},
        now=NOW,
    )
    assert any(item["finding"] == "contradicts" for item in items)


# ---------------------------------------------------------------------------
# Replay (time machine)
# ---------------------------------------------------------------------------
def make_event(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "sequence_no": 4,
        "status": "recovered",
        "is_open": False,
        "scope_key": "site:test",
        "site_id": None,
        "edge_device_id": None,
        "link_from_name": "primary",
        "link_to_name": "backup",
        "detected_at": NOW,
        "failed_over_at": NOW,
        "recovering_at": NOW + timedelta(seconds=5),
        "recovered_at": NOW + timedelta(seconds=10),
        "status_changed_at": NOW + timedelta(seconds=10),
        "trigger": {"reason": "path down", "path_failure": True, "breaches": []},
        "decision": {"selected_path": "backup", "score": 88.0, "decision": "FORWARD",
                     "reason": "backup is best", "paths": [{"name": "backup"}]},
        "transition_log": [
            {"status": "failed_over", "at": NOW.isoformat(), "reason": "path down"},
            {"status": "recovering", "at": (NOW + timedelta(seconds=5)).isoformat(),
             "reason": "healthy"},
            {"status": "recovered", "at": (NOW + timedelta(seconds=10)).isoformat(),
             "reason": "verified"},
        ],
        "route_transitions": [
            {"at": NOW.isoformat(), "label": "0.0.0.0/0 (bgp)", "field": "is_best",
             "from": False, "to": True, "key": "k"}
        ],
        "bgp_transitions": [],
        "blast_radius": {"total": 3, "by_severity": {"critical": 1, "warning": 2},
                         "nodes": []},
        "evidence": [{"kind": "link_from_state", "claim": "primary is down",
                      "finding": "supports"}],
        "verification": {"status": "pass", "summary": "verified", "checks": [],
                         "trigger": "detector"},
        "incident_id": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_replay_orders_the_timeline_and_tags_provenance():
    result = replay.build_timeline(make_event(), now=NOW)
    kinds = [entry["kind"] for entry in result["timeline"]]
    assert kinds.index("detection") < kinds.index("verification")
    assert kinds.count("status") == 3
    assert result["provenance_summary"]["captured"] > 0
    assert result["provenance_summary"]["derived"] == 1  # the blast radius
    assert result["provenance_summary"]["context"] == 0


def test_replay_names_the_signals_it_did_not_capture():
    result = replay.build_timeline(make_event(bgp_transitions=[]), now=NOW)
    assert any("BGP" in gap for gap in result["gaps"])
    assert any("alerts" in gap for gap in result["gaps"])
    assert result["coverage"]["is_partial"] is True


def test_replay_computes_phase_durations_and_total_coverage():
    result = replay.build_timeline(make_event(), now=NOW)
    assert result["coverage"]["duration_seconds"] == 10.0
    assert result["coverage"]["still_open"] is False
    assert [phase["seconds"] for phase in result["phases"]] == [0.0, 5.0, 5.0]


def test_replay_marks_alerts_and_incidents_as_context():
    alert = SimpleNamespace(
        id=uuid.uuid4(), title="Loss on primary", severity="critical",
        status="open", device_id=None, occurred_at=NOW + timedelta(seconds=1),
        created_at=NOW,
    )
    incident = SimpleNamespace(
        id=uuid.uuid4(), title="WAN down", status="open", severity="high",
        created_at=NOW + timedelta(seconds=2),
    )
    result = replay.build_timeline(make_event(), alerts=[alert], incident=incident, now=NOW)
    context = [
        entry for entry in result["timeline"] if entry["provenance"] == "context"
    ]
    assert len(context) == 2
    assert {entry["kind"] for entry in context} == {"alert", "incident"}
    assert not any("alerts" in gap for gap in result["gaps"])


def test_replay_uses_readable_titles_for_fsm_steps():
    result = replay.build_timeline(make_event(), now=NOW)
    titles = {entry["title"] for entry in result["timeline"]}
    assert "Failover observed" in titles
    assert "Recovery verified" in titles


def test_replay_of_an_open_episode_says_so_and_flags_unverified_recovery():
    result = replay.build_timeline(
        make_event(
            status="failed_over", is_open=True, verification=None,
            recovered_at=None, status_changed_at=NOW,
        ),
        now=NOW,
    )
    assert result["coverage"]["still_open"] is True
    assert any("not been verified" in gap for gap in result["gaps"])


def test_replay_of_a_bare_episode_does_not_invent_entries():
    bare = make_event(
        transition_log=[], route_transitions=[], bgp_transitions=[], blast_radius={},
        evidence=[], verification=None, failed_over_at=None, recovering_at=None,
        recovered_at=None,
    )
    result = replay.build_timeline(bare, now=NOW)
    kinds = [entry["kind"] for entry in result["timeline"]]
    assert kinds == ["detection", "decision"]
    assert len(result["gaps"]) >= 4


@pytest.mark.parametrize(
    "status,expected",
    [
        ("detected", "detected"),
        ("recovered", "recovered"),
        ("aborted", "aborted"),
    ],
)
def test_status_titles_cover_the_fsm(status, expected):
    assert status in replay.STATUS_TITLES or expected in replay.STATUS_TITLES.values()
