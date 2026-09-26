"""
Repository Pattern: direct SQLAlchemy access for tenant-scoped Subscription rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.subscription import Subscription


class SubscriptionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _with_plan(self, stmt):
        return stmt.options(selectinload(Subscription.plan))

    async def get_active(self, tenant_id: uuid.UUID) -> Subscription | None:
        """Return the tenant's active (or trialing) subscription."""
        stmt = self._with_plan(
            select(Subscription).where(
                Subscription.tenant_id == tenant_id,
                Subscription.status.in_(["trialing", "active", "past_due"]),
                Subscription.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_current(self, tenant_id: uuid.UUID) -> Subscription | None:
        """Return the tenant's most recent subscription regardless of status
        (used by the customer-facing subscription view so a suspended/canceled
        tenant can still SEE their status, even though operations are denied)."""
        stmt = self._with_plan(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id, Subscription.is_deleted.is_(False))
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all_with_tenants(self) -> list[Subscription]:
        """Platform-admin: every tenant's latest subscription with tenant and
        plan eagerly loaded (for the customer management list)."""
        from app.models.tenant import Tenant

        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.plan), selectinload(Subscription.tenant))
            .join(Tenant, Tenant.id == Subscription.tenant_id)
            .where(Subscription.is_deleted.is_(False), Tenant.is_deleted.is_(False))
            .order_by(Subscription.created_at.desc())
        )
        result = await self.db.execute(stmt)
        # One row per tenant (latest subscription wins).
        seen: dict[uuid.UUID, Subscription] = {}
        for sub in result.scalars().unique().all():
            seen.setdefault(sub.tenant_id, sub)
        return list(seen.values())

    async def get_by_id(self, subscription_id: uuid.UUID, tenant_id: uuid.UUID) -> Subscription | None:
        stmt = self._with_plan(
            select(Subscription).where(
                Subscription.id == subscription_id,
                Subscription.tenant_id == tenant_id,
                Subscription.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Subscription | None:
        stmt = self._with_plan(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_subscription_id,
                Subscription.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Subscription]:
        stmt = self._with_plan(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id, Subscription.is_deleted.is_(False))
            .order_by(Subscription.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        plan_id: uuid.UUID,
        status: str = "trialing",
        stripe_subscription_id: str | None = None,
        stripe_customer_id: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        trial_ends_at: datetime | None = None,
        billing_interval: str = "month",
    ) -> Subscription:
        sub = Subscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=status,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=stripe_customer_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            trial_ends_at=trial_ends_at,
            billing_interval=billing_interval,
        )
        self.db.add(sub)
        await self.db.flush()
        await self.db.refresh(sub)
        return sub

    async def update_status(self, sub: Subscription, status: str) -> Subscription:
        sub.status = status
        if status == "canceled":
            sub.canceled_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(sub)
        return sub

    async def update_stripe_fields(
        self,
        sub: Subscription,
        *,
        stripe_subscription_id: str | None = None,
        stripe_customer_id: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
    ) -> Subscription:
        if stripe_subscription_id is not None:
            sub.stripe_subscription_id = stripe_subscription_id
        if stripe_customer_id is not None:
            sub.stripe_customer_id = stripe_customer_id
        if current_period_start is not None:
            sub.current_period_start = current_period_start
        if current_period_end is not None:
            sub.current_period_end = current_period_end
        await self.db.flush()
        await self.db.refresh(sub)
        return sub

    async def update_over_limit_blocked(self, sub: Subscription, blocked: bool) -> Subscription:
        """Set/clear the over-limit-blocked flag (downgrade-over-limit)."""
        sub.over_limit_blocked = blocked
        await self.db.flush()
        await self.db.refresh(sub)
        return sub

    async def list_expired_trials(self, now: datetime) -> list[Subscription]:
        """Return subscriptions still `trialing` whose trial_ends_at is in the past."""
        stmt = self._with_plan(
            select(Subscription).where(
                Subscription.status == "trialing",
                Subscription.trial_ends_at.is_not(None),
                Subscription.trial_ends_at < now,
                Subscription.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def list_active(self) -> list[Subscription]:
        """Return all non-canceled subscriptions (for reconciliation)."""
        stmt = self._with_plan(
            select(Subscription).where(
                Subscription.status.in_(["trialing", "active", "past_due", "incomplete"]),
                Subscription.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())