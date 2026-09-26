"""
WAN failover detector — capture, explain, verify. Never control.

HSDEEP does NOT decide failovers. The SD-WAN / HA Performance-SLA mechanism on
the network owns that decision; this detector *observes* it. What it adds is the
history nobody else keeps: ``TopologyLink``, ``NetworkRoute`` and
``BgpNeighbor``
are refreshed in place by every collection run, so "which path was carrying
traffic, what changed, when" is destroyed by the next poll. Catching a failover
therefore requires comparing two consecutive rounds and persisting the
difference — which is exactly what this module does, in one bounded pass per
site edge.

The round is a pure function (``analyze_round``) wrapped by a thin
persistence-facing service:

1. load one bounded snapshot of the site edge (``loader.load_scope``)
2. run the EXISTING path decision engine over the real candidate links
   (``paths.decide`` -> ``sdwan.evaluate_paths``)
3. evaluate operational SLA policies against the same links (``policies``)
4. diff this round against the stored baseline -> route / BGP / link transitions
5. resolve an FSM action and apply it to the open episode (if any)

WHEN AN EPISODE OPENS
---------------------
Only on an observable event, never on a guess:

* **path failure** - at least one candidate WAN path stopped being usable, or
  the engine reports ``NO_AVAILABLE_PATH``. Something really stopped carrying
  traffic.
* **confirmed critical breach** - a configured operational SLA policy crossed
  its critical level for ``min_consecutive_samples`` consecutive rounds.

Note the deliberate distinction from the engine's own ``failover`` flag: that
flag also turns true for a merely *degraded* but still-working path. That is the
right semantics for path selection and the wrong trigger for an incident record,
so degradation alone opens nothing. It is still captured verbatim in the
episode's ``decision`` payload when an episode does open.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.failover_event import (
    STATUS_ABORTED,
    STATUS_DETECTED,
    STATUS_FAILED_OVER,
    STATUS_RECOVERED,
    STATUS_RECOVERING,
    STATUS_VERIFICATION_FAILED,
)
from app.repositories.failover_detector_state_repository import (
    FailoverDetectorStateRepository,
)
from app.repositories.failover_event_repository import FailoverEventRepository
from app.repositories.operational_sla_policy_repository import (
    OperationalSLAPolicyRepository,
)
from app.services.events.event_bus import EventType
from app.services.failover_intelligence import evidence as evidence_module
from app.services.failover_intelligence import paths as path_module
from app.services.failover_intelligence import policies as policy_module
from app.services.failover_intelligence.loader import (
    ScopeSnapshot,
    dedup_key_for,
    load_scope,
    list_scopes,
)

logger = logging.getLogger(__name__)

DEFAULT_MIN_SAMPLES = 2

#: FSM actions resolved by ``analyze_round`` (pure) and applied by the service.
ACTION_BASELINE = "baseline"
ACTION_NONE = "none"
ACTION_OPEN_FAILOVER = "open_failover"
ACTION_OPEN_BREACH = "open_breach"
ACTION_ESCALATE = "escalate"
ACTION_BEGIN_RECOVERY = "begin_recovery"
ACTION_VERIFY = "verify"
ACTION_ABORT = "abort"

#: Actions that create a new episode.
OPEN_ACTIONS: frozenset[str] = frozenset({ACTION_OPEN_FAILOVER, ACTION_OPEN_BREACH})


@dataclass(frozen=True)
class PathEvaluation:
    """The decision engine's verdict for this round, plus its provenance."""

    decision: dict[str, Any]
    paths: list[dict[str, Any]]
    selected_link: Any
    previous_selected_link: Any
    failed_link: Any
    unavailable: list[Any]
    #: Verbatim engine flag (true for unavailable paths AND degraded live ones).
    failover: bool
    #: Stricter, observable: a candidate path actually stopped being usable.
    path_failure: bool
    path_switched: bool


