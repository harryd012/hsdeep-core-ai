"""
OperationalSLAPolicy — the *operational* (telemetry-driven) SLA threshold set
the WAN Failover Intelligence layer evaluates against observed link telemetry.

This is deliberately NOT ``SLAPolicy`` (``app/models/sla.py``). That model is
the *business* SLA/OLA contract model used by the SLA Compliance engine
(targets, business hours, breach windows, penalties). This model is its
operational counterpart — the only thing the failover detector needs to answer
"is this WAN link in breach right now?" deterministically from the current link
snapshot:

* one metric per policy (latency / jitter / packet loss / utilisation /
  availability),
* a warning level, a critical level and an independent clear level (so the
  detector can apply hysteresis instead of flapping on a threshold boundary),
* ``min_consecutive_samples`` — the number of consecutive breached collection
  cycles required before a critical breach is treated as an event.

Scopes are intentionally coarse and stable:

* ``all_wan`` — tenant-wide default for every WAN link
* ``site``    — every WAN link whose endpoint device belongs to ``scope_id``
* ``link``    — one specific ``topology_links.id``

When several policies resolve for the same link the most specific scope wins
(link > site > all_wan); see
``app/services/failover_intelligence/policies.py``.

``scope_id`` is polymorphic (a site id or a topology-link id depending on
``scope``) and is therefore intentionally NOT foreign-key constrained: the
``scope`` string states which table it addresses, matching how
``ResourceRelationship``/``ResourceCapability`` handle resource-typed columns.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant

# -- Scope vocabulary --------------------------------------------------------
SCOPE_ALL_WAN = "all_wan"
SCOPE_SITE = "site"
SCOPE_LINK = "link"
SCOPES: frozenset[str] = frozenset({SCOPE_ALL_WAN, SCOPE_SITE, SCOPE_LINK})

#: Most specific first — the resolver returns the first match in this order.
SCOPE_PRECEDENCE: tuple[str, ...] = (SCOPE_LINK, SCOPE_SITE, SCOPE_ALL_WAN)

# -- Metric vocabulary -------------------------------------------------------
METRIC_LATENCY_MS = "latency_ms"
METRIC_JITTER_MS = "jitter_ms"
METRIC_PACKET_LOSS_PCT = "packet_loss_pct"
METRIC_UTILIZATION_PCT = "utilization_pct"
METRIC_AVAILABILITY_PCT = "availability_pct"
METRICS: frozenset[str] = frozenset(
    {
        METRIC_LATENCY_MS,
        METRIC_JITTER_MS,
        METRIC_PACKET_LOSS_PCT,
        METRIC_UTILIZATION_PCT,
        METRIC_AVAILABILITY_PCT,
    }
)

#: Metrics where a HIGHER observed value is worse (breach when value >= level).
HIGHER_IS_WORSE: frozenset[str] = frozenset(
    {
        METRIC_LATENCY_MS,
        METRIC_JITTER_MS,
        METRIC_PACKET_LOSS_PCT,
        METRIC_UTILIZATION_PCT,
    }
)
#: Metrics where a LOWER observed value is worse (breach when value <= level).
LOWER_IS_WORSE: frozenset[str] = frozenset({METRIC_AVAILABILITY_PCT})


class OperationalSLAPolicy(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "operational_sla_policies"
    __table_args__ = (
        Index("ix_operational_sla_policies_tenant_active", "tenant_id", "is_active"),
        Index(
            "ix_operational_sla_policies_tenant_scope",
            "tenant_id", "scope", "scope_id",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Scope ---------------------------------------------------------------
    # all_wan | site | link (see module constants).
    scope: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SCOPE_ALL_WAN, server_default=SCOPE_ALL_WAN,
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # -- Threshold definition ------------------------------------------------
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    warning_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Level at (or past) which an already-breached policy is considered clear
    # again. Optional: when NULL the detector reuses the warning (or critical)
    # level, which is correct but less resistant to boundary flapping.
    clear_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Consecutive breached collection cycles required before a critical breach
    # is promoted to a failover event. Also governs the consecutive healthy
    # cycles required before recovery verification begins.
    min_consecutive_samples: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True,
    )

    # -- Free-form overflow --------------------------------------------------
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<OperationalSLAPolicy id={self.id} name={self.name!r} "
            f"scope={self.scope} metric={self.metric} active={self.is_active}>"
        )
