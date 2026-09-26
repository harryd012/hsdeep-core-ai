"""
Operational state of a resource — pure derivations, no I/O.

Three questions that must never be collapsed into one:

1. **How far have we got in understanding this thing?** (identity stage)
2. **Is a collector actually responsible for its telemetry?** (binding)
3. **Is the telemetry we hold still current?** (freshness)

A target that only answered an ICMP probe is *evidence*: ``discovered``, unbound,
``never_collected``. It is NOT an operational resource and this module will never
describe it as one. Equally, a resource with ``expired`` telemetry is not
``healthy``; it is unknown-and-old, and the blocker says exactly that.

Everything here is a pure function over already-loaded facts, so it is cheap to
test and cannot silently query its way to a different answer than the UI shows.

The vocabulary lives in ``vocabulary`` (one source of truth shared with the
health service), and the numbers deliberately reuse the existing thresholds:
``AGING`` starts at 60% of the established stale window and ``EXPIRED`` is four
windows, so a resource can never be "fresh" for the resource view while the
health view already calls it stale.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.infrastructure_intelligence import vocabulary as V

#: Collector outcomes that mean "we cannot collect this right now".
_COLLECTION_FAILURE_STATES: frozenset[str] = frozenset(
    {
        V.COLLECTION_FAILED,
        V.COLLECTION_UNAVAILABLE,
        V.COLLECTION_UNSUPPORTED,
        V.COLLECTION_NOT_AVAILABLE,
    }
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def age_seconds(value: datetime | None, *, now: datetime | None = None) -> float | None:
    """Age of a timestamp in seconds, or None when there is no timestamp."""
    stamp = _as_utc(value)
    if stamp is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (reference - stamp).total_seconds())


def freshness_state_for_age(
    age: float,
    *,
    aging_after_seconds: int = V.TELEMETRY_AGING_AFTER_SECONDS,
    stale_after_seconds: int = V.TELEMETRY_STALE_AFTER_SECONDS,
    expired_after_seconds: float = V.TELEMETRY_EXPIRED_AFTER_SECONDS,
) -> str:
    """Freshness bucket for a KNOWN age. The only place these thresholds are applied.

    Exposed separately so a single telemetry record (which has no binding
    context) can be classified with exactly the same numbers as a whole resource.
    """
    if age > expired_after_seconds:
        return V.FRESHNESS_EXPIRED
    if age > stale_after_seconds:
        return V.FRESHNESS_STALE
    if age > aging_after_seconds:
        return V.FRESHNESS_AGING
    return V.FRESHNESS_FRESH


@dataclass(frozen=True)
class ResourceFacts:
    """Everything the derivation needs, already loaded.

    Deliberately flat and JSON-friendly: the service layer fills it from the
    Device row plus capability/relationship summaries, and tests construct it
    directly.
    """

    resource_id: str | None = None
    resource_type: str | None = None
    resource_name: str | None = None
    lifecycle_state: str | None = None
    # -- identity --
    identity_key: str | None = None
    identity_confidence: float = 0.0
    recognized: bool = False
    vendor: str | None = None
    model: str | None = None
    # -- binding --
    monitoring_source_id: str | None = None
    monitoring_source_name: str | None = None
    monitoring_source_type: str | None = None
    monitoring_source_active: bool | None = None
    provider: str | None = None
    provider_resource_id: str | None = None
    #: Distinct OTHER monitoring sources claiming the same identity key.
    competing_source_ids: tuple[str, ...] = ()
    # -- telemetry --
    last_telemetry_at: datetime | None = None
    last_collection_status: str | None = None
    # -- capabilities --
    supported_capabilities: tuple[str, ...] = ()
    unsupported_capabilities: tuple[str, ...] = ()
    unknown_capabilities: tuple[str, ...] = ()
    #: Capabilities the DISCOVERY collector asserted for this device (the
    #: ``device_capabilities`` store, e.g. ``supports_ping``). Real evidence, but
    #: a different store from the capability assertions above, so it is reported
    #: separately and only ever used to reach "collectable" - never to claim a
    #: capability is exposed when nothing has asserted it.
    discovery_capabilities: tuple[str, ...] = ()
    # -- topology --
    relationship_count: int = 0


@dataclass(frozen=True)
class FreshnessAssessment:
    """Freshness of the telemetry we hold for one resource."""

    state: str
    age_seconds: float | None
    last_telemetry_at: datetime | None
    reason: str | None
    detail: str

    @property
    def is_current(self) -> bool:
        return self.state in V.FRESHNESS_CURRENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "is_current": self.is_current,
            "age_seconds": (
                None if self.age_seconds is None else round(self.age_seconds, 3)
            ),
            "last_telemetry_at": (
                self.last_telemetry_at.isoformat() if self.last_telemetry_at else None
            ),
            "reason": self.reason,
            "detail": self.detail,
        }


def assess_freshness(
    facts: ResourceFacts,
    *,
    now: datetime | None = None,
    aging_after_seconds: int = V.TELEMETRY_AGING_AFTER_SECONDS,
    stale_after_seconds: int = V.TELEMETRY_STALE_AFTER_SECONDS,
    expired_after_seconds: float = V.TELEMETRY_EXPIRED_AFTER_SECONDS,
) -> FreshnessAssessment:
    """Freshness state of a resource's telemetry.

    Order matters and is the whole point: "no binding" is reported before "no
    telemetry" (we are not expecting any), and "a collector is bound but nothing
    has arrived" is reported as ``never_collected`` rather than ``stale`` - the
    two need different operator actions (fix the collector vs. wait).

    Only an ACTIVE binding counts: a source attached but switched off is not
    collecting anything, so its resource is ``unavailable`` rather than
    "waiting for a collector that is supposedly running".
    """
    bound = bool(facts.monitoring_source_id) and (
        facts.monitoring_source_active is not False
    )
    last = _as_utc(facts.last_telemetry_at)

    if last is None:
        if not bound:
            return FreshnessAssessment(
                state=V.FRESHNESS_UNAVAILABLE,
                age_seconds=None,
                last_telemetry_at=None,
                reason=V.REASON_FRESHNESS_UNAVAILABLE,
                detail=(
                    "no ACTIVE monitoring binding, so no telemetry is expected - "
                    "discovery evidence only"
                    if facts.monitoring_source_id
                    else "no monitoring source is bound to this resource, so no "
                    "telemetry is expected - discovery evidence only"
                ),
            )
        if facts.last_collection_status in _COLLECTION_FAILURE_STATES:
            return FreshnessAssessment(
                state=V.FRESHNESS_NEVER_COLLECTED,
                age_seconds=None,
                last_telemetry_at=None,
                reason=V.REASON_COLLECTOR_FAILED,
                detail=(
                    "the bound collector has not produced a sample; last "
                    f"collection status was {facts.last_collection_status!r}"
                ),
            )
        return FreshnessAssessment(
            state=V.FRESHNESS_NEVER_COLLECTED,
            age_seconds=None,
            last_telemetry_at=None,
            reason=V.REASON_FRESHNESS_NEVER_COLLECTED,
            detail="a collector is bound but no sample has ever been received",
        )

    age = age_seconds(last, now=now)
    assert age is not None  # `last` is not None here
    state = freshness_state_for_age(
        age,
        aging_after_seconds=aging_after_seconds,
        stale_after_seconds=stale_after_seconds,
        expired_after_seconds=expired_after_seconds,
    )
    if state == V.FRESHNESS_EXPIRED:
        return FreshnessAssessment(
            state=state,
            age_seconds=age,
            last_telemetry_at=last,
            reason=V.REASON_FRESHNESS_EXPIRED,
            detail=(
                f"newest telemetry is {int(age)}s old (expired beyond "
                f"{int(expired_after_seconds)}s): not usable as the current state"
            ),
        )
    if state == V.FRESHNESS_STALE:
        return FreshnessAssessment(
            state=state,
            age_seconds=age,
            last_telemetry_at=last,
            reason=V.REASON_TELEMETRY_STALE,
            detail=f"newest telemetry is {int(age)}s old",
        )
    return FreshnessAssessment(
        state=state,
        age_seconds=age,
        last_telemetry_at=last,
        reason=None,
        detail=(
            f"newest telemetry is {int(age)}s old"
            + (" (ageing)" if state == V.FRESHNESS_AGING else "")
        ),
    )


@dataclass(frozen=True)
class MonitoringBinding:
    """Whether a collector is responsible for this resource's telemetry."""

    state: str
    source_id: str | None
    source_name: str | None
    source_type: str | None
    source_active: bool | None
    provider: str | None
    provider_resource_id: str | None
    detail: str

    @property
    def is_bound(self) -> bool:
        return self.state == V.BINDING_BOUND

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "is_bound": self.is_bound,
            "monitoring_source_id": self.source_id,
            "monitoring_source_name": self.source_name,
            "monitoring_source_type": self.source_type,
            "monitoring_source_active": self.source_active,
            "provider": self.provider,
            # The provider-native identifier is preserved verbatim: it is the
            # only thing that can map a resource back to a vendor device page.
            "provider_resource_id": self.provider_resource_id,
            "detail": self.detail,
        }


