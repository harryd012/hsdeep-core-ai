"""
DiscoveryScope model (Phase 6.3).

A DiscoveryScope is tenant-scoped CONFIGURATION that says WHERE discovery is
allowed, over WHICH protocols, and under WHAT limits. It is deliberately NOT an
inventory object: a scope only produces observations. Canonical resources
(Device/Interface/Service/Cloud resource) are still created, matched and
enriched by the existing identity path - `DeviceRepository.upsert()` plus the
tenant-scoped `UNIQUE (tenant_id, serial_number)` constraint - so the same real
asset observed through two scopes resolves to ONE canonical resource.

Two tenants may legitimately hold the same CIDR; ownership is never inferred
from the IP, CIDR, hostname or scope name, only from `tenant_id`.

Credentials are NOT stored here. `credential_profile_id` references the existing
credential-bearing `MonitoringSource` row, so this model adds no second secret
store and exposes no secret material.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


#: Canonical scope types (Phase 6.3 §3). Provider-specific discovery behaviour is
#: deliberately NOT implemented here - this phase only establishes the
#: abstraction and its validation.
SCOPE_TYPES: frozenset[str] = frozenset(
    {
        "private_cidr",
        "public_ip",
        "public_cidr",
        "seed_device",
        "dns_domain",
        "cloud_account",
        "cloud_subscription",
        "cloud_project",
    }
)

#: Protocols a scope may allow. Advisory configuration consumed by discovery;
#: a protocol is never attempted unless the scope allows it AND a credential is
#: configured for it.
ALLOWED_PROTOCOLS: frozenset[str] = frozenset(
    {"icmp", "snmp", "ssh", "http", "https", "wmi", "winrm", "api"}
)


class DiscoveryScope(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "discovery_scopes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_discovery_scopes_tenant_id_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # scope_type / target - a discovery ENTRY POINT, never an inventory object.
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    # Reference only - the credential itself lives on the existing
    # MonitoringSource row. Nullable: a scope may legitimately run unauthenticated
    # (ICMP/reachability only).
    credential_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # -- Discovery policy (§7) -------------------------------------------------
    allowed_protocols: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    max_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    max_hosts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=256, server_default="256"
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=32, server_default="32"
    )
    timeout: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1"
    )
    excluded_targets: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Free-form schedule descriptor; execution is owned by the existing
    # scheduler, this only records the tenant's intent.
    schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # -- Run state -------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NOTE: migration a3f7c1d84b26 omitted the ix_discovery_scopes_is_deleted
    # index that every other SoftDeleteMixin table carries. The model mirrors
    # the live schema (no single-column is_deleted index) so autogenerate stays
    # clean; add it via a dedicated forward migration if it is ever wanted.
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    tenant: Mapped["Tenant"] = relationship()
    credential_profile: Mapped["MonitoringSource | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DiscoveryScope {self.name!r} type={self.scope_type} "
            f"target={self.target} tenant={self.tenant_id}>"
        )
