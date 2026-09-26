"""
MonitoringSource model. Represents a single connected instance of an external
monitoring platform (a PRTG core, a Meraki org, an Azure subscription, etc.)
that the platform pulls sensor/health data from.

New monitoring platforms are added by registering a new collector class
(see app/collectors/) and adding its `source_type` string here as data - no
changes to this model, the repository, the service layer, or the API are
required to support a new vendor.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.sensor import Sensor
    from app.models.site import Site
    from app.models.tenant import Tenant


class MonitoringSource(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "monitoring_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_monitoring_sources_tenant_id_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Optional: a source can be scoped to one site (e.g. a site's local PRTG
    # probe) or left null for tenant/global-scoped sources (e.g. an Azure
    # subscription monitoring resources across multiple sites).
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Drives collector selection via the CollectorRegistry. Not an enum column so
    # that new source types never require a migration to add.
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Connection details (base_url, org_id, region, etc). Secrets referenced
    # here should be names/IDs pointing at a real secrets manager in
    # production, not raw credential values - flagged in the schema layer too.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )  # pending | connected | error | disabled
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Connection details for vendor collectors (additive; existing `config`
    # JSONB remains the overflow bucket for anything not modeled here).
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4/IPv6
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(20), nullable=True)  # https | ssh | snmp | wmi | winrm

    # Credentials - always Fernet ciphertext (see SecretCrypto), never plaintext.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    snmp_version: Mapped[str | None] = mapped_column(String(10), nullable=True)  # v1 | v2c | v3
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )

    verify_ssl: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=False, server_default="false"
    )  # NEW, additive: TLS verification for HTTPS-based collectors (PRTG first).
       # Nullable so non-HTTPS collectors (SNMP/SSH/WinRM) can leave it unset;
       # False default matches today's implicit no-verification behavior and
       # the dev PRTG server's self-signed cert (PRTG_DEV_VERIFY_SSL=false).

    # -- Source health state machine (migration d4e5f6a7b8c9; additive) ------
    # Maintained by SourceHealthManager; transitions are audited in the
    # source_health_audits table.
    health_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="healthy", server_default="healthy", index=True
    )  # healthy | degraded | unreachable | ...
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consecutive_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_successful_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    sensors: Mapped[list["Sensor"]] = relationship(
        back_populates="monitoring_source", cascade="all, delete-orphan"
    )

    @property
    def credential_configured(self) -> bool:
        """Whether ANY credential material is stored for this source.

        Derived from the ciphertext columns, so an operator interface can show
        "credential configured" without the API ever returning the value: this is
        the boolean the `MonitoringSourceOut` schema comment sanctions. Adding a
        new credential column means adding it here too.
        """
        return bool(self.api_key_encrypted or self.password_encrypted)

    def __repr__(self) -> str:
        return f"<MonitoringSource id={self.id} type={self.source_type!r} name={self.name!r}>"
