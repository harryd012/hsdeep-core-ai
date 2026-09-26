"""
Integration model.

A generic, provider-neutral abstraction for external system integrations
(PNETLab, FortiGate, FortiManager, FortiAnalyzer, Security Onion, AWS,
Azure, GCP, SNMP, Syslog, etc).

The Integration model is NOT coupled to subscriptions or plans — it sits
in the Resource Plane and is authorized by the Control Plane's entitlement
system. A tenant's subscription determines HOW MANY integrations they can
create, but the integration itself is a resource.

Architecture:
  Tenant -> Subscription -> Entitlement -> Integration -> Resources
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Integration(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A configured connection to an external system.

    Provider types:
      - NETWORK: PNETLab, FortiGate, physical network gear
      - SECURITY: Security Onion, SIEM, IDS/IPS
      - CLOUD: AWS, Azure, GCP
      - DATACENTER: SNMP, Syslog, WMI, agent-based monitoring
      - MONITORING: PRTG, Zabbix, Prometheus
      - ITSM: ServiceNow, Jira

    Provider identifiers (free-form, not enum):
      - pnetlab, fortigate, fortimanager, fortianalyzer
      - security_onion, aws, azure, gcp
      - snmp, syslog, prtg, servicenow
    """

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_integrations_tenant_id_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Identity --
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # -- Classification --
    # network | security | cloud | datacenter | monitoring | itsm
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Free-form provider identifier (pnetlab, fortigate, aws, etc)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # -- Status --
    # connected | degraded | disconnected | authentication_failed | syncing | discovery_failed | disabled
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="disabled", server_default="disabled", index=True
    )
    # Health score 0-100 (computed from last collection success/failure)
    health_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # -- Capabilities --
    # What this integration can do: discovery, metrics, events, logs, snmp, syslog, etc
    capabilities: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # -- Configuration --
    # Reference to encrypted credential store (not the actual credentials!)
    credential_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Configuration parameters (endpoint, poll interval, etc) — no secrets
    configuration: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # -- Collection state --
    discovery_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    synchronization_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    last_successful_collection: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovery: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synchronization: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- Telemetry metrics --
    telemetry_rate_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    event_rate_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_events_ingested: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # -- Relationships --
    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<Integration id={self.id} name={self.name!r} "
            f"provider={self.provider!r} status={self.status!r}>"
        )
