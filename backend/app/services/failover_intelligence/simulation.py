"""
Counterfactual simulation — "what if this circuit failed?" answered without
touching anything.

Strictly read-only: no writes, no events, no incident. It builds a virtual copy
of the site edge's current candidate paths, applies the requested perturbation
in memory, and re-runs the SAME decision engine (``sdwan.evaluate_paths``) that
production uses. Two consequences worth stating plainly:

* the simulated answer is exactly as good as the engine's answer, because it IS
  the engine — there is no second model to drift out of sync, and
* nothing here observes the future. A simulation says "with these inputs the
  current scoring would pick X". It is a counterfactual over the snapshot, not a
  prediction, and the report labels it as such.

Perturbation shape::

    {
      "links": {
        "<link name or id>": {"available": false, "latency_ms": 250,
                              "packet_loss_pct": 12}
      },
      "scale": {"latency_ms": 1.5, "packet_loss_pct": 2.0},   # multipliers
      "weights": {"latency": 0.5, "packet_loss": 0.5}         # engine weights
    }

Targets that match no candidate path are reported back in
``unresolved_targets`` instead of being silently ignored — a simulation against
a typo'd circuit name would otherwise "prove" that nothing would happen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.failover_intelligence import paths as path_module
from app.services.network_intelligence.sdwan import evaluate_paths

#: Numeric fields a perturbation may set or scale on a path.
NUMERIC_FIELDS: tuple[str, ...] = (
    "latency_ms", "jitter_ms", "packet_loss_pct", "bandwidth_mbps", "utilization_pct",
)


@dataclass
class SimulationReport:
    baseline: dict[str, Any]
    simulated: dict[str, Any]
    changed: bool
    delta: dict[str, Any] = field(default_factory=dict)
    unresolved_targets: list[str] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    method: str = "counterfactual (read-only replay of sdwan.evaluate_paths)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "simulated": self.simulated,
            "changed": self.changed,
            "delta": self.delta,
            "unresolved_targets": self.unresolved_targets,
            "applied": self.applied,
            "weights": self.weights,
            "method": self.method,
        }


def _matches(target: str, payload: dict[str, Any]) -> bool:
    return target in (str(payload.get("name") or ""), str(payload.get("link_id") or ""))


def build_virtual_paths(
    links: Any, perturbation: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """``(virtual_payloads, applied, unresolved_targets)`` — no side effects."""
    payloads: list[dict[str, Any]] = [path_module.link_payload(link) for link in links]
    for payload, link in zip(payloads, links):
        payload["link_id"] = str(getattr(link, "id", ""))
    perturbation = perturbation or {}

    scale = perturbation.get("scale") or {}
    for payload in payloads:
        for key, factor in scale.items():
            if key not in NUMERIC_FIELDS:
                continue
            try:
                multiplier = float(factor)
            except (TypeError, ValueError):
                continue
            current = payload.get(key)
            if current is None:
                continue
            payload[key] = max(0.0, float(current) * multiplier)

    applied: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for target, changes in (perturbation.get("links") or {}).items():
        matched = False
        for payload in payloads:
            if not _matches(str(target), payload):
                continue
            matched = True
            for key, value in (changes or {}).items():
                if key == "available":
                    payload["available"] = bool(value)
                elif key in NUMERIC_FIELDS:
                    try:
                        payload[key] = float(value)
                    except (TypeError, ValueError):
                        continue
            applied.append({"target": str(target), "changes": dict(changes or {})})
        if not matched:
            unresolved.append(str(target))
    return payloads, applied, unresolved


def _summary(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_path": decision.get("selected_path"),
        "score": decision.get("score"),
        "decision": decision.get("decision"),
        "failover": decision.get("failover"),
        "reason": decision.get("reason"),
        "paths": [
            {
                "name": path.get("name"),
                "score": path.get("score"),
                "health": path.get("health"),
                "available": path.get("available"),
            }
            for path in (decision.get("paths") or [])
        ],
    }


def simulate(
    links: Any,
    perturbation: dict[str, Any] | None = None,
    *,
    baseline_decision: dict[str, Any] | None = None,
) -> SimulationReport:
    """Run the counterfactual from a live link set (pure)."""
    perturbation = perturbation or {}
    baseline = baseline_decision or evaluate_paths(
        [path_module.to_wan_path(link) for link in links]
    )
    payloads, applied, unresolved = build_virtual_paths(links, perturbation)
    weights = perturbation.get("weights") or None
    simulated = evaluate_paths(payloads, weights)

    before = baseline.get("selected_path")
    after = simulated.get("selected_path")
    before_paths = {
        path.get("name") for path in (baseline.get("paths") or []) if path.get("available")
    }
    after_paths = {
        path.get("name") for path in (simulated.get("paths") or []) if path.get("available")
    }

    changed = (
        str(before) != str(after)
        or bool(baseline.get("failover")) != bool(simulated.get("failover"))
        or before_paths != after_paths
    )
    delta = {
        "selected_path": {"before": before, "after": after},
        "score": {"before": baseline.get("score"), "after": simulated.get("score")},
        "failover": {
            "before": bool(baseline.get("failover")),
            "after": bool(simulated.get("failover")),
        },
        "paths_lost": sorted(before_paths - after_paths),
        "paths_gained": sorted(after_paths - before_paths),
        "path_switched": bool(before and after and str(before) != str(after)),
        "no_path_left": after is None,
    }
    return SimulationReport(
        baseline=_summary(baseline),
        simulated=_summary(simulated),
        changed=changed,
        delta=delta,
        unresolved_targets=unresolved,
        applied=applied,
        weights={
            str(key): float(value)
            for key, value in (weights or {}).items()
            if isinstance(value, (int, float))
        },
    )


# ---------------------------------------------------------------------------
# Persistence-facing wrapper
# ---------------------------------------------------------------------------
class CounterfactualSimulator:
    """Read-only: loads the live snapshot and runs the counterfactual on it.

    There is intentionally no method that persists or publishes anything — the
    whole point of a what-if is that it leaves no trace in the operational
    record.
    """

    #: Bounded: at most this many "lost path" blast radii are computed.
    MAX_IMPACT_SEEDS = 3

    def __init__(self, db) -> None:
        self.db = db

    async def simulate_for_site(
        self, tenant_id, site_id, perturbation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from datetime import datetime, timezone

        from app.services.failover_intelligence.loader import load_scope

        snapshot = await load_scope(self.db, tenant_id, site_id=site_id)
        base = {
            "scope": snapshot.scope_key,
            "site_id": str(site_id) if site_id else None,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_paths": [
                {
                    "link_id": str(link.id),
                    "name": link.name,
                    "available": path_module.is_available(link),
                    "link_type": getattr(link, "link_type", None),
                    "redundancy_group": getattr(link, "redundancy_group", None),
                }
                for link in snapshot.links
            ],
            "note": (
                "read-only counterfactual over the live snapshot; the current "
                "network state was not changed"
            ),
        }
        if not snapshot.has_candidates:
            base["skipped"] = snapshot.skipped_reason or "no candidate WAN paths"
            return base

        report = simulate(snapshot.links, perturbation).to_dict()
        base.update(report)

        lost = [
            link
            for link in snapshot.links
            if getattr(link, "name", None) in (report["delta"] or {}).get("paths_lost", [])
        ]
        if lost:
            from app.services.failover_intelligence.blast_radius import (
                BlastRadiusService,
            )

            service = BlastRadiusService(self.db)
            base["impact_if_scenario_holds"] = [
                (await service.compute_for_link(tenant_id, link))["total"]
                for link in lost[: self.MAX_IMPACT_SEEDS]
            ]
        return base
