"""
Evidence — the facts behind an explanation, each traceable to real telemetry.

RCA output is only as good as the provenance behind it. Every item this module
produces states three things separately:

* **what was observed** (a value, a transition, or its absence),
* **which stored source it came from** (a link row, a captured route
  transition, a BGP session, the decision engine's own output),
* **how it bears on the hypothesis** — ``supports``, ``neutral`` or
  ``contradicts`` — with a confidence the caller can sort by.

``neutral`` and ``contradicts`` are first-class outcomes, not failures. A route
transition list that is empty because the collector does not gather routes is
*not* evidence that routing was fine; it is an absence of evidence, and saying
so is the honest answer. Likewise, if the replacement path is also down, that
contradicts the "recovered" reading and must be reported as such.

The hypothesis this module explains is deliberately narrow and observable:

    "the path <link_from> stopped carrying traffic and the decision moved to
    <link_to>"

Nothing here speculates about vendor bugs, config drift or upstream carriers -
those would need evidence this product does not collect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.services.failover_intelligence.paths import is_available
from app.services.failover_intelligence.policies import (
    LEVEL_CRITICAL,
    metric_value,
    to_number,
)
# One shared vocabulary with the universal-infrastructure layer, so failover
# evidence and resource evidence cannot drift apart.
from app.services.infrastructure_intelligence import vocabulary as V

FINDING_SUPPORTS = "supports"
FINDING_NEUTRAL = "neutral"
FINDING_CONTRADICTS = "contradicts"

FINDING_ORDER: tuple[str, ...] = (
    FINDING_SUPPORTS, FINDING_NEUTRAL, FINDING_CONTRADICTS,
)

# ---------------------------------------------------------------------------
# Provenance (shared Phase 7 vocabulary)
#
# Provenance answers "where did this come from?" and is a DIFFERENT axis from
# `finding` ("how does it bear on the hypothesis?"). An OBSERVED item can be
# neutral or even contradicting; the two are reported separately and never
# collapsed.
#
# The vocabulary strings come from the universal-infrastructure vocabulary so the
# failover evidence list, canonical telemetry and discovery evidence all speak one
# language. `captured` in the replay timeline is the same thing as `observed`
# here - the replay keeps its own token for backward compatibility and exposes the
# shared one alongside it.
# ---------------------------------------------------------------------------
PROVENANCE_OBSERVED = V.EVIDENCE_OBSERVED
PROVENANCE_DERIVED = V.EVIDENCE_DERIVED
PROVENANCE_CONTEXT = V.EVIDENCE_CONTEXT

PROVENANCE_STATES: tuple[str, ...] = V.EVIDENCE_PROVENANCE_STATES

#: Provenance per evidence kind. Unlisted kinds fall back to `derived`: the
#: failure mode of claiming something is observed when it is not is far worse
#: than the reverse.
PROVENANCE_BY_KIND: dict[str, str] = {
    "link_from_state": PROVENANCE_OBSERVED,
    "link_to_state": PROVENANCE_OBSERVED,
    "impairment_measured": PROVENANCE_OBSERVED,
    "route_transition": PROVENANCE_OBSERVED,
    "bgp_transition": PROVENANCE_OBSERVED,
    "link_transition": PROVENANCE_OBSERVED,
    "threshold_breach": PROVENANCE_DERIVED,
    "redundancy_depth": PROVENANCE_DERIVED,
    "blast_radius": PROVENANCE_DERIVED,
    "decision_engine_output": PROVENANCE_DERIVED,
    "data_quality": PROVENANCE_CONTEXT,
}
DEFAULT_PROVENANCE = PROVENANCE_DERIVED


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    claim: str
    finding: str
    confidence: float
    source: str
    observed_at: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    #: Where the item came from (shared Phase 7 vocabulary). Defaulted per kind
    #: in ``_item`` so a new evidence kind can never silently claim to be
    #: observed.
    provenance: str = DEFAULT_PROVENANCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "claim": self.claim,
            "finding": self.finding,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "observed_at": self.observed_at,
            "detail": dict(self.detail),
            "provenance": self.provenance,
        }


def _item(*, kind, claim, finding, confidence, source, observed_at=None,
          provenance=None, **detail):
    return EvidenceItem(
        kind=kind,
        claim=claim,
        finding=finding,
        confidence=confidence,
        source=source,
        observed_at=observed_at,
        detail=detail,
        provenance=provenance or PROVENANCE_BY_KIND.get(kind, DEFAULT_PROVENANCE),
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def collect(
    *,
    link_from: Any = None,
    link_to: Any = None,
    candidate_links: Iterable[Any] = (),
    decision: dict[str, Any] | None = None,
    previous_decision: dict[str, Any] | None = None,
    breaches: Iterable[dict[str, Any]] = (),
    route_transitions: Iterable[dict[str, Any]] = (),
    bgp_transitions: Iterable[dict[str, Any]] = (),
    link_transitions: Iterable[dict[str, Any]] = (),
    blast_radius: dict[str, Any] | None = None,
    duplicate_names: Iterable[str] = (),
    now: Any = None,
) -> list[dict[str, Any]]:
    """Evidence items for the "link_from failed, traffic moved to link_to" read."""
    items: list[EvidenceItem] = []
    links = list(candidate_links or ())

    # -- 1. State of the path that stopped carrying traffic -----------------
    if link_from is not None:
        available = is_available(link_from)
        admin_down = str(getattr(link_from, "admin_status", "") or "").lower() in {
            "down", "disabled", "shutdown",
        }
        observed_at = _iso(getattr(link_from, "last_state_change_at", None)) or _iso(now)
        if not available:
            claim = (
                f"{getattr(link_from, 'name', 'path')} is admin-down (operator action)"
                if admin_down
                else f"{getattr(link_from, 'name', 'path')} no longer reports usable"
            )
        else:
            claim = f"{getattr(link_from, 'name', 'path')} still reports usable"
        items.append(
            _item(
                kind="link_from_state",
                claim=claim,
                finding=FINDING_SUPPORTS if not available else FINDING_CONTRADICTS,
                confidence=0.95 if not available else 0.6,
                source="topology_links.admin_status/oper_status",
                observed_at=observed_at,
                link_id=str(getattr(link_from, "id", "")),
                link_name=getattr(link_from, "name", None),
                oper_status=getattr(link_from, "oper_status", None),
                admin_status=getattr(link_from, "admin_status", None),
                operator_initiated=admin_down,
            )
        )

    # -- 2. State of the path the decision selected -------------------------
    if link_to is not None:
        available = is_available(link_to)
        items.append(
            _item(
                kind="link_to_state",
                claim=(
                    f"{getattr(link_to, 'name', 'path')} reports usable and is "
                    f"the selected path"
                    if available
                    else f"{getattr(link_to, 'name', 'path')} is NOT usable"
                ),
                finding=FINDING_SUPPORTS if available else FINDING_CONTRADICTS,
                confidence=0.9 if available else 0.95,
                source="topology_links.admin_status/oper_status",
                observed_at=(
                    _iso(getattr(link_to, "last_state_change_at", None)) or _iso(now)
                ),
                link_id=str(getattr(link_to, "id", "")),
                link_name=getattr(link_to, "name", None),
                oper_status=getattr(link_to, "oper_status", None),
                admin_status=getattr(link_to, "admin_status", None),
            )
        )

    # -- 3. Redundancy depth ------------------------------------------------
    unavailable = [link for link in links if not is_available(link)]
    if links:
        items.append(
            _item(
                kind="redundancy_depth",
                claim=(
                    f"{len(links) - len(unavailable)} of {len(links)} candidate "
                    f"WAN paths are usable"
                ),
                finding=(
                    FINDING_CONTRADICTS if len(unavailable) == len(links)
                    else FINDING_SUPPORTS
                ),
                confidence=0.85,
                source="topology_links (candidate WAN paths)",
                observed_at=_iso(now),
                total_paths=len(links),
                unavailable_paths=[getattr(link, "name", None) for link in unavailable],
            )
        )

    # -- 4. Threshold breaches ---------------------------------------------
    for breach in breaches or ():
        level = breach.get("level")
        items.append(
            _item(
                kind="threshold_breach",
                claim=(
                    f"{breach.get('metric')}={breach.get('observed')} at {level} "
                    f"level (policy {breach.get('policy_name')})"
                ),
                finding=FINDING_SUPPORTS if level == LEVEL_CRITICAL else FINDING_NEUTRAL,
                confidence=0.9 if level == LEVEL_CRITICAL else 0.5,
                source=f"operational_sla_policies ({breach.get('scope')})",
                observed_at=_iso(now),
                policy_id=breach.get("policy_id"),
                metric=breach.get("metric"),
                threshold=breach.get("threshold"),
            )
        )

    # -- 5. Measured impairment on the failed path --------------------------
    if link_from is not None:
        name = getattr(link_from, "name", "path")
        latency = to_number(metric_value(link_from, "latency_ms"))
        if latency is not None and latency >= 100.0:
            items.append(
                _item(
                    kind="impairment_measured",
                    claim=f"{name} latency measured at {latency} ms",
                    finding=FINDING_SUPPORTS,
                    confidence=0.7,
                    source="topology_links.latency_ms",
                    observed_at=_iso(now),
                    metric="latency_ms",
                    value=latency,
                )
            )
        loss = to_number(metric_value(link_from, "packet_loss_pct"))
        if loss is not None and loss > 0.0:
            items.append(
                _item(
                    kind="impairment_measured",
                    claim=f"{name} packet loss measured at {loss}%",
                    finding=FINDING_SUPPORTS,
                    confidence=min(0.95, 0.6 + loss / 20.0),
                    source="topology_links.packet_loss_pct",
                    observed_at=_iso(now),
                    metric="packet_loss_pct",
                    value=loss,
                )
            )

    # -- 6. Captured route transitions --------------------------------------
    routes = list(route_transitions or ())
    if routes:
        best_changes = [
            r for r in routes if r.get("field") == "is_best" and r.get("to") is True
        ]
        items.append(
            _item(
                kind="route_transition",
                claim=(
                    f"{len(routes)} routing change(s) captured; "
                    f"{len(best_changes)} new best path(s) elected"
                ),
                finding=FINDING_SUPPORTS if best_changes else FINDING_NEUTRAL,
                confidence=0.85 if best_changes else 0.5,
                source="network_routes (captured transition diff)",
                observed_at=_iso(now),
                transitions=routes[:20],
            )
        )
    else:
        items.append(
            _item(
                kind="route_transition",
                claim=(
                    "no routing changes captured between the two compared "
                    "collection rounds (the collector may not gather routes)"
                ),
                finding=FINDING_NEUTRAL,
                confidence=0.3,
                source="network_routes (captured transition diff)",
                observed_at=_iso(now),
                # About OUR collection, not the device: context, not observation.
                provenance=PROVENANCE_CONTEXT,
            )
        )

    # -- 7. Captured BGP transitions ----------------------------------------
    bgp = list(bgp_transitions or ())
    if bgp:
        down = [
            t for t in bgp if str(t.get("to") or "").lower() not in {"established", "up"}
        ]
        items.append(
            _item(
                kind="bgp_transition",
                claim=(
                    f"{len(bgp)} BGP session change(s) captured; "
                    f"{len(down)} session(s) left Established"
                ),
                finding=FINDING_SUPPORTS if down else FINDING_NEUTRAL,
                confidence=0.9 if down else 0.5,
                source="bgp_neighbors (captured transition diff)",
                observed_at=_iso(now),
                transitions=bgp[:20],
            )
        )
    else:
        items.append(
            _item(
                kind="bgp_transition",
                claim=(
                    "no BGP session changes captured between the two compared "
                    "collection rounds (the collector may not gather BGP)"
                ),
                finding=FINDING_NEUTRAL,
                confidence=0.3,
                source="bgp_neighbors (captured transition diff)",
                observed_at=_iso(now),
                # About OUR collection, not the device: context, not observation.
                provenance=PROVENANCE_CONTEXT,
            )
        )

    # -- 8. Link state transitions ------------------------------------------
    for transition in link_transitions or ():
        items.append(
            _item(
                kind="link_transition",
                claim=(
                    f"{transition.get('name')} {transition.get('field')} "
                    f"{transition.get('from')} -> {transition.get('to')}"
                ),
                finding=(
                    FINDING_SUPPORTS
                    if str(transition.get("to") or "").lower() == "down"
                    else FINDING_NEUTRAL
                ),
                confidence=0.9,
                source="topology_links (captured transition diff)",
                observed_at=transition.get("at") or _iso(now),
                link_id=transition.get("link_id"),
            )
        )

    # -- 9. Impact ---------------------------------------------------------
    if blast_radius and blast_radius.get("total"):
        by_severity = blast_radius.get("by_severity") or {}
        items.append(
            _item(
                kind="blast_radius",
                claim=(
                    f"{blast_radius.get('total')} dependent resource(s) in the "
                    f"blast radius ({by_severity.get('critical', 0)} critical)"
                ),
                finding=FINDING_SUPPORTS,
                confidence=0.8,
                source="resource_relationships (real topology graph)",
                observed_at=_iso(now),
                total=blast_radius.get("total"),
                by_severity=by_severity,
                truncated=blast_radius.get("truncated", False),
            )
        )

    # -- 10. Data-quality caveats ------------------------------------------
    for name in duplicate_names or ():
        items.append(
            _item(
                kind="data_quality",
                claim=(
                    f"more than one WAN link is named {name!r}; the path "
                    f"decision can only identify paths by name"
                ),
                finding=FINDING_NEUTRAL,
                confidence=0.4,
                source="topology_links.name",
                observed_at=_iso(now),
                link_name=name,
            )
        )

    # -- 11. The decision mechanism's own output (context, not proof) --------
    if decision:
        items.append(
            _item(
                kind="decision_engine_output",
                claim=(
                    f"SD-WAN/HA decision engine selected "
                    f"{decision.get('selected_path')!r} (score "
                    f"{decision.get('score')}, decision {decision.get('decision')})"
                ),
                finding=FINDING_NEUTRAL,
                confidence=1.0,
                source="sdwan.evaluate_paths (existing engine, authoritative)",
                observed_at=_iso(now),
                failover=decision.get("failover"),
                reason=decision.get("reason"),
                previous_selected=(previous_decision or {}).get("selected_path"),
            )
        )

    items.sort(key=lambda item: (FINDING_ORDER.index(item.finding), -item.confidence))
    return [item.to_dict() for item in items]


def summarise(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Counts by finding, plus the strongest supporting claim."""
    materialised = list(items or ())
    by_finding = {finding: 0 for finding in FINDING_ORDER}
    for item in materialised:
        finding = item.get("finding") or FINDING_NEUTRAL
        by_finding[finding] = by_finding.get(finding, 0) + 1
    top = next(
        (i for i in materialised if i.get("finding") == FINDING_SUPPORTS), None
    )
    return {
        "total": len(materialised),
        "by_finding": by_finding,
        "strongest_support": top.get("claim") if top else None,
    }
