"""
Plan model. A subscription tier (Starter, Pro, Enterprise) that defines the
limits and features a tenant is entitled to. Plans are global (not
tenant-scoped) - every tenant references one of the seeded plans.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.subscription import Subscription


class Plan(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "plans"

    # Human-readable name (Starter, Pro, Enterprise)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    # Machine-readable tier key: starter | pro | enterprise
    tier: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pricing (USD, per month / per year)
    price_monthly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    price_annual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    # Resource limits
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sensor_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    api_rate_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collector_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Metered event allowances per billing period (usage_counters based).
    # 0 means "no metered allowance configured" - the limit is not enforced.
    alert_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    collector_run_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Feature flags (JSON object, e.g. {"sso": true, "audit_log": true})
    features_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # Whether this plan is available for self-service signup
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Catalog ordering (lower = shown first) and trial eligibility
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    trial_eligible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Stripe Price IDs (set once the Stripe integration is configured)
    stripe_price_id_monthly: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_price_id_annual: Mapped[str | None] = mapped_column(String(255), nullable=True)

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Plan id={self.id} tier={self.tier!r} name={self.name!r}>"