def evaluate_scope_paths(
    snapshot: ScopeSnapshot, previous_decision: dict[str, Any] | None,
) -> PathEvaluation:
    """Run the shared engine over the snapshot's real candidate links."""
    links = list(snapshot.links)
    decision = path_module.decide(links)
    selected = path_module.selected_link(decision, links)
    by_name = path_module.link_by_name(links)

    previous_name = (previous_decision or {}).get("selected_path")
    previous_selected = by_name.get(str(previous_name)) if previous_name else None
    unavailable = path_module.unavailable_links(links)

    failed = None
    if previous_selected is not None and not path_module.is_available(previous_selected):
        failed = previous_selected
    elif unavailable:
        failed = unavailable[0]
    elif previous_selected is not None:
        # Breach-only episode: nothing is down, so the "from" path is the one
        # the engine had been preferring.
        failed = previous_selected

    selected_name = decision.get("selected_path")
    path_switched = bool(
        previous_name is not None
        and selected_name is not None
        and str(previous_name) != str(selected_name)
    )
    path_failure = bool(unavailable) or decision.get("decision") == "NO_AVAILABLE_PATH"
    return PathEvaluation(
        decision=decision,
        paths=list(decision.get("paths") or []),
        selected_link=selected,
        previous_selected_link=previous_selected,
        failed_link=failed,
        unavailable=unavailable,
        failover=bool(decision.get("failover")),
        path_failure=path_failure,
        path_switched=path_switched,
    )


# ---------------------------------------------------------------------------
# Baseline diffs (route / BGP / link state)
# ---------------------------------------------------------------------------
#: Fields whose change is worth recording for each entity type.
ROUTE_DIFF_FIELDS: tuple[str, ...] = (
    "state", "is_best", "next_hop", "outgoing_interface", "metric",
    "administrative_distance",
)
BGP_DIFF_FIELDS: tuple[str, ...] = ("state", "admin_status", "prefixes_received")
LINK_DIFF_FIELDS: tuple[str, ...] = ("oper_status", "admin_status")


def _row_label(row: dict[str, Any], kind: str) -> str | None:
    if kind == "route":
        destination = row.get("destination")
        protocol = row.get("protocol")
        if destination:
            return f"{destination} ({protocol})" if protocol else str(destination)
    if kind == "bgp":
        return row.get("neighbor_ip")
    return row.get("name")


def _is_significant(row: dict[str, Any], kind: str) -> bool:
    """A brand-new / vanished row only matters when it is operationally live.

    The first round after a collector starts would otherwise emit one
    "appeared" transition per route in the table, drowning the timeline. A new
    BEST route or a new ESTABLISHED session is real news; a new backup route in
    the FIB is not.
    """
    if kind == "route":
        return bool(row.get("is_best"))
    if kind == "bgp":
        return str(row.get("state") or "").lower() in {"established", "up"}
    return False


def diff_rows(
    previous: dict[str, dict[str, Any]] | None,
    current: dict[str, dict[str, Any]],
    *,
    kind: str,
    fields: tuple[str, ...],
    at: datetime,
) -> list[dict[str, Any]]:
    """Field-level diff between two rounds; the captured failover timeline."""
    before_map = previous or {}
    if not before_map:
        # No baseline yet: nothing can honestly be called a *change*.
        return []
    stamp = at.isoformat()
    transitions: list[dict[str, Any]] = []
    for key in sorted(set(before_map) | set(current)):
        before = before_map.get(key)
        after = current.get(key)
        label = _row_label(after or before or {}, kind)
        if before is None and after is not None:
            if not _is_significant(after, kind):
                continue
            transitions.append(
                {
                    "key": key, "kind": kind, "field": "entry", "from": None,
                    "to": after.get("state") or "present", "label": label,
                    "at": stamp, "detail": after,
                }
            )
            continue
        if after is None and before is not None:
            if not _is_significant(before, kind):
                continue
            transitions.append(
                {
                    "key": key, "kind": kind, "field": "entry", "from": "present",
                    "to": None, "label": label, "at": stamp, "detail": before,
                }
            )
            continue
        if before is None or after is None:
            continue
        for name in fields:
            old = before.get(name)
            new = after.get(name)
            if old == new:
                continue
            transitions.append(
                {
                    "key": key, "kind": kind, "field": name, "from": old, "to": new,
                    "label": label, "at": stamp, "detail": after,
                }
            )
    return transitions


