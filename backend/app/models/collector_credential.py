"""
Collector credential (customer data plane).

The permanent credential a REGISTERED collector uses for its subsequent outbound
requests. Deliberately narrow: it authenticates exactly one collector, is bound
to that collector's tenant, and is revocable. It is NOT a general-purpose auth
system - there is no role, no scope set, no permission model.

APPROVED CREDENTIAL FLOW (customer-entered PRTG credentials):
    customer enters PRTG credentials
      -> HSDEEP encrypts/stores them (SecretCrypto, MonitoringSource columns)
      -> authenticated collector requests its configuration  [this credential]
      -> HTTPS/TLS
      -> collector receives credentials IN MEMORY
      -> collector connects directly to the customer's PRTG

Consequences enforced elsewhere, recorded here so they are not lost:
  * The plaintext credential is returned EXACTLY ONCE, when the collector
    registers. Only its hash is stored here.
  * It is never returned in a normal API response after creation.
  * The runtime holds the customer's PRTG credentials in memory only and never
    writes them to local state.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


class CollectorCredential(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "collector_credentials"
    __table_args__ = (
        # One credential per registered collector. Re-registration rotates it.
        UniqueConstraint("source_id", name="uq_collector_credentials_source_id"),
        UniqueConstraint(
            "credential_hash", name="uq_collector_credentials_credential_hash"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: The registered collector this credential belongs to. The tenant is ALWAYS
    #: read through this row - a collector never supplies a tenant id.
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Hash of the plaintext credential. NEVER the credential itself.
    credential_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Reported by the collector on heartbeat; informational only.
    collector_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    source: Mapped["MonitoringSource"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CollectorCredential source={self.source_id} "
            f"revoked={self.revoked_at is not None}>"
        )
