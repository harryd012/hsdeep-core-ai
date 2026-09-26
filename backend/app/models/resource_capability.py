"""
ResourceCapability model - the persisted, provider-neutral capability registry.

ONE ROW PER (resource, capability). This is deliberately a row-per-capability
table rather than N boolean columns: the capability catalog has ~47 entries and
grows with every milestone, and the spec explicitly forbids "hundreds of
vendor-specific columns".

Relationship to the EXISTING capability table
---------------------------------------------
``app/models/device_action.py`` already owns ``DeviceCapability``, the
per-device ACTION capability model (supports_ping / snmp / ssh / rest /
config / reboot / firmware / backup / rollback) consumed by the Action Plane
and its capability resolver. That table is about "what can be DONE to this
device" and is a 1:1 boolean matrix.

``ResourceCapability`` is about "what can be OBSERVED about this resource":
telemetry/inventory capabilities such as cpu, memory, wireless, bgp, vpn.
The two answer different questions and neither replaces the other; a device
may be observable over SNMP (here) while not being configurable (there).

Tri-state support
-----------------
``support`` is ``supported`` | ``unsupported`` | ``unknown``. ``unknown`` is
honest ("we have not evaluated this") and is never silently coerced into
either of the other two. Whenever support is not ``supported`` the row MUST
carry a ``reason`` - enforced by a CHECK constraint so no code path can
persist an unexplained "no".
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant


class ResourceCapability(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "resource_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "resource_id", "capability", name="uq_resource_capabilities_resource_capability"
        ),
        Index("ix_resource_capabilities_tenant_capability", "tenant_id", "capability"),
        Index("ix_resource_capabilities_tenant_support", "tenant_id", "support"),
        CheckConstraint(
            "support IN ('supported', 'unsupported', 'unknown')",
            name="ck_resource_capabilities_support",
        ),
        CheckConstraint(
            "support = 'supported' OR reason IS NOT NULL",
            name="ck_resource_capabilities_reason_required",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The provider that produced this assertion (collector source token).
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    capability: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    #: supported | unsupported | unknown
    support: Mapped[str] = mapped_column(String(20), nullable=False)
    #: collected | empty | unsupported | unavailable | failed | stale |
    #: not_collected | not_available
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_collected", server_default="not_collected"
    )
    #: Explicit reason code whenever support != 'supported'.
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: collector | operator | derived
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="collector", server_default="collector"
    )
    #: When the assertion was made (distinct from created_at/updated_at so an
    #: unchanged row's assertion age is still visible).
    asserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Non-secret evidence only (counts, protocol, sensor name).
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()
    resource: Mapped["Device"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<ResourceCapability resource_id={self.resource_id} "
            f"capability={self.capability!r} support={self.support!r}>"
        )