# ---------------------------------------------------------------------------
# Operational SLA breaches
# ---------------------------------------------------------------------------
def evaluate_breaches(
    snapshot: ScopeSnapshot,
    policies: Any,
    *,
    previous_levels: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], bool, int, dict[str, dict[str, str]]]:
    """Evaluate every configured policy against every candidate link.

    Returns ``(hits, any_critical, required_samples, next_levels)``. The
    ``next_levels`` map (link -> metric -> level) is what makes hysteresis real
    across rounds; it is persisted in the detector state's ``attributes``.
    """
    hits: list[dict[str, Any]] = []
    next_levels: dict[str, dict[str, str]] = {}
    required_candidates: list[int] = []
    prior = previous_levels or {}

    for link in snapshot.links:
        link_key = str(link.id)
        resolved = policy_module.resolve_policies(
            policies, link_id=link.id, site_id=snapshot.site_id
        )
        prior_for_link = prior.get(link_key) or {}
        current_for_link: dict[str, str] = {}
        for metric, policy in sorted(resolved.items()):
            value = policy_module.metric_value(link, metric)
            hit = policy_module.evaluate_policy(
                policy, value, previous_level=prior_for_link.get(metric)
            )
            if hit is None:
                continue
            current_for_link[metric] = hit.level
            if hit.level in policy_module.BREACH_LEVELS:
                payload = hit.to_dict()
                payload["link_id"] = link_key
                payload["link_name"] = getattr(link, "name", None)
                hits.append(payload)
                try:
                    required_candidates.append(
                        max(1, int(getattr(policy, "min_consecutive_samples", DEFAULT_MIN_SAMPLES)))
                    )
                except (TypeError, ValueError):
                    required_candidates.append(DEFAULT_MIN_SAMPLES)
        next_levels[link_key] = current_for_link

    required = (
        max(required_candidates) if required_candidates else DEFAULT_MIN_SAMPLES
    )
    critical = any(
        hit.get("level") == policy_module.LEVEL_CRITICAL for hit in hits
    )
    return hits, critical, required, next_levels


# ---------------------------------------------------------------------------
# The round
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoundResult:
    """Everything one collection round observed and decided (pure output)."""

    action: str
    reason: str
    baseline: bool
    decision: dict[str, Any]
    paths: list[dict[str, Any]]
    failed_link: Any
    selected_link: Any
    unavailable_labels: list[str] = field(default_factory=list)
    breaches: list[dict[str, Any]] = field(default_factory=list)
    critical: bool = False
    failover: bool = False
    path_failure: bool = False
    path_switched: bool = False
    route_transitions: list[dict[str, Any]] = field(default_factory=list)
    bgp_transitions: list[dict[str, Any]] = field(default_factory=list)
    link_transitions: list[dict[str, Any]] = field(default_factory=list)
    consecutive_breaches: int = 0
    consecutive_clear: int = 0
    required_samples: int = DEFAULT_MIN_SAMPLES
    levels: dict[str, dict[str, str]] = field(default_factory=dict)
    next_status: str | None = None
    link_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    bgp_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)


def analyze_round(
    snapshot: ScopeSnapshot,
    *,
    policies: Any = (),
    previous: Any = None,
    open_status: str | None = None,
    now: datetime | None = None,
) -> RoundResult:
    """One pure detection round over a loaded snapshot."""
    moment = now or datetime.now(timezone.utc)
    baseline = previous is None

    previous_decision = None if baseline else (previous.previous_decision or None)
    previous_routes = {} if baseline else (previous.route_snapshot or {})
    previous_bgp = {} if baseline else (previous.bgp_snapshot or {})
    previous_links = {} if baseline else (previous.link_snapshot or {})
    previous_levels = {} if baseline else ((previous.attributes or {}).get("levels") or {})
    prior_breaches = 0 if baseline else int(previous.consecutive_breaches or 0)
    prior_clear = 0 if baseline else int(previous.consecutive_clear or 0)

    evaluation = evaluate_scope_paths(snapshot, previous_decision)
    current_routes = snapshot.route_snapshot()
    current_bgp = snapshot.bgp_snapshot()
    current_links = snapshot.link_snapshot()

    route_transitions = diff_rows(
        previous_routes, current_routes, kind="route",
        fields=ROUTE_DIFF_FIELDS, at=moment,
    )
    bgp_transitions = diff_rows(
        previous_bgp, current_bgp, kind="bgp", fields=BGP_DIFF_FIELDS, at=moment
    )
    link_transitions = diff_rows(
        previous_links, current_links, kind="link", fields=LINK_DIFF_FIELDS, at=moment
    )

    breaches, critical, required, levels = evaluate_breaches(
        snapshot, policies, previous_levels=previous_levels
    )

    # Confirmation counters. Breaches need N consecutive critical rounds;
    # recovery needs N consecutive healthy ones. Both reset on any doubt.
    next_breaches = prior_breaches + 1 if critical else 0
    healthy = not evaluation.path_failure and not critical
    next_clear = prior_clear + 1 if healthy else 0

    action, reason, next_status = _resolve_action(
        baseline=baseline,
        open_status=open_status,
        path_failure=evaluation.path_failure,
        critical=critical,
        next_breaches=next_breaches,
        next_clear=next_clear,
        required=required,
    )

    return RoundResult(
        action=action,
        reason=reason,
        baseline=baseline,
        decision=evaluation.decision,
        paths=evaluation.paths,
        failed_link=evaluation.failed_link,
        selected_link=evaluation.selected_link,
        unavailable_labels=[
            getattr(link, "name", None) for link in evaluation.unavailable
        ],
        breaches=breaches,
        critical=critical,
        failover=evaluation.failover,
        path_failure=evaluation.path_failure,
        path_switched=evaluation.path_switched,
        route_transitions=route_transitions,
        bgp_transitions=bgp_transitions,
        link_transitions=link_transitions,
        consecutive_breaches=next_breaches,
        consecutive_clear=next_clear,
        required_samples=required,
        levels=levels,
        next_status=next_status,
        link_snapshot=current_links,
        route_snapshot=current_routes,
        bgp_snapshot=current_bgp,
    )