def resolve_binding(facts: ResourceFacts) -> MonitoringBinding:
    """Resolve the canonical-resource -> monitoring-binding edge.

    A resource is only BOUND when a source is attached AND active. Competing
    sources for the same identity make telemetry ownership unprovable, so the
    binding is reported as AMBIGUOUS instead of silently picking the first -
    a wrong attribution is worse than an admitted ambiguity.
    """
    if not facts.monitoring_source_id:
        return MonitoringBinding(
            state=V.BINDING_UNBOUND,
            source_id=None,
            source_name=None,
            source_type=None,
            source_active=None,
            provider=facts.provider,
            provider_resource_id=facts.provider_resource_id,
            detail=(
                "no monitoring source is attached: this resource exists as "
                "discovery evidence only, and no telemetry is expected"
            ),
        )
    if facts.competing_source_ids:
        return MonitoringBinding(
            state=V.BINDING_AMBIGUOUS,
            source_id=facts.monitoring_source_id,
            source_name=facts.monitoring_source_name,
            source_type=facts.monitoring_source_type,
            source_active=facts.monitoring_source_active,
            provider=facts.provider,
            provider_resource_id=facts.provider_resource_id,
            detail=(
                f"{len(facts.competing_source_ids) + 1} monitoring sources claim the "
                f"same identity key {facts.identity_key!r}; telemetry ownership is "
                "not provable without operator input"
            ),
        )
    if facts.monitoring_source_active is False:
        return MonitoringBinding(
            state=V.BINDING_UNBOUND,
            source_id=facts.monitoring_source_id,
            source_name=facts.monitoring_source_name,
            source_type=facts.monitoring_source_type,
            source_active=False,
            provider=facts.provider,
            provider_resource_id=facts.provider_resource_id,
            detail="the monitoring source attached to this resource is inactive",
        )
    return MonitoringBinding(
        state=V.BINDING_BOUND,
        source_id=facts.monitoring_source_id,
        source_name=facts.monitoring_source_name,
        source_type=facts.monitoring_source_type,
        source_active=facts.monitoring_source_active,
        provider=facts.provider,
        provider_resource_id=facts.provider_resource_id,
        detail=(
            "a collector is responsible for this resource"
            + (f" (provider {facts.provider})" if facts.provider else "")
        ),
    )


@dataclass(frozen=True)
class TopologyAssessment:
    """Whether we hold adjacency evidence for this resource."""

    state: str
    relationship_count: int
    stale_count: int
    current_count: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "relationship_count": self.relationship_count,
            "current": self.current_count,
            "stale": self.stale_count,
            "detail": self.detail,
        }


def assess_topology(
    facts: ResourceFacts,
    edges: Any = (),
    *,
    now: datetime | None = None,
) -> TopologyAssessment:
    """Topology status derived ONLY from persisted relationship evidence.

    ``NO_RELATIONSHIP_EVIDENCE`` is a first-class answer: an empty graph means we
    have no adjacency evidence, not that the resource has no neighbours. Nothing
    here infers an edge from a shared subnet, a name, or a site.
    """
    edge_list = list(edges or ())
    if not edge_list:
        count = int(facts.relationship_count or 0)
        if count <= 0:
            return TopologyAssessment(
                state=V.NO_RELATIONSHIP_EVIDENCE,
                relationship_count=0,
                stale_count=0,
                current_count=0,
                detail=(
                    "no adjacency evidence has been collected for this resource "
                    "(LLDP/CDP/ARP/BGP/SD-WAN/cloud); this is not a claim that it "
                    "has no neighbours"
                ),
            )
        return TopologyAssessment(
            state=V.RELATIONSHIP_ACTIVE,
            relationship_count=count,
            stale_count=0,
            current_count=count,
            detail=f"{count} recorded relationship(s)",
        )

    stale = 0
    current = 0
    for edge in edge_list:
        if relationship_evidence_state(edge, now=now) == V.RELATIONSHIP_ACTIVE:
            current += 1
        else:
            stale += 1
    return TopologyAssessment(
        state=V.RELATIONSHIP_ACTIVE if current else V.RELATIONSHIP_STALE,
        relationship_count=len(edge_list),
        stale_count=stale,
        current_count=current,
        detail=(
            f"{current} current, {stale} stale relationship(s)"
            if stale
            else f"{current} current relationship(s)"
        ),
    )


