"""
FailoverEvent — one observed WAN/HA failover (or threshold-breach) episode on a
site edge, captured at detection time and advanced through a finite-state
machine as the observed telemetry evolves.

WHY THIS TABLE CAPTURES ITS OWN TRANSITIONS
-------------------------------------------
``TopologyLink``, ``NetworkRoute`` and ``BgpNeighbor`` deliberately only hold
the CURRENT state (they are refreshed in place by every collection run) — none
of them keeps a transition history. A failover timeline therefore cannot be
reconstructed after the fact from those tables; it has to be *captured* while it
is happening. This model is that capture, in the ``AlertHistory`` tradition:

* ``route_transitions`` / ``bgp_transitions`` — the route and BGP session
  changes the detector observed between two consecutive collection rounds,
* ``decision`` — the verbatim ``sdwan.evaluate_paths()`` result the network's
  own SLA/HA mechanism produced for that site's candidate paths,
* ``link_from`` / ``link_to`` — the path that was carrying traffic and the path
  the decision selected afterwards,
* ``trigger`` — which operational SLA policy/threshold combination fired,
* ``blast_radius`` / ``evidence`` / ``verification`` — the downstream analysis.

HSDEEP NEVER DRIVES THE FAILOVER. The SD-WAN/HA SLA mechanism on the network
remains the authoritative decision maker; this row is an observation, an
explanation and a verification record.

Lifecycle (``status``), forward-only:

    detected ──► failed_over ──► recovering ──► recovered
        │             │              │
        └─────────────┴──────────────┴──► aborted | verification_failed

* ``detected``            — an operational SLA critical breach was observed, but
                            the path decision had not switched yet
* ``failed_over``         — the path decision shows traffic on a different path
* ``recovering``          — the decision is healthy again and the confirmation
                            window is satisfied; verification is running
* ``recovered``           — telemetry-only verification passed
* ``verification_failed`` — the decision looked healthy but verification did not
                            confirm it (honest, explicit non-confirmation)
* ``aborted``             — the breach cleared without a path switch (a
                            threshold blip, not a failover)

``transition_log`` is the append-only audit trail of that FSM; the ``*_at``
timestamps are denormalised copies of the log's milestones so the list API and
the timeline UI never have to parse JSON to sort by time.

``dedup_key`` is a deterministic string (``scope|edge|link_from``) used by a
partial unique index so only ONE open event can exist per edge/path at a time —
a flapping link produces one episode with many appended transitions instead of
a storm of rows. No soft delete: like ``AlertHistory`` this is an append-only
record (status advances; nothing is removed).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant

# -- FSM states --------------------------------------------------------------
STATUS_DETECTED = "detected"
STATUS_FAILED_OVER = "failed_over"
STATUS_RECOVERING = "recovering"
STATUS_RECOVERED = "recovered"
STATUS_VERIFICATION_FAILED = "verification_failed"
STATUS_ABORTED = "aborted"

STATUSES: frozenset[str] = frozenset(
    {
        STATUS_DETECTED,
        STATUS_FAILED_OVER,
        STATUS_RECOVERING,
        STATUS_RECOVERED,
        STATUS_VERIFICATION_FAILED,
        STATUS_ABORTED,
    }
)
#: Statuses that still count as "this episode is not finished".
OPEN_STATUSES: tuple[str, ...] = (
    STATUS_DETECTED, STATUS_FAILED_OVER, STATUS_RECOVERING,
)
#: Statuses that are final — an episode never leaves these.
TERMINAL_STATUSES: tuple[str, ...] = (
    STATUS_RECOVERED, STATUS_VERIFICATION_FAILED, STATUS_ABORTED,
)

#: Legal FSM edges. Enforced in ``failover_intelligence.detector``; declared
#: here so the timeline/API layer can validate without importing the service.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_DETECTED: (STATUS_FAILED_OVER, STATUS_ABORTED, STATUS_RECOVERING),
    STATUS_FAILED_OVER: (STATUS_RECOVERING,),
    STATUS_RECOVERING: (STATUS_RECOVERED, STATUS_VERIFICATION_FAILED),
    STATUS_RECOVERED: (),
    STATUS_VERIFICATION_FAILED: (),
    STATUS_ABORTED: (),
}

_OPEN_SQL = ", ".join(f"'{s}'" for s in OPEN_STATUSES)


class FailoverEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "failover_events"
    __table_args__ = (
        Index("ix_failover_events_tenant_status", "tenant_id", "status"),
        Index(
            "ix_failover_events_tenant_site_detected",
            "tenant_id", "site_id", "detected_at",
        ),
        Index("ix_failover_events_tenant_created", "tenant_id", "created_at"),
        # Resume / sequence lookups are always (tenant, scope) - the detector
        # runs them every round, so they must not scan the table.
        Index("ix_failover_events_tenant_scope", "tenant_id", "scope_key"),
        # `link_from_id` / `link_to_id` are FK REFERENCING columns (topology
        # links are deleted with their tenant), and Postgres needs an index on
        # the referencing side to avoid a sequential scan per deleted link.
        Index("ix_failover_events_link_from_id", "link_from_id"),
        Index("ix_failover_events_link_to_id", "link_to_id"),
        # Only one OPEN episode per (scope, edge, failed path). Flapping appends
        # transitions to the existing row instead of creating a new one.
        Index(
            "uq_failover_events_open_dedup",
            "tenant_id", "dedup_key",
            unique=True,
            postgresql_where=text(f"status IN ({_OPEN_SQL})"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # The site edge device whose WAN paths were being decided.
    edge_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # -- Identity / ordering -------------------------------------------------
    # Monotonic per (tenant, scope); lets the timeline show "event #7" without
    # exposing internals and survives clock corrections better than timestamps.
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(300), nullable=False)

    # -- Lifecycle -----------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=STATUS_DETECTED,
        server_default=STATUS_DETECTED, index=True,
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    transition_log: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )

    # -- What triggered it ---------------------------------------------------
    trigger: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # Verbatim sdwan.evaluate_paths() output at the moment of the decision.
    decision: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    # -- Path transition -----------------------------------------------------
    link_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topology_links.id", ondelete="SET NULL"),
        nullable=True,
    )
    link_from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topology_links.id", ondelete="SET NULL"),
        nullable=True,
    )
    link_to_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # -- Routing / BGP transitions (captured, not reconstructible later) -----
    route_transitions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    bgp_transitions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )

    # -- Analysis ------------------------------------------------------------
    blast_radius: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    verification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # -- Correlations (never created BY this layer) --------------------------
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # -- Milestones ----------------------------------------------------------
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    failed_over_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recovering_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    tenant: Mapped["Tenant"] = relationship()

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def __repr__(self) -> str:
        return (
            f"<FailoverEvent id={self.id} seq={self.sequence_no} "
            f"status={self.status} {self.link_from_name} -> {self.link_to_name}>"
        )