def _resolve_action(
    *,
    baseline: bool,
    open_status: str | None,
    path_failure: bool,
    critical: bool,
    next_breaches: int,
    next_clear: int,
    required: int,
) -> tuple[str, str, str | None]:
    """Resolve the FSM action for this round. Returns ``(action, reason, status)``.

    Kept separate from ``analyze_round`` so the transition table can be read (and
    tested) in one place.
    """
    if baseline:
        return (
            ACTION_BASELINE,
            "first observation for this scope: baseline captured, no transitions "
            "can be attributed yet",
            None,
        )

    confirmed_breach = next_breaches >= max(1, required)
    clear_confirmed = next_clear >= max(1, required)

    if open_status is None:
        if path_failure:
            return (
                ACTION_OPEN_FAILOVER,
                "candidate WAN path is no longer usable",
                STATUS_FAILED_OVER,
            )
        if critical and confirmed_breach:
            return (
                ACTION_OPEN_BREACH,
                f"critical operational SLA breach confirmed over {next_breaches} "
                f"consecutive round(s)",
                STATUS_DETECTED,
            )
        if critical:
            return (
                ACTION_NONE,
                f"critical breach observed ({next_breaches}/{max(1, required)} "
                f"consecutive rounds); not yet confirmed",
                None,
            )
        return (ACTION_NONE, "no observable failover condition", None)

    if open_status == STATUS_DETECTED:
        if path_failure:
            return (
                ACTION_ESCALATE,
                "the path decision shows traffic on a different path",
                STATUS_FAILED_OVER,
            )
        if clear_confirmed:
            return (
                ACTION_ABORT,
                "the breach cleared without a path switch: not a failover",
                STATUS_ABORTED,
            )
        return (ACTION_NONE, "breach still open, no path switch observed", None)

    if open_status == STATUS_FAILED_OVER:
        if path_failure:
            return (ACTION_NONE, "path still unusable", None)
        if clear_confirmed:
            return (
                ACTION_BEGIN_RECOVERY,
                f"recovery observed over {next_clear} consecutive round(s); "
                f"starting verification",
                STATUS_RECOVERING,
            )
        return (
            ACTION_NONE,
            f"healthy again but confirmation window not met ({next_clear}/{max(1, required)})",
            None,
        )

    if open_status == STATUS_RECOVERING:
        if clear_confirmed or not path_failure:
            return (
                ACTION_VERIFY,
                "verifying recovery against live telemetry",
                None,
            )
        return (ACTION_NONE, "recovery regressed before verification", None)

    return (ACTION_NONE, f"episode in terminal state {open_status!r}", None)


# ---------------------------------------------------------------------------
# Persistence-facing service
# ---------------------------------------------------------------------------
#: Cap on captured transitions per event. A pathological flapping episode must
#: not grow one JSONB array without bound; the oldest entries are dropped and
#: the trimming is recorded in the event's attributes.
TRANSITION_CAP = 200


