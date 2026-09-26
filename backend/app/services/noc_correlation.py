"""
NOC correlation engine (Phase 5 - topology-aware event correlation).

ADDITIVE extension of the existing alert correlation architecture
(`app/services/alerting/correlation_engine.py`, which stays untouched and
keeps owning the Alert -> Incident path). This engine correlates
NormalizedEvents into NOC events, reusing the same design principles:

  - Honest heuristics only: every grouping/score below is a deterministic
    query or comparison over real stored data (NormalizedEvent /
    NocEvent / ResourceRelationship). No ML, no simulated telemetry.
  - Conservative suppression: an event is only marked as a symptom when it
    belongs to the SAME deterministic group as the chosen root cause;
    events with no grouping key are left uncorrelated, never force-grouped.
  - Idempotency: re-running correlation on unchanged data returns the
    existing NocEvent (`created=False`) instead of duplicating groups -
    via NocEventRepository.find_active_by_signature.
  - Time-window bounded: only events newer than the request window are
    considered; cold groups are not silently extended.
  - Tenant-scoped: every query is filtered by tenant_id.

Grouping keys (deterministic, first match wins):
  1. event_signature          - same telemetry condition
  2. (device_id, category)    - same device, same category
  3. (site_id, category)      - same site, same category
  4. nothing                  - event stays uncorrelated

Root cause: earliest-triggered "availability"-category event in the group
(a device/link being down precedes its performance symptoms), mirroring
the alert engine's conservative heuristic; otherwise the earliest event,
and the result says so via `selection_reason`.

Evidence sufficiency (documented formula, derived from real data):
  - "sufficient": >= 3 correlated events AND >= 1 real topology edge
  - "partial":    >= 2 correlated events
  - "insufficient": single event (reported honestly, still correlated)

Confidence score (documented formula over real evidence):
  0.4 (group formed) + 0.2 * min(topology_edges, 2) / 2
  + 0.2 * (group size >= 3) + 0.2 * (all events share one signature)
  Capped at 0.95 - never claims certainty.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_event import NormalizedEvent
from app.models.noc_event import NocEvent
from app.repositories.noc_event_repository import NocEventRepository
from app.repositories.normalized_event_repository import NormalizedEventRepository
from app.schemas.correlation import (
    CorrelatedEventRef,
    CorrelationGroupSummary,
    CorrelationResult,
    CorrelationRunRequest,
    RootCauseCandidate,
    TopologyEvidenceEdge,
)
from app.services.alerting.severity_mapper import max_severity
from app.services.topology_service import TopologyService

_ENGINE_SOURCE = "noc-correlation"
_ROOT_CAUSE_CATEGORY = "availability"


class NocCorrelationEngine:
    """Correlates NormalizedEvents into NOC events with topology evidence."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.noc_repo = NocEventRepository(db)
        self.event_repo = NormalizedEventRepository(db)
        self.topology = TopologyService(db)

    # -- Public API ---------------------------------------------------------

    async def run(
        self, tenant_id: uuid.UUID, request: CorrelationRunRequest
    ) -> list[CorrelationResult]:
        """Correlate recent uncorrelated normalized events into groups.

        Returns one CorrelationResult per affected group (new OR re-used
        NocEvent). Idempotent: a second run over the same data returns []
        because the events are already claimed by a correlation group.
        """
        now = datetime.now(timezone.utc)
        since = now - timedelta(minutes=request.time_window_minutes)
        events = await self.event_repo.list_uncorrelated(
            tenant_id, since=since, limit=request.max_related_events
        )
        if not events:
            return []

        groups = self._group(events)
        results: list[CorrelationResult] = []
        for (key_kind, key_value), members in groups.items():
            members.sort(key=lambda e: (e.event_timestamp or now))
            existing = await self.noc_repo.find_active_by_signature(
                tenant_id,
                source=_ENGINE_SOURCE,
                event_signature=self._sig_key(key_kind, key_value),
            )
            if existing is not None:
                # Idempotent re-run: reuse the existing NOC event; do NOT
                # re-derive a new group (mirrors CorrelationEngine's
                # recomputed=False fast path).
                noc_event = existing
                created = False
                root_event = await self._pick_root_cause(members)
            else:
                created = True
                root_event = await self._pick_root_cause(members)
                noc_event = await self._create_noc_event(
                    tenant_id, key_kind, key_value, root_event, members
                )

            topology_path = await self._topology_evidence(tenant_id, root_event)
            await self._claim_members(members, noc_event, root_event)

            group_size = len(members)
            shared_signature = key_kind == "sig"
            confidence = min(
                0.95,
                0.4
                + 0.2 * min(len(topology_path), 2) / 2
                + (0.2 if group_size >= 3 else 0.0)
                + (0.2 if shared_signature else 0.0),
            )
            if group_size >= 3 and topology_path:
                sufficiency = "sufficient"
            elif group_size >= 2:
                sufficiency = "partial"
            else:
                sufficiency = "insufficient"

            results.append(
                CorrelationResult(
                    tenant_id=tenant_id,
                    correlation_id=uuid.uuid5(uuid.NAMESPACE_URL, f"noc-corr:{noc_event.id}"),
                    root_cause=self._root_cause_candidate(root_event),
                    correlated_events=[self._event_ref(e) for e in members],
                    suppressed_symptom_ids=[
                        e.id for e in members if e.is_suppressed and e.id != root_event.id
                    ],
                    topology_path=topology_path,
                    affected_device_ids=sorted(
                        {e.device_id for e in members if e.device_id is not None},
                        key=lambda u: str(u),
                    ),
                    confidence_score=confidence,
                    evidence_sufficiency=sufficiency,
                    noc_event_id=noc_event.id,
                    created=created,
                )
            )

        await self.db.commit()
        return results

    async def group_summaries(
        self, tenant_id: uuid.UUID, limit: int = 50
    ) -> list[CorrelationGroupSummary]:
        """Read-side listing of active correlation groups (NOC events
        produced by this engine) with real counts from linked events."""
        noc_events = await self.noc_repo.list(
            tenant_id, source=_ENGINE_SOURCE, limit=limit
        )
        summaries: list[CorrelationGroupSummary] = []
        for noc_event in noc_events:
            if noc_event.status == "resolved":
                continue
            linked = await self.event_repo.list_by_noc_event(tenant_id, noc_event.id)
            suppressed = [e for e in linked if e.is_suppressed]
            latest = max(
                (e.event_timestamp for e in linked if e.event_timestamp is not None),
                default=None,
            )
            summaries.append(
                CorrelationGroupSummary(
                    correlation_id=uuid.uuid5(uuid.NAMESPACE_URL, f"noc-corr:{noc_event.id}"),
                    root_cause_title=noc_event.title,
                    root_cause_severity=noc_event.severity,
                    root_cause_category=noc_event.category,
                    root_cause_device_id=noc_event.device_id,
                    correlated_count=len(linked),
                    suppressed_count=len(suppressed),
                    noc_event_id=noc_event.id,
                    latest_event_at=latest,
                )
            )
        return summaries

    # -- Grouping -----------------------------------------------------------

    def _group(
        self, events: list[NormalizedEvent]
    ) -> dict[tuple[str, str], list[NormalizedEvent]]:
        """Deterministic grouping (see module docstring for the key order)."""
        groups: dict[tuple[str, str], list[NormalizedEvent]] = defaultdict(list)
        for event in events:
            if event.event_signature:
                groups[("sig", event.event_signature)].append(event)
            elif event.device_id is not None and event.event_category:
                groups[("dev", f"{event.device_id}:{event.event_category}")].append(event)
            elif event.site_id is not None and event.event_category:
                groups[("site", f"{event.site_id}:{event.event_category}")].append(event)
            # else: no grouping key -> left uncorrelated (honest heuristic)
        return groups

    @staticmethod
    def _sig_key(kind: str, value: str) -> str:
        return f"{kind}:{value}"

    # -- Root cause ---------------------------------------------------------

    async def _pick_root_cause(self, members: list[NormalizedEvent]) -> NormalizedEvent:
        candidates = [
            e for e in members
            if e.event_category == _ROOT_CAUSE_CATEGORY and e.is_suppressed is not True
        ]
        pool = candidates or members
        return min(
            pool,
            key=lambda e: (e.event_timestamp or datetime.max.replace(tzinfo=timezone.utc)),
        )

    def _root_cause_candidate(self, event: NormalizedEvent) -> RootCauseCandidate:
        if event.event_category == _ROOT_CAUSE_CATEGORY:
            reason = (
                "earliest availability-category event in the group "
                "(outage conditions precede their performance symptoms)"
            )
        else:
            reason = (
                "earliest event in the group (no availability-category "
                "event present yet - conservative fallback)"
            )
        return RootCauseCandidate(
            event_id=event.id,
            title=event.title,
            event_category=event.event_category,
            event_type=event.event_type or "unknown",
            severity=event.severity,
            device_id=event.device_id,
            site_id=event.site_id,
            selection_reason=reason,
        )

    # -- Persistence --------------------------------------------------------

    async def _create_noc_event(
        self,
        tenant_id: uuid.UUID,
        key_kind: str,
        key_value: str,
        root_event: NormalizedEvent,
        members: list[NormalizedEvent],
    ) -> NocEvent:
        earliest = min((e.event_timestamp for e in members if e.event_timestamp), default=None)
        latest = max((e.event_timestamp for e in members if e.event_timestamp), default=None)
        return await self.noc_repo.create(
            tenant_id=tenant_id,
            site_id=root_event.site_id,
            device_id=root_event.device_id,
            event_signature=self._sig_key(key_kind, key_value),
            source=_ENGINE_SOURCE,
            category=root_event.event_category or "general",
            title=f"NOC Correlation: {root_event.title}",
            description=None,
            severity=max_severity([e.severity for e in members]),
            status="open",
            triggered_at=earliest,
            last_occurred_at=latest,
            occurrence_count=len(members),
        )

    async def _claim_members(
        self,
        members: list[NormalizedEvent],
        noc_event: NocEvent,
        root_event: NormalizedEvent,
    ) -> None:
        """Link every member to the NOC event + group id; suppress all
        non-root members (conservative: same-group symptoms only)."""
        correlation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"noc-corr:{noc_event.id}")
        for event in members:
            await self.event_repo.set_correlation_id(event, correlation_id)
            await self.event_repo.link_to_noc_event(event, noc_event.id)
            if event.id != root_event.id and not event.is_suppressed:
                await self.event_repo.suppress(event)

    # -- Topology evidence --------------------------------------------------

    async def _topology_evidence(
        self, tenant_id: uuid.UUID, root_event: NormalizedEvent
    ) -> list[TopologyEvidenceEdge]:
        """Real stored topology edges upstream of the root-cause device.

        Returns [] (never fabricated evidence) when the root cause has no
        device or the device has no upstream dependencies.
        """
        if root_event.device_id is None:
            return []
        upstream = await self.topology.get_upstream_dependencies(
            root_event.device_id, tenant_id, max_depth=3
        )
        return [
            TopologyEvidenceEdge(
                source_device_id=e["source_device_id"],
                target_device_id=e["target_device_id"],
                relationship_type=e["relationship_type"],
            )
            for e in upstream
        ]

    # -- Refs ---------------------------------------------------------------

    @staticmethod
    def _event_ref(event: NormalizedEvent) -> CorrelatedEventRef:
        return CorrelatedEventRef(
            id=event.id,
            event_category=event.event_category,
            event_type=event.event_type or "unknown",
            severity=event.severity,
            title=event.title,
            device_id=event.device_id,
            site_id=event.site_id,
            occurrence_count=event.occurrence_count or 1,
            is_suppressed=bool(event.is_suppressed),
            event_timestamp=event.event_timestamp,
        )