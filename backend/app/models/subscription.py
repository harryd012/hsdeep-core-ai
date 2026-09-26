"""
Subscription model. Links a tenant to a Plan and tracks the billing lifecycle
(trialing, active, past_due, canceled). One tenant has exactly one active
subscription at a time.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.plan import Plan
    from app.models.tenant import Tenant


class Subscription(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (
        # A tenant can have at most one active subscription at a time.
        UniqueConstraint("tenant_id", "status", name="uq_subscriptions_tenant_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # trialing | active | past_due | canceled | incomplete
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="trialing", server_default="trialing", index=True
    )

    # Stripe subscription ID (null until the Stripe integration is configured)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Billing period
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Trial
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Billing interval: month | year
    billing_interval: Mapped[str] = mapped_column(String(10), nullable=False, default="month", server_default="month")

    # When the subscription was canceled (if applicable)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when a downgrade leaves the tenant over the new plan's limits.
    # require_entitlement() checks this flag and blocks NEW resource creation
    # (but never deletes/disabled existing resources). Cleared when the
    # tenant upgrades or reduces usage below the new limits.
    over_limit_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    tenant: Mapped["Tenant"] = relationship()
    plan: Mapped["Plan"] = relationship(back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} tenant_id={self.tenant_id} status={self.status!r}>"