class FailoverDetectorService:
    """One bounded, exception-isolated capture pass over a tenant's site edges.

    Nothing here can fail a collection run: the caller (the post-collection
    hook) wraps it, and ``run_for_tenant`` additionally isolates each scope so
    one bad site cannot stop the others.
    """

    #: Sites evaluated per run. A tenant with hundreds of sites gets its
    #: lowest-id slice each cycle rather than one enormous pass.
    MAX_SCOPES = 25

    def __init__(self, db, *, max_scopes: int | None = None, publish: bool = True):
        self.db = db
        self.max_scopes = max_scopes or self.MAX_SCOPES
        self.publish = publish
        self.policy_repo = OperationalSLAPolicyRepository(db)
        self.event_repo = FailoverEventRepository(db)
        self.state_repo = FailoverDetectorStateRepository(db)

    async def run_for_tenant(self, tenant_id) -> dict:
        report: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "scopes": 0,
            "evaluated": 0,
            "skipped": 0,
            "opened": 0,
            "advanced": 0,
            "recovered": 0,
            "errors": 0,
            "actions": {},
            "details": [],
        }
        scopes = await list_scopes(self.db, tenant_id)
        if not scopes:
            report["skipped_reason"] = "no devices discovered for this tenant"
            return report
        if len(scopes) > self.max_scopes:
            report["scopes_truncated"] = True
            scopes = scopes[: self.max_scopes]
        report["scopes"] = len(scopes)

        for site_id in scopes:
            try:
                detail = await self.run_for_scope(tenant_id, site_id)
            except Exception as exc:  # noqa: BLE001 - one bad scope must not stop the rest
                report["errors"] += 1
                logger.warning(
                    "Failover detection failed for tenant %s scope site=%s: %s",
                    tenant_id, site_id, exc, exc_info=True,
                )
                continue
            report["details"].append(detail)
            if detail.get("skipped"):
                report["skipped"] += 1
                continue
            report["evaluated"] += 1
            action = detail.get("action") or "none"
            report["actions"][action] = report["actions"].get(action, 0) + 1
            if detail.get("created"):
                report["opened"] += 1
            elif action in {
                ACTION_ESCALATE, ACTION_BEGIN_RECOVERY, ACTION_VERIFY,
            }:
                if action == ACTION_VERIFY and detail.get("status") == "recovered":
                    report["recovered"] += 1
                else:
                    report["advanced"] += 1
        return report

    async def run_for_scope(self, tenant_id, site_id) -> dict:
        """Detect on one site edge and persist whatever was observed."""
        now = datetime.now(timezone.utc)
        snapshot = await load_scope(self.db, tenant_id, site_id=site_id)
        if not snapshot.has_candidates:
            # Keep no stale baseline for a scope that no longer has WAN paths.
            existing = await self.state_repo.get(tenant_id, snapshot.scope_key)
            if existing is not None:
                await self.state_repo.delete(existing)
            return {
                "scope": snapshot.scope_key,
                "site_id": str(site_id) if site_id else None,
                "skipped": snapshot.skipped_reason or "no candidate WAN links",
            }

        policies = await self.policy_repo.list_active(tenant_id)
        previous = await self.state_repo.get(tenant_id, snapshot.scope_key)
        open_event = await self._resolve_open_event(tenant_id, snapshot, previous)

        result = analyze_round(
            snapshot,
            policies=policies,
            previous=previous,
            open_status=open_event.status if open_event is not None else None,
            now=now,
        )
        outcome = await self._apply_round(
            tenant_id, snapshot, result, previous, open_event, now=now
        )
        outcome.setdefault("scope", snapshot.scope_key)
        outcome["policies"] = len(policies)
        outcome["paths"] = len(snapshot.links)
        return outcome

    async def _resolve_open_event(self, tenant_id, snapshot, previous):
        """Restart-safe resume: the baseline's episode, else any open episode."""
        if previous is not None and previous.active_episode_id is not None:
            event = await self.event_repo.get(previous.active_episode_id, tenant_id)
            if event is not None and event.is_open:
                return event
        return await self.event_repo.find_open_for_scope(tenant_id, snapshot.scope_key)

    async def _apply_round(
        self, tenant_id, snapshot, result, previous, open_event, *, now,
    ) -> dict:
        """Persist whatever this round observed. Returns a small outcome dict."""
        action = result.action
        outcome: dict[str, Any] = {
            "action": action, "reason": result.reason,
            "event_id": None, "status": None,
        }

        if action == ACTION_BASELINE:
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=None, now=now,
            )
            return outcome

        if action in OPEN_ACTIONS:
            event, created = await self._open_event(
                tenant_id, snapshot, result, open_event, now=now
            )
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=event.id, now=now,
            )
            outcome.update(
                {
                    "event_id": str(event.id),
                    "status": event.status,
                    "created": created,
                    "sequence_no": event.sequence_no,
                }
            )
            if created:
                await self._publish(EventType.WAN_FAILOVER_DETECTED, tenant_id, event)
            return outcome

        if open_event is None:
            # Nothing open, nothing to open: just refresh the baseline.
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=None, now=now,
            )
            return outcome

        if action == ACTION_ESCALATE:
            blast = await self._blast_radius(tenant_id, result)
            event = await self.event_repo.apply_transition(
                open_event,
                status=STATUS_FAILED_OVER,
                at=now,
                reason=result.reason,
                append_routes=result.route_transitions,
                append_bgp=result.bgp_transitions,
                decision=result.decision,
                link_from_id=getattr(result.failed_link, "id", None),
                link_from_name=getattr(result.failed_link, "name", None),
                link_to_id=getattr(result.selected_link, "id", None),
                link_to_name=getattr(result.selected_link, "name", None),
                blast_radius=blast,
                evidence=self._evidence(snapshot, result, previous, blast, now=now),
                failed_over_at=now,
            )
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=event.id, now=now,
            )
            outcome.update({"event_id": str(event.id), "status": event.status})
            await self._publish(EventType.WAN_FAILOVER_DETECTED, tenant_id, event)
            return outcome

        if action == ACTION_ABORT:
            event = await self.event_repo.apply_transition(
                open_event,
                status=STATUS_ABORTED,
                at=now,
                reason=result.reason,
                append_routes=result.route_transitions,
                append_bgp=result.bgp_transitions,
                decision=result.decision,
            )
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=None, now=now,
            )
            outcome.update({"event_id": str(event.id), "status": event.status})
            return outcome

        if action == ACTION_BEGIN_RECOVERY:
            event = await self.event_repo.apply_transition(
                open_event,
                status=STATUS_RECOVERING,
                at=now,
                reason=result.reason,
                append_routes=result.route_transitions,
                append_bgp=result.bgp_transitions,
                decision=result.decision,
                recovering_at=now,
            )
            await self._save_state(
                tenant_id, snapshot, result, previous,
                active_episode_id=event.id, now=now,
            )
            outcome.update({"event_id": str(event.id), "status": event.status})
            return outcome

        if action == ACTION_VERIFY:
            return await self._verify_and_close(
                tenant_id, snapshot, result, previous, open_event, now=now
            )

        # ACTION_NONE with an open episode: keep capturing. Newly observed
        # transitions are appended so a long episode keeps its full timeline,
        # but the row is only touched when something actually changed.
        if result.route_transitions or result.bgp_transitions or result.link_transitions:
            event = await self.event_repo.update(
                open_event,
                decision=result.decision,
                route_transitions=self._append_capped(
                    open_event.route_transitions, result.route_transitions
                ),
                bgp_transitions=self._append_capped(
                    open_event.bgp_transitions, result.bgp_transitions
                ),
            )
            outcome.update({"event_id": str(event.id), "status": event.status})
        await self._save_state(
            tenant_id, snapshot, result, previous,
            active_episode_id=open_event.id, now=now,
        )
        return outcome

    # ------------------------------------------------------------------
    # Episode helpers
    # ------------------------------------------------------------------
    async def _open_event(self, tenant_id, snapshot, result, open_event, *, now):
        """Create the episode (or extend the one already open for this key)."""
        failed = result.failed_link
        dedup = dedup_key_for(
            snapshot.scope_key, snapshot.edge_device_id, getattr(failed, "id", None)
        )
        status = result.next_status or STATUS_DETECTED
        blast = await self._blast_radius(tenant_id, result)
        evidence = self._evidence(snapshot, result, None, blast, now=now)
        trigger = {
            "reason": result.reason,
            "path_failure": result.path_failure,
            "path_switched": result.path_switched,
            "engine_failover_flag": result.failover,
            "critical": result.critical,
            "required_samples": result.required_samples,
            "consecutive_breaches": result.consecutive_breaches,
            "breaches": result.breaches,
            "unavailable_paths": result.unavailable_labels,
            "duplicate_link_names": snapshot.duplicate_names,
        }

        existing = open_event
        if existing is None:
            existing = await self.event_repo.find_open(tenant_id, dedup)
        if existing is not None:
            # Resume rather than duplicate: the partial unique index would
            # reject a second open row for this key anyway.
            fields: dict[str, Any] = {
                "decision": result.decision,
                "trigger": trigger,
                "route_transitions": self._append_capped(
                    existing.route_transitions, result.route_transitions
                ),
                "bgp_transitions": self._append_capped(
                    existing.bgp_transitions, result.bgp_transitions
                ),
                "blast_radius": blast,
                "evidence": evidence,
            }
            if status == STATUS_FAILED_OVER and existing.status == STATUS_DETECTED:
                return (
                    await self.event_repo.apply_transition(
                        existing, status=STATUS_FAILED_OVER, at=now,
                        reason=result.reason, failed_over_at=now,
                        link_to_id=getattr(result.selected_link, "id", None),
                        link_to_name=getattr(result.selected_link, "name", None),
                        **fields,
                    ),
                    False,
                )
            return await self.event_repo.update(existing, **fields), False

        sequence = await self.event_repo.next_sequence(tenant_id, snapshot.scope_key)
        event = await self.event_repo.create(
            tenant_id,
            scope_key=snapshot.scope_key,
            dedup_key=dedup,
            status=status,
            detected_at=now,
            sequence_no=sequence,
            site_id=snapshot.site_id,
            edge_device_id=snapshot.edge_device_id,
            trigger=trigger,
            decision=result.decision,
            link_from_id=getattr(failed, "id", None),
            link_from_name=getattr(failed, "name", None),
            link_to_id=getattr(result.selected_link, "id", None),
            link_to_name=getattr(result.selected_link, "name", None),
            route_transitions=result.route_transitions,
            bgp_transitions=result.bgp_transitions,
            blast_radius=blast,
            evidence=evidence,
            failed_over_at=now if status == STATUS_FAILED_OVER else None,
            attributes={
                "detector": {
                    "required_samples": result.required_samples,
                    "path_failure": result.path_failure,
                    "path_switched": result.path_switched,
                },
                "duplicate_link_names": snapshot.duplicate_names,
                "links_truncated": snapshot.links_truncated,
            },
            transition_reason=result.reason,
        )
        return event, True

    async def _verify_and_close(
        self, tenant_id, snapshot, result, previous, open_event, *, now,
    ) -> dict:
        """Run telemetry-only verification and move the episode to a terminal state."""
        from app.services.failover_intelligence.verify import (
            RecoveryVerificationService,
        )

        verification = await RecoveryVerificationService(self.db).verify_event(
            tenant_id, open_event, snapshot=snapshot, now=now
        )
        passed = verification.get("status") == "pass"
        link = snapshot.link_by_id(open_event.link_from_id)
        blast = await self._blast_radius(tenant_id, result, link=link) or (
            open_event.blast_radius or {}
        )
        status = STATUS_RECOVERED if passed else STATUS_VERIFICATION_FAILED
        event = await self.event_repo.apply_transition(
            open_event,
            status=status,
            at=now,
            reason=verification.get("summary") or result.reason,
            append_routes=result.route_transitions,
            append_bgp=result.bgp_transitions,
            decision=result.decision,
            verification=verification,
            blast_radius=blast,
            evidence=self._evidence(snapshot, result, previous, blast, now=now),
            recovered_at=now if passed else None,
        )
        await self._save_state(
            tenant_id, snapshot, result, previous,
            active_episode_id=None, now=now,
        )
        await self._publish(
            EventType.WAN_FAILOVER_RECOVERED
            if passed
            else EventType.WAN_FAILOVER_VERIFICATION_FAILED,
            tenant_id,
            event,
        )
        return {
            "action": result.action,
            "reason": result.reason,
            "event_id": str(event.id),
            "status": event.status,
            "verification": verification.get("status"),
        }

    # ------------------------------------------------------------------
    # Supporting analysis
    # ------------------------------------------------------------------
    async def _blast_radius(self, tenant_id, result, *, link=None) -> dict:
        target = link if link is not None else result.failed_link
        if target is None:
            # No identified failing path: reporting an empty blast radius is
            # honest; seeding from an arbitrary resource would not be.
            return {}
        from app.services.failover_intelligence.blast_radius import BlastRadiusService

        return await BlastRadiusService(self.db).compute_for_link(tenant_id, target)

    def _evidence(self, snapshot, result, previous, blast, *, now) -> list[dict]:
        return evidence_module.collect(
            link_from=result.failed_link,
            link_to=result.selected_link,
            candidate_links=snapshot.links,
            decision=result.decision,
            previous_decision=(
                previous.previous_decision if previous is not None else None
            ),
            breaches=result.breaches,
            route_transitions=result.route_transitions,
            bgp_transitions=result.bgp_transitions,
            link_transitions=result.link_transitions,
            blast_radius=blast,
            duplicate_names=snapshot.duplicate_names,
            now=now,
        )

    @staticmethod
    def _append_capped(existing, new, cap: int = TRANSITION_CAP) -> list:
        merged = list(existing or []) + list(new or [])
        return merged[-cap:] if len(merged) > cap else merged

    async def _save_state(
        self, tenant_id, snapshot, result, previous, *, active_episode_id, now,
    ) -> None:
        last_breach_at = previous.last_breach_at if previous is not None else None
        if result.critical:
            last_breach_at = now
        await self.state_repo.upsert(
            tenant_id,
            snapshot.scope_key,
            site_id=snapshot.site_id,
            edge_device_id=snapshot.edge_device_id,
            active_episode_id=active_episode_id,
            consecutive_breaches=result.consecutive_breaches,
            consecutive_clear=result.consecutive_clear,
            last_breach_at=last_breach_at,
            previous_decision=result.decision,
            link_snapshot=result.link_snapshot,
            route_snapshot=result.route_snapshot,
            bgp_snapshot=result.bgp_snapshot,
            last_evaluated_at=now,
            attributes={
                "levels": result.levels,
                "duplicate_names": snapshot.duplicate_names,
                "links_truncated": snapshot.links_truncated,
            },
        )

    async def _publish(self, event_type, tenant_id, event) -> None:
        """Best-effort notification; never allowed to break detection."""
        if not self.publish:
            return
        try:
            from app.services.events.event_bus import DomainEvent, get_event_bus

            await get_event_bus().publish(
                DomainEvent(
                    event_type=event_type,
                    tenant_id=tenant_id,
                    payload={
                        "failover_event_id": str(event.id),
                        "sequence_no": event.sequence_no,
                        "status": event.status,
                        "site_id": str(event.site_id) if event.site_id else None,
                        "edge_device_id": (
                            str(event.edge_device_id) if event.edge_device_id else None
                        ),
                        "link_from": event.link_from_name,
                        "link_to": event.link_to_name,
                        "detected_at": (
                            event.detected_at.isoformat() if event.detected_at else None
                        ),
                    },
                )
            )
        except Exception:  # noqa: BLE001 - notification is not the product
            logger.debug("Failover event publish failed", exc_info=True)

    # ------------------------------------------------------------------
    # Read-only dry run (backing the API's evaluate endpoint)
    # ------------------------------------------------------------------
    async def dry_run_scope(self, tenant_id, site_id) -> dict:
        """Analyse one scope WITHOUT writing anything.

        Used by the manual evaluate endpoint and by tests: it shares the exact
        pure round the detector runs, so a dry run cannot drift from production
        behaviour.
        """
        now = datetime.now(timezone.utc)
        snapshot = await load_scope(self.db, tenant_id, site_id=site_id)
        policies = await self.policy_repo.list_active(tenant_id)
        previous = await self.state_repo.get(tenant_id, snapshot.scope_key)
        open_event = await self._resolve_open_event(tenant_id, snapshot, previous)
        result = analyze_round(
            snapshot,
            policies=policies,
            previous=previous,
            open_status=open_event.status if open_event is not None else None,
            now=now,
        )
        return {
            "scope": snapshot.scope_key,
            "site_id": str(site_id) if site_id else None,
            "skipped": snapshot.skipped_reason,
            "evaluated_at": now.isoformat(),
            "policies": len(policies),
            "candidate_paths": [
                {
                    "link_id": str(link.id),
                    "name": link.name,
                    "available": path_module.is_available(link),
                }
                for link in snapshot.links
            ],
            "duplicate_link_names": snapshot.duplicate_names,
            "decision": result.decision,
            "action": result.action,
            "action_reason": result.reason,
            "next_status": result.next_status,
            "path_failure": result.path_failure,
            "path_switched": result.path_switched,
            "engine_failover_flag": result.failover,
            "critical": result.critical,
            "required_samples": result.required_samples,
            "consecutive_breaches": result.consecutive_breaches,
            "consecutive_clear": result.consecutive_clear,
            "breaches": result.breaches,
            "link_from": getattr(result.failed_link, "name", None),
            "link_to": getattr(result.selected_link, "name", None),
            "route_transitions": result.route_transitions,
            "bgp_transitions": result.bgp_transitions,
            "link_transitions": result.link_transitions,
            "open_event_id": str(open_event.id) if open_event is not None else None,
            "open_event_status": open_event.status if open_event is not None else None,
        }
