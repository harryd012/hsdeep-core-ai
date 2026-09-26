"""
FailoverDetectorState — restart-safe working memory for the WAN failover
detector, one row per (tenant, scope).

The detector is a *differ*: it can only say "this route moved", "this BGP
session dropped" or "this path stopped carrying traffic" by comparing the
current collection round's normalised view against the previous round's. Those
previous values are not recoverable from the domain tables (``TopologyLink``,
``NetworkRoute`` and ``BgpNeighbor`` are all refreshed in place, so the
previous state is overwritten), which is precisely why the comparison baseline
has to be persisted somewhere. This table is that baseline.

It also carries the two confirmation counters the FSM needs:

* ``consecutive_breaches`` — collection rounds in a row with a critical breach
  (a single blip must not open an episode), and
* ``consecutive_clear`` — collection rounds in a row with a healthy decision
  (recovery must be observed, not assumed).

Because the state lives in the database rather than in process memory, a
scheduler restart or a worker crash mid-episode resumes the SAME episode
instead of re-opening it — the property an in-memory cache could not provide.

Rows are pure bookkeeping and are safe to delete at any time: the worst case is
that the next round re-establishes a baseline and attributes no transitions to
it (an honest "we do not know what changed before this point" rather than a
fabricated one).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class FailoverDetectorState(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "failover_detector_state"
    __table_args__ = (
        # Exactly one baseline per scope; the repository upserts on this key.
        UniqueConstraint(
            "tenant_id", "scope_key", name="uq_failover_detector_state_scope",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # "site:<uuid>" or "site:none" — human-readable, greppable, and stable
    # across restarts (see failover_intelligence.detector.scope_key_for).
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    edge_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # Restart-safe resume handle: the open episode this baseline belongs to, if
    # any. Cleared when the episode reaches a terminal status.
    active_episode_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("failover_events.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # -- Confirmation counters ----------------------------------------------
    consecutive_breaches: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    consecutive_clear: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    last_breach_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # -- Captured baseline (previous round's normalised view) ---------------
    previous_decision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {link_id: {name, oper_status, admin_status, latency_ms, ...}}
    link_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # {"<resource_id>|<vrf>|<dest>|<proto>": {state, is_best, next_hop, ...}}
    route_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # {"<resource_id>|<vrf>|<neighbor_ip>": {state, uptime_seconds, ...}}
    bgp_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<FailoverDetectorState scope={self.scope_key!r} "
            f"edge={self.edge_device_id} breaches={self.consecutive_breaches}>"
        )