# ---------------------------------------------------------------------------
# Relationship evidence
# ---------------------------------------------------------------------------
def relationship_evidence_state(
    edge: Any,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = V.RELATIONSHIP_STALE_AFTER_SECONDS,
) -> str:
    """``active`` / ``stale`` / ``removed`` for one persisted relationship.

    Derived from the row's own provenance (status + last observation), never
    from a re-probe: this answers "is the evidence we hold still current?", which
    is a different question from "is this adjacency true right now?".
    """
    status = str(getattr(edge, "status", "") or "").strip().lower()
    if status == V.RELATIONSHIP_REMOVED:
        return V.RELATIONSHIP_REMOVED
    last_seen = getattr(edge, "last_seen", None) or getattr(edge, "updated_at", None)
    age = age_seconds(last_seen, now=now)
    if age is None:
        # No observation timestamp: we cannot claim it is current.
        return V.RELATIONSHIP_STALE
    return (
        V.RELATIONSHIP_ACTIVE
        if age <= stale_after_seconds
        else V.RELATIONSHIP_STALE
    )


def merge_relationship_evidence(
    attributes: dict[str, Any] | None,
    *,
    source: str,
    observed_at: datetime,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one observation into an edge's per-source evidence ledger.

    A later LLDP-only poll must not erase earlier ARP evidence, so every source
    keeps its own entry with its own last-seen timestamp. Returns a NEW dict
    (the caller persists it) so this stays a pure function.
    """
    merged = dict(attributes or {})
    ledger = dict(merged.get(V.RELATIONSHIP_EVIDENCE_LEDGER_KEY) or {})
    existing = dict(ledger.get(source) or {})
    entry = {**existing, **dict(payload or {})}
    entry["last_seen"] = observed_at.isoformat()
    entry.setdefault("first_seen", existing.get("last_seen") or observed_at.isoformat())
    ledger[source] = entry
    merged[V.RELATIONSHIP_EVIDENCE_LEDGER_KEY] = ledger
    # Keep the single-source convenience keys in sync for existing consumers.
    merged["evidence_type"] = source
    merged["evidence_last_seen"] = observed_at.isoformat()
    return merged


def evidence_sources_of(edge: Any) -> list[str]:
    """Evidence sources recorded for an edge, newest evidence first.

    Falls back to the flat ``evidence_type``/``discovered_by`` columns for rows
    written before the ledger existed, so old evidence is never invisible.
    """
    attributes = getattr(edge, "attributes", None) or {}
    ledger = attributes.get(V.RELATIONSHIP_EVIDENCE_LEDGER_KEY) or {}
    if ledger:
        return sorted(
            (str(key) for key in ledger),
            key=lambda key: str((ledger.get(key) or {}).get("last_seen") or ""),
            reverse=True,
        )
    flat = attributes.get("evidence_type") or getattr(edge, "discovered_by", None)
    return [str(flat)] if flat else []


# ---------------------------------------------------------------------------
# Identity stage
# ---------------------------------------------------------------------------
#: Raw stored spellings that mean "deliberately out of the collection path".
_RETIRED_LIFECYCLE_STATES: frozenset[str] = frozenset(
    {V.LIFECYCLE_RETIRED, V.LIFECYCLE_DECOMMISSIONED}
)

#: Resource types that carry no classification. A resource with one of these has
#: NOT been classified, whatever else it reports.
_UNCLASSIFIED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"", "unknown", "unrecognized", "unrecognised", "undetermined", "other"}
)


def identity_stage(
    facts: ResourceFacts,
    *,
    freshness: FreshnessAssessment,
    binding: MonitoringBinding,
    now: datetime | None = None,
) -> tuple[str, str]:
    """``(stage, reason)`` for one resource. Ascending, evidence-gated.

    Each rung requires the evidence for THAT rung; nothing is skipped and nothing
    is assumed. An unrecognised probe stops at ``discovered`` even when a device
    row exists for it, because a row is persistence, not identification.
    """
    lifecycle = str(facts.lifecycle_state or "").strip().lower()
    if lifecycle in _RETIRED_LIFECYCLE_STATES:
        return (
            V.STAGE_RETIRED,
            f"lifecycle is {lifecycle!r}: deliberately outside the collection path",
        )

    resource_type = str(facts.resource_type or "").strip().lower()
    if not facts.identity_key and not facts.recognized:
        return (
            V.STAGE_DISCOVERED,
            "probe evidence only: no stable identity key and no recognition result",
        )
    if resource_type in _UNCLASSIFIED_RESOURCE_TYPES:
        return (
            V.STAGE_IDENTIFIED,
            (
                "identity established"
                + (f" ({facts.identity_key})" if facts.identity_key else "")
                + ", but the resource type is still unknown"
            ),
        )

    has_assertions = bool(
        facts.supported_capabilities
        or facts.unsupported_capabilities
        or facts.unknown_capabilities
    )
    if not has_assertions and not facts.discovery_capabilities:
        return (
            V.STAGE_CLASSIFIED,
            f"classified as {resource_type!r}; no capability assertions resolved yet",
        )

    if not binding.is_bound:
        return (
            V.STAGE_CAPABILITY_RESOLVED,
            (
                "capabilities resolved, but no active collector is bound "
                f"({binding.state})"
            ),
        )

    # Collectability needs capability evidence from EITHER store: an explicit
    # assertion, or a capability the discovery collector actually reached.
    if not (facts.supported_capabilities or facts.discovery_capabilities):
        return (
            V.STAGE_CAPABILITY_RESOLVED,
            (
                "a collector is bound, but no capability is collectable for this "
                "resource type (nothing supported)"
            ),
        )

    if facts.last_telemetry_at is not None and freshness.state in {
        V.FRESHNESS_STALE,
        V.FRESHNESS_EXPIRED,
    }:
        return (
            V.STAGE_STALE,
            (
                f"was monitored, but its telemetry is {freshness.state} "
                f"({freshness.detail})"
            ),
        )

    if freshness.state in V.FRESHNESS_CURRENT:
        return (V.STAGE_MONITORED, f"bound and carrying {freshness.state} telemetry")

    return (
        V.STAGE_COLLECTABLE,
        f"bound and collectable; telemetry is {freshness.state} ({freshness.detail})",
    )


@dataclass(frozen=True)
class ResourceOperationalState:
    """The four axes the UI must show separately, plus why they are what they are."""

    resource_id: str | None
    resource_type: str | None
    resource_name: str | None
    lifecycle_state: str | None
    identity_stage: str
    identity_reason: str
    is_operational_resource: bool
    freshness: FreshnessAssessment
    binding: MonitoringBinding
    topology: TopologyAssessment
    blockers: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "lifecycle_state": self.lifecycle_state,
            "identity_stage": self.identity_stage,
            "identity_reason": self.identity_reason,
            # True only once we hold a classified, bound resource whose telemetry
            # is current. Evidence-only targets stay False on purpose.
            "is_operational_resource": self.is_operational_resource,
            "freshness": self.freshness.to_dict(),
            "binding": self.binding.to_dict(),
            "topology": self.topology.to_dict(),
            "blockers": list(self.blockers),
            "evidence_sources": sorted(set(self.evidence_sources)),
        }


def derive_operational_state(
    facts: ResourceFacts,
    *,
    edges: Any = (),
    now: datetime | None = None,
) -> ResourceOperationalState:
    """Compose freshness + binding + topology + the identity stage.

    The ``blockers`` list is what makes the state actionable: each entry names the
    concrete missing evidence that holds the resource at its current rung, in the
    order an operator should fix them.
    """
    freshness = assess_freshness(facts, now=now)
    binding = resolve_binding(facts)
    topology = assess_topology(facts, edges, now=now)
    stage, reason = identity_stage(facts, freshness=freshness, binding=binding, now=now)

    blockers: list[str] = []
    if stage == V.STAGE_DISCOVERED:
        blockers.append(
            "no stable identity key: recognise the device (serial, provider id) "
            "before treating it as an operational resource"
        )
    if binding.state == V.BINDING_UNBOUND:
        blockers.append(
            "no active monitoring binding: attach a collector before expecting "
            "telemetry"
        )
    if binding.state == V.BINDING_AMBIGUOUS:
        blockers.append(
            "multiple monitoring sources claim this identity: telemetry ownership "
            "must be resolved by an operator"
        )
    if freshness.state == V.FRESHNESS_NEVER_COLLECTED:
        blockers.append(
            "a collector is bound but has never delivered a sample; verify "
            "credentials, scopes and device reachability"
        )
    if freshness.state in {V.FRESHNESS_STALE, V.FRESHNESS_EXPIRED}:
        blockers.append(
            f"telemetry is {freshness.state}: no operational conclusion can be "
            "drawn from it"
        )
    if topology.state == V.NO_RELATIONSHIP_EVIDENCE:
        blockers.append(
            "no topology evidence: blast radius and dependency analysis stay empty "
            "until an adjacency collector runs"
        )

    return ResourceOperationalState(
        resource_id=facts.resource_id,
        resource_type=facts.resource_type,
        resource_name=facts.resource_name,
        lifecycle_state=facts.lifecycle_state,
        identity_stage=stage,
        identity_reason=reason,
        is_operational_resource=stage == V.STAGE_MONITORED,
        freshness=freshness,
        binding=binding,
        topology=topology,
        blockers=tuple(blockers),
        evidence_sources=tuple(
            source for edge in (edges or ()) for source in evidence_sources_of(edge)
        ),
    )
