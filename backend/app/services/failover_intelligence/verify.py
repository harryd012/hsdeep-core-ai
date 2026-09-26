"""
Recovery verification — did the network ACTUALLY recover, or does it only look
like it?

The distinction matters because "the path decision stopped reporting a failover"
is a *policy* statement, while "the circuits are up, the sessions are
established and the routes exist" is a *telemetry* statement. This module only
ever asserts the second one, and it is explicit when it cannot:

* ``pass``      - every core check was satisfied from live telemetry
* ``fail``      - at least one core check was contradicted
* ``unknown``   - not contradicted, but not confirmable either (a collector that
                  does not gather BGP, a missing timestamp). Reporting success
                  here would be a lie by omission, so it is reported as unknown.

Checks are deliberately few and each names the source it read. Nothing is
inferred from the incident's own narrative: an episode cannot "verify itself".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.failover_intelligence import paths as path_module
from app.services.failover_intelligence import policies as policy_module
from app.services.failover_intelligence.detector import evaluate_breaches
from app.services.failover_intelligence.loader import load_scope

CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_UNKNOWN = "unknown"

OVERALL_PASS = "pass"
OVERALL_FAIL = "fail"
OVERALL_UNKNOWN = "unknown"

#: Checks that must ALL pass for an overall ``pass``. The rest are informative.
CORE_CHECKS: tuple[str, ...] = (
    "selected_path_available",
    "decision_not_failing_over",
    "no_critical_breach",
    "confirmation_window",
)

#: A link whose telemetry has not been refreshed for this long cannot be used to
#: assert recovery (it may simply not be collected any more).
DEFAULT_STALENESS_SECONDS = 3600

ESTABLISHED_STATES: frozenset[str] = frozenset({"established", "up"})


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    status: str
    detail: str
    source: str
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "source": self.source,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class VerificationResult:
    status: str
    summary: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    checked_at: str | None = None
    method: str = "telemetry"

    def to_dict(self) -> dict[str, Any]:
        counts = {CHECK_PASS: 0, CHECK_FAIL: 0, CHECK_UNKNOWN: 0}
        for check in self.checks:
            counts[check.get("status", CHECK_UNKNOWN)] = (
                counts.get(check.get("status", CHECK_UNKNOWN), 0) + 1
            )
        return {
            "status": self.status,
            "summary": self.summary,
            "method": self.method,
            "checked_at": self.checked_at,
            "counts": counts,
            "checks": self.checks,
        }


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def verify_recovery(
    *,
    link_to: Any,
    candidate_links: list[Any],
    decision: dict[str, Any],
    breaches: list[dict[str, Any]],
    bgp: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    consecutive_clear: int = 0,
    required_samples: int = 2,
    stale_after_seconds: int = DEFAULT_STALENESS_SECONDS,
    now: datetime | None = None,
) -> VerificationResult:
    """Evaluate the recovery checks from live telemetry (pure)."""
    moment = now or datetime.now(timezone.utc)
    checks: list[VerificationCheck] = []
    links = list(candidate_links or ())
    unavailable = [link for link in links if not path_module.is_available(link)]
    required = max(1, int(required_samples or 1))

    # 1. Is the replacement path usable?
    if link_to is None:
        checks.append(
            VerificationCheck(
                name="selected_path_available",
                status=(
                    CHECK_FAIL if not decision.get("selected_path") else CHECK_UNKNOWN
                ),
                detail=(
                    "the decision selected no path at all"
                    if not decision.get("selected_path")
                    else f"decision selected {decision.get('selected_path')!r} but it "
                    f"could not be resolved to a topology link"
                ),
                source="sdwan.evaluate_paths + topology_links",
                observed=decision.get("selected_path"),
            )
        )
    else:
        available = path_module.is_available(link_to)
        checks.append(
            VerificationCheck(
                name="selected_path_available",
                status=CHECK_PASS if available else CHECK_FAIL,
                detail=(
                    f"{getattr(link_to, 'name', 'path')} is usable "
                    f"(oper={getattr(link_to, 'oper_status', None)}, "
                    f"admin={getattr(link_to, 'admin_status', None)})"
                    if available
                    else f"{getattr(link_to, 'name', 'path')} is not usable"
                ),
                source="topology_links.oper_status/admin_status",
                observed={
                    "oper_status": getattr(link_to, "oper_status", None),
                    "admin_status": getattr(link_to, "admin_status", None),
                },
            )
        )

    # 2. Does the decision engine still report a failover condition?
    decision_value = decision.get("decision")
    if not unavailable and decision_value == "FORWARD":
        status = CHECK_PASS
        detail = "the decision engine reports a best path with every candidate usable"
    else:
        status = CHECK_FAIL
        detail = (
            f"decision={decision_value}, unusable paths="
            f"{[getattr(link, 'name', None) for link in unavailable]}"
        )
    checks.append(
        VerificationCheck(
            name="decision_not_failing_over",
            status=status,
            detail=detail,
            source="sdwan.evaluate_paths (existing engine)",
            observed={
                "decision": decision_value,
                "selected_path": decision.get("selected_path"),
                "score": decision.get("score"),
                "failover": decision.get("failover"),
            },
        )
    )

    # 3. Any operational SLA policy still in critical breach?
    critical = [
        b for b in (breaches or ()) if b.get("level") == policy_module.LEVEL_CRITICAL
    ]
    checks.append(
        VerificationCheck(
            name="no_critical_breach",
            status=CHECK_FAIL if critical else CHECK_PASS,
            detail=(
                "critical breach still active on "
                + ", ".join(
                    f"{b.get('link_name')}:{b.get('metric')}={b.get('observed')}"
                    for b in critical
                )
                if critical
                else "no operational SLA policy reports a critical breach"
            ),
            source="operational_sla_policies x topology_links telemetry",
            observed=[
                {
                    "metric": b.get("metric"),
                    "observed": b.get("observed"),
                    "threshold": b.get("threshold"),
                }
                for b in critical
            ],
        )
    )

    # 4. Confirmation window satisfied?
    checks.append(
        VerificationCheck(
            name="confirmation_window",
            status=CHECK_PASS if consecutive_clear >= required else CHECK_FAIL,
            detail=(
                f"{consecutive_clear} consecutive healthy round(s) "
                f"(required {required})"
            ),
            source="failover_detector_state.consecutive_clear",
            observed={"consecutive_clear": consecutive_clear, "required": required},
        )
    )

    # 5. BGP sessions (informative; a collector may not gather BGP at all)
    sessions = list(bgp or ())
    if not sessions:
        checks.append(
            VerificationCheck(
                name="bgp_sessions_established",
                status=CHECK_UNKNOWN,
                detail="no BGP sessions are collected for this edge",
                source="bgp_neighbors",
                observed=0,
            )
        )
    else:
        bad = [
            s for s in sessions
            if str(s.get("state") or "").lower() not in ESTABLISHED_STATES
        ]
        checks.append(
            VerificationCheck(
                name="bgp_sessions_established",
                status=CHECK_FAIL if bad else CHECK_PASS,
                detail=f"{len(sessions) - len(bad)}/{len(sessions)} sessions Established",
                source="bgp_neighbors.state",
                observed=[s.get("neighbor_ip") for s in bad],
            )
        )

    # 6. Routes (informative, same reasoning as BGP)
    route_rows = list(routes or ())
    if not route_rows:
        checks.append(
            VerificationCheck(
                name="best_routes_present",
                status=CHECK_UNKNOWN,
                detail="no routes are collected for this edge",
                source="network_routes",
                observed=0,
            )
        )
    else:
        best = [r for r in route_rows if r.get("is_best")]
        checks.append(
            VerificationCheck(
                name="best_routes_present",
                status=CHECK_PASS if best else CHECK_FAIL,
                detail=f"{len(best)} of {len(route_rows)} route(s) marked best",
                source="network_routes.is_best",
                observed=len(best),
            )
        )

    # 7. Telemetry freshness (an old snapshot cannot assert recovery)
    freshness = None
    if link_to is not None:
        freshness = getattr(link_to, "updated_at", None) or getattr(
            link_to, "last_state_change_at", None
        )
    stamp = _parse(freshness)
    if stamp is None:
        checks.append(
            VerificationCheck(
                name="telemetry_fresh",
                status=CHECK_UNKNOWN,
                detail="no telemetry timestamp available for the selected path",
                source="topology_links.updated_at",
                observed=None,
            )
        )
    else:
        age = (moment - stamp).total_seconds()
        fresh = age <= max(1, int(stale_after_seconds))
        checks.append(
            VerificationCheck(
                name="telemetry_fresh",
                status=CHECK_PASS if fresh else CHECK_UNKNOWN,
                detail=(
                    f"last telemetry for the selected path is {int(age)}s old"
                    + ("" if fresh else " (stale: recovery cannot be asserted)")
                ),
                source="topology_links.updated_at",
                observed={"age_seconds": int(age)},
            )
        )

    # 8. Redundancy depth restored
    available_count = len(links) - len(unavailable)
    if available_count >= 2:
        redundancy = CHECK_PASS
        redundancy_detail = f"{available_count} usable WAN paths (redundancy restored)"
    elif available_count == 1:
        redundancy = CHECK_UNKNOWN
        redundancy_detail = "only one usable WAN path; no redundancy left to verify"
    else:
        redundancy = CHECK_FAIL
        redundancy_detail = "no usable WAN path"
    checks.append(
        VerificationCheck(
            name="redundancy_restored",
            status=redundancy,
            detail=redundancy_detail,
            source="topology_links (candidate WAN paths)",
            observed={
                "usable": available_count,
                "total": len(links),
                "unusable": [getattr(link, "name", None) for link in unavailable],
            },
        )
    )

    core = [c for c in checks if c.name in CORE_CHECKS]
    if any(c.status == CHECK_FAIL for c in core):
        overall = OVERALL_FAIL
        summary = "recovery NOT verified: " + "; ".join(
            c.detail for c in core if c.status == CHECK_FAIL
        )
    elif any(c.status == CHECK_UNKNOWN for c in core):
        overall = OVERALL_UNKNOWN
        summary = "recovery could not be confirmed from available telemetry"
    elif core:
        overall = OVERALL_PASS
        summary = "recovery verified from live telemetry: " + "; ".join(
            c.detail for c in core if c.status == CHECK_PASS
        )
    else:
        overall = OVERALL_UNKNOWN
        summary = "no verification checks could be evaluated"

    return VerificationResult(
        status=overall,
        summary=summary,
        checks=[check.to_dict() for check in checks],
        checked_at=moment.isoformat(),
    )


# ---------------------------------------------------------------------------
# Persistence-facing wrapper
# ---------------------------------------------------------------------------
class RecoveryVerificationService:
    """Runs the pure checks against live state and (optionally) records them.

    ``persist=False`` is used by the detector, which writes the verification as
    part of its own FSM transition. ``persist=True`` backs the manual
    verification endpoint, so an operator-triggered check becomes part of the
    episode's evidence trail with its trigger recorded.
    """

    def __init__(self, db) -> None:
        from app.repositories.failover_detector_state_repository import (
            FailoverDetectorStateRepository,
        )
        from app.repositories.failover_event_repository import FailoverEventRepository
        from app.repositories.operational_sla_policy_repository import (
            OperationalSLAPolicyRepository,
        )

        self.db = db
        self.event_repo = FailoverEventRepository(db)
        self.policy_repo = OperationalSLAPolicyRepository(db)
        self.state_repo = FailoverDetectorStateRepository(db)

    async def verify_event(
        self,
        tenant_id,
        event,
        *,
        snapshot=None,
        consecutive_clear: int | None = None,
        required_samples: int | None = None,
        persist: bool = False,
        trigger: str = "detector",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        if snapshot is None:
            snapshot = await load_scope(self.db, tenant_id, site_id=event.site_id)

        policies = await self.policy_repo.list_active(tenant_id)
        decision = path_module.decide(snapshot.links)
        breaches, _critical, policy_required, _levels = evaluate_breaches(
            snapshot, policies
        )

        link_to = None
        if getattr(event, "link_to_id", None) is not None:
            link_to = snapshot.link_by_id(event.link_to_id)
        if link_to is None:
            link_to = path_module.selected_link(decision, snapshot.links)

        need_state = consecutive_clear is None or required_samples is None
        state = (
            await self.state_repo.get(tenant_id, snapshot.scope_key)
            if need_state
            else None
        )
        if consecutive_clear is None:
            consecutive_clear = int(getattr(state, "consecutive_clear", 0) or 0)
        if required_samples is None:
            required_samples = (
                ((event.attributes or {}).get("detector") or {}).get("required_samples")
                or policy_required
            )

        result = verify_recovery(
            link_to=link_to,
            candidate_links=snapshot.links,
            decision=decision,
            breaches=breaches,
            bgp=snapshot.bgp,
            routes=snapshot.routes,
            consecutive_clear=consecutive_clear,
            required_samples=required_samples,
            now=moment,
        ).to_dict()
        result["event_id"] = str(event.id)
        result["link_to"] = getattr(link_to, "name", None)
        result["required_samples"] = int(required_samples or 1)
        result["trigger"] = trigger

        if persist:
            attributes = dict(event.attributes or {})
            attributes["last_verification_at"] = moment.isoformat()
            attributes["last_verification_trigger"] = trigger
            await self.event_repo.update(
                event, verification=result, attributes=attributes
            )
        return result

    async def verify_by_id(
        self, tenant_id, event_id, *, trigger: str = "manual", persist: bool = True,
    ) -> dict[str, Any] | None:
        """Manual verification entry point for the API (None if not visible)."""
        event = await self.event_repo.get(event_id, tenant_id)
        if event is None:
            return None
        return await self.verify_event(
            tenant_id, event, persist=persist, trigger=trigger
        )
