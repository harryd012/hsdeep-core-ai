"""
Device Action Plane models (Phase 1).

Two additive tables:

  * DeviceActionRequest - one row per requested device action: the full
    WHY/WHO/WHAT/WHEN/WHERE/RESULT audit spine (tenant, requester, device,
    action, params, policy decision, approval link, execution link,
    provider result, verification result, correlation id).
    REUSES the existing engines instead of duplicating them:
      - approval_request_id -> approval_requests (ApprovalRequest engine)
      - execution_job_id    -> execution_jobs (ExecutionJob engine)

  * DeviceCapability - the per-device capability model (1:1 with devices).
    Tri-state booleans: True / False / NULL (= unknown, never guessed).
    Rows are 'explicit' (operator/collector asserted) or 'derived'
    (resolver inferred from the device's monitoring source / vendor).

No credential material is ever stored here - the target's credentials
live (Fernet-encrypted) on MonitoringSource and are decrypted per
execution by the capability resolver.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device


# Request lifecycle:
#   pending -> policy_denied (terminal)
#           -> awaiting_approval -> denied (terminal)
#                              -> approved -> executing -> succeeded/failed
#                                           -> verified / verification_failed
ACTION_REQUEST_STATUSES = (
    "pending", "policy_denied", "awaiting_approval", "denied",
    "approved", "executing", "succeeded", "failed",
    "verified", "verification_failed",
)


class DeviceActionRequest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "device_action_requests"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)

    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )

    # WHY: the recommendation/evidence that motivated this action (AI or
    # human). Kept as structured context, not a second recommendation engine.
    motivation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # WHO requested / resolved (human decision).
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional operational links.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    execution_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Correlation id threading request -> approval -> execution -> audit.
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Policy decision snapshot (what the policy engine said, and when).
    policy_decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # RESULT: provider output + verification outcome (never secrets).
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    verification: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    device: Mapped["Device"] = relationship()

    def __repr__(self) -> str:
        return f"<DeviceActionRequest id={self.id} action={self.action!r} status={self.status!r}>"


class DeviceCapability(Base, UUIDPKMixin, TimestampMixin):
    """Per-device capability model - 1:1 with devices.

    All supports_* flags are tri-state: True / False / NULL(=unknown).
    NULL is honest ("we don't know"), never silently coerced to True.
    `source` records how the row came to be: 'explicit' (operator or
    collector asserted) or 'derived' (resolver inferred from the linked
    monitoring source / vendor metadata).

    NOTE: monitoring credentials NEVER imply configuration privileges -
    the capability row is the only grant for action execution, and
    derived rows only ever assert READ-ONLY transport capabilities
    (ping/snmp/rest read), never supports_config / supports_reboot.
    """
    __tablename__ = "device_capabilities"
    __table_args__ = (
        UniqueConstraint("device_id", name="uq_device_capabilities_device_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="derived", server_default="derived"
    )

    supports_ping: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_snmp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_ssh: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_rest: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_config: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_reboot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_firmware: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_backup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_rollback: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Observed device facts (from monitoring), for the resolver/UI.
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    management_address: Mapped[str | None] = mapped_column(String(255), nullable=True)

    device: Mapped["Device"] = relationship()

    def __repr__(self) -> str:
        return f"<DeviceCapability device_id={self.device_id} source={self.source!r}>"

