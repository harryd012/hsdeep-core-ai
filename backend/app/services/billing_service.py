"""
BillingService (Epic A-E: Billing & Entitlements Foundation).

Application-service facade for the subscription system: plan seeding,
subscription lifecycle, and entitlement computation (plan limits vs. current
usage from usage_counters). Routes call this service; repositories run SQL;
the service owns business rules.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.models.plan import Plan
from app.models.sensor import Sensor
from app.models.user import User
from app.models.subscription import Subscription
from app.models.usage import UsageCounter
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default plan catalog (seeded idempotently at startup)
# ---------------------------------------------------------------------------

# Commercial plan catalog (data-driven; seeded/synced idempotently at
# startup). Limits are the single source of truth - the frontend renders
# whatever the backend returns and never hardcodes plan limits.
#
# Metered allowances (api_rate_limit / alert_limit / collector_run_limit)
# are per billing period via usage_counters. collector_limit is the number
# of monitoring SOURCES. Enterprise uses high finite limits (the backend
# enforces finite values) with custom-limits handled via sales.
DEFAULT_PLANS: list[dict] = [
    {
        "name": "Starter",
        "tier": "starter",
        "description": "For small environments getting started with infrastructure monitoring.",
        "price_monthly": 49.0,
        "price_annual": 490.0,
        "device_limit": 25,
        "sensor_limit": 250,
        "api_rate_limit": 50_000,
        "alert_limit": 25_000,
        "collector_run_limit": 5_000,
        "collector_limit": 5,
        "seats": 5,
        "display_order": 1,
        "features_json": {
            "sso": False, "audit_log": False, "sla": False,
            "windows_monitoring": True, "snmp": True,
            "cloud_monitoring": "limited", "ai_rca": True, "msp_features": False,
        },
        "is_public": True,
    },
    {
        "name": "Professional",
        "tier": "pro",
        "description": "For growing IT teams and MSPs that need advanced correlation and automation.",
        "price_monthly": 199.0,
        "price_annual": 1_990.0,
        "device_limit": 250,
        "sensor_limit": 2_500,
        "api_rate_limit": 500_000,
        "alert_limit": 250_000,
        "collector_run_limit": 50_000,
        "collector_limit": 25,
        "seats": 25,
        "display_order": 2,
        "features_json": {
            "sso": False, "audit_log": True, "sla": False,
            "windows_monitoring": True, "snmp": True,
            "cloud_monitoring": True, "ai_rca": True, "msp_features": "limited",
        },
        "is_public": True,
    },
    {
        "name": "Enterprise",
        "tier": "enterprise",
        "description": "For MSP/enterprise/property portfolios: high-scale monitoring, custom limits, SSO, audit, custom SLAs.",
        "price_monthly": 999.0,
        "price_annual": 9_990.0,
        "device_limit": 10_000,
        "sensor_limit": 100_000,
        "api_rate_limit": 5_000_000,
        "alert_limit": 2_500_000,
        "collector_run_limit": 1_000_000,
        "collector_limit": 250,
        "seats": 500,
        "display_order": 3,
        "features_json": {
            "sso": True, "audit_log": True, "sla": True,
            "windows_monitoring": True, "snmp": True,
            "cloud_monitoring": True, "ai_rca": "advanced", "msp_features": "full",
        },
        "is_public": True,
    },
]


class BillingError(Exception):
    """Base for billing failures the route maps to an HTTP error."""


class InvalidTransition(BillingError):
    """Raised when a subscription status transition is not allowed."""


# ---------------------------------------------------------------------------
# Subscription lifecycle state machine (server-side enforced)
# ---------------------------------------------------------------------------
# States: trialing | active | past_due | suspended | canceled | incomplete
# Enforcement: suspended/canceled subscriptions are excluded by
# SubscriptionRepository.get_active(), so require_entitlement() /
# BillingService.get_entitlements() deny operations for them automatically -
# no second enforcement gate is introduced.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "trialing": frozenset({"active", "past_due", "canceled", "suspended"}),
    "active": frozenset({"past_due", "canceled", "suspended"}),
    "past_due": frozenset({"active", "suspended", "canceled"}),
    "suspended": frozenset({"active", "canceled"}),  # reactivate after payment
    "incomplete": frozenset({"active", "canceled"}),
    "canceled": frozenset(),  # terminal
}


class PlanNotFound(BillingError):
    pass


class SubscriptionNotFound(BillingError):
    pass


class BillingService:
    def __init__(
        self,
        *,
        plans: PlanRepository,
        subscriptions: SubscriptionRepository,
        db: AsyncSession,
    ):
        self.plans = plans
        self.subscriptions = subscriptions
        self.db = db

    # -- Plan seeding ------------------------------------------------------

    async def seed_default_plans(self) -> list[Plan]:
        """Idempotently create AND sync the DEFAULT_PLANS catalog.

        New tiers are created; existing tiers have their catalog fields
        (name, pricing, limits, features, ordering) synced to the current
        catalog so plan data stays data-driven across deploys. Subscriptions
        referencing a plan are NEVER touched - only the plan row updates.
        Stripe price IDs and is_public are preserved for existing plans.
        """
        created: list[Plan] = []
        synced: list[Plan] = []
        for spec in DEFAULT_PLANS:
            existing = await self.plans.get_by_tier(spec["tier"])
            if existing is None:
                plan = await self.plans.create(**spec)
                created.append(plan)
                continue
            changed = False
            for field, value in spec.items():
                if field in ("stripe_price_id_monthly", "stripe_price_id_annual"):
                    continue
                if field == "is_public":
                    continue
                current = getattr(existing, field, None)
                if current != value:
                    setattr(existing, field, value)
                    changed = True
            if changed:
                synced.append(existing)
        if synced:
            await self.db.flush()
            logger.info("Plan catalog synced for tiers: %s", [p.tier for p in synced])
        return created

    # -- Subscription lifecycle --------------------------------------------

    async def create_trial_subscription(
        self,
        *,
        tenant_id: uuid.UUID,
        plan_tier: str = "starter",
        trial_days: int = 14,
    ) -> Subscription:
        """Create a trial subscription for a new tenant on the given plan."""
        plan = await self.plans.get_by_tier(plan_tier)
        if plan is None:
            raise PlanNotFound(f"Plan tier {plan_tier!r} not found")

        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(days=trial_days)
        return await self.subscriptions.create(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="trialing",
            current_period_start=now,
            current_period_end=trial_end,
            trial_ends_at=trial_end,
            billing_interval="month",
        )

    async def get_active_subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return await self.subscriptions.get_active(tenant_id)

    async def get_subscription_with_plan(self, tenant_id: uuid.UUID) -> Subscription | None:
        return await self.subscriptions.get_active(tenant_id)

    # -- Trial expiry ------------------------------------------------------

    async def expire_trials(self, now: datetime | None = None) -> int:
        """Flip expired trials to past_due (3-day grace before canceled).

        Called by the scheduler job. Finds subscriptions still `trialing`
        whose trial_ends_at is in the past and flips them to `past_due`.
        A separate job (or the same one, after the grace period) flips
        past_due subscriptions older than 3 days to `canceled`.
        """
        now = now or datetime.now(timezone.utc)
        expired = await self.subscriptions.list_expired_trials(now)
        flipped = 0
        for sub in expired:
            await self.subscriptions.update_status(sub, "past_due")
            flipped += 1
            logger.info(
                "Trial expired: subscription_id=%s tenant_id=%s trial_ends_at=%s -> past_due",
                sub.id,
                sub.tenant_id,
                sub.trial_ends_at,
            )
        return flipped

    async def cancel_past_due_after_grace(self, now: datetime | None = None) -> int:
        """Suspend past_due subscriptions whose grace period (3 days) elapsed.

        Simulated non-payment handling (no payment gateway yet): a past_due
        subscription that stays unpaid past the grace period is SUSPENDED,
        which blocks all entitled operations via require_entitlement()
        (get_active() excludes suspended). Reactivation is an explicit
        transition to active once payment is resolved."""
        now = now or datetime.now(timezone.utc)
        grace_cutoff = now - timedelta(days=3)
        stmt = select(Subscription).where(
            Subscription.status == "past_due",
            Subscription.trial_ends_at.is_not(None),
            Subscription.trial_ends_at < grace_cutoff,
            Subscription.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        past_due = list(result.scalars().unique().all())
        suspended = 0
        for sub in past_due:
            await self.transition_status(
                sub,
                "suspended",
                actor_email="system:billing",
                reason="Non-payment grace period elapsed",
            )
            suspended += 1
            logger.info(
                "Trial grace period elapsed: subscription_id=%s tenant_id=%s -> suspended",
                sub.id,
                sub.tenant_id,
            )
        return suspended

    # -- Lifecycle state machine -------------------------------------------

    async def transition_status(
        self,
        sub: Subscription,
        new_status: str,
        *,
        actor_id: uuid.UUID | None = None,
        actor_email: str | None = None,
        reason: str | None = None,
    ) -> Subscription:
        """Apply a validated status transition and audit it.

        Raises InvalidTransition when the move is not in ALLOWED_TRANSITIONS.
        The caller owns the commit (route/scheduler convention).
        """
        current = sub.status
        if new_status == current:
            return sub
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if new_status not in allowed:
            raise InvalidTransition(
                f"Cannot transition subscription from {current!r} to {new_status!r}"
            )
        old_status = current
        await self.subscriptions.update_status(sub, new_status)
        await AuditService(self.db).record(
            tenant_id=sub.tenant_id,
            action="subscription.status_changed",
            actor_id=actor_id,
            actor_email=actor_email,
            entity_type="subscription",
            entity_id=sub.id,
            description=f"Subscription {old_status!r} -> {new_status!r}"
            + (f" ({reason})" if reason else ""),
            old_values={"status": old_status},
            new_values={"status": new_status},
        )
        logger.info(
            "Subscription %s transitioned %s -> %s (actor=%s)",
            sub.id, old_status, new_status, actor_email,
        )
        return sub

    # -- Plan changes -------------------------------------------------------

    async def change_plan(
        self,
        sub: Subscription,
        new_plan_tier: str,
        *,
        actor_id: uuid.UUID | None = None,
        actor_email: str | None = None,
    ) -> Subscription:
        """Upgrade/downgrade the tenant's plan on the existing subscription.

        Downgrades reuse the existing over-limit machinery: if the tenant is
        over the NEW plan's limits, check_downgrade_over_limit() detects it
        and set_over_limit_blocked() flags the subscription so
        require_entitlement() blocks NEW resource creation (existing
        resources are never deleted).

        Every change is audited with old/new plan tiers. Caller commits.
        """
        new_plan = await self.plans.get_by_tier(new_plan_tier)
        if new_plan is None:
            raise PlanNotFound(f"Plan tier {new_plan_tier!r} not found")
        old_plan = sub.plan
        if old_plan is None or new_plan.id == old_plan.id:
            return sub  # no-op

        is_downgrade = new_plan.price_monthly < old_plan.price_monthly
        sub.plan_id = new_plan.id
        sub.plan = new_plan  # keep the relationship in sync so the
        # over-limit check below evaluates against the NEW plan's limits
        await self.db.flush()

        over_limit = False
        if is_downgrade:
            over_limit = await self.check_downgrade_over_limit(sub)
            if over_limit != sub.over_limit_blocked:
                await self.set_over_limit_blocked(sub, over_limit)

        await AuditService(self.db).record(
            tenant_id=sub.tenant_id,
            action="subscription.plan_changed",
            actor_id=actor_id,
            actor_email=actor_email,
            entity_type="subscription",
            entity_id=sub.id,
            description=(
                f"Plan changed {old_plan.name!r} -> {new_plan.name!r}"
                + (" (over-limit blocked)" if over_limit else "")
            ),
            old_values={"plan_tier": old_plan.tier, "plan_name": old_plan.name},
            new_values={
                "plan_tier": new_plan.tier,
                "plan_name": new_plan.name,
                "over_limit_blocked": over_limit,
            },
        )
        await self.db.refresh(sub)
        return sub

    # -- Downgrade-over-limit handling -------------------------------------

    async def check_downgrade_over_limit(self, sub: Subscription) -> bool:
        """After a plan change, check if the tenant is over the NEW plan's limits.

        Returns True if the tenant is over the new plan's limits (and the
        subscription should be flagged over_limit_blocked), False otherwise.
        Does NOT block the downgrade itself - that's a Stripe-side action
        already completed. This only sets a flag that require_entitlement()
        checks to block NEW resource creation.
        """
        plan = sub.plan
        if plan is None:
            return False

        device_count = await self._count(Device, sub.tenant_id)
        sensor_count = await self._count(Sensor, sub.tenant_id)
        collector_count = await self._count(MonitoringSource, sub.tenant_id)

        over = (
            device_count > plan.device_limit
            or sensor_count > plan.sensor_limit
            or collector_count > plan.collector_limit
        )
        return over

    async def set_over_limit_blocked(self, sub: Subscription, blocked: bool) -> Subscription:
        """Set/clear the over-limit-blocked flag on a subscription."""
        return await self.subscriptions.update_over_limit_blocked(sub, blocked)

    # -- Entitlements ------------------------------------------------------

    async def get_entitlements(self, tenant_id: uuid.UUID) -> dict:
        """Compute plan limits + current usage for a tenant.

        Returns a dict matching the EntitlementsOut schema shape.
        """
        sub = await self.subscriptions.get_active(tenant_id)
        if sub is None or sub.plan is None:
            raise SubscriptionNotFound("No active subscription for this tenant")

        plan = sub.plan

        # Current usage counts (live DB queries, not usage_counters, for
        # device/sensor/collector limits - these are inventory counts).
        device_count = await self._count(Device, tenant_id)
        sensor_count = await self._count(Sensor, tenant_id)
        collector_count = await self._count(MonitoringSource, tenant_id)

        # Seats: active users in the tenant vs. the plan's seat count.
        seat_count = await self._count_active_users(tenant_id)

        # Metered usage in the current billing period (from usage_counters).
        api_calls = await self._sum_usage(tenant_id, "api.call", sub.current_period_start)
        alerts_used = await self._sum_usage(tenant_id, "alert.ingested", sub.current_period_start)
        collector_runs = await self._sum_usage(tenant_id, "collector.run", sub.current_period_start)

        limits = [
            self._limit("devices", "Devices", plan.device_limit, device_count),
            self._limit("sensors", "Sensors", plan.sensor_limit, sensor_count),
            # NOTE: resource key stays "collectors" - require_entitlement(
            # "collectors") in routes/monitoring.py depends on it. The label
            # is what the UI shows ("Sources").
            self._limit("collectors", "Sources", plan.collector_limit, collector_count),
            self._limit("seats", "Seats", plan.seats, seat_count),
            self._limit("api_calls", "API Requests", plan.api_rate_limit, api_calls),
            self._limit("alerts", "Alerts", plan.alert_limit, alerts_used),
            self._limit("collector_runs", "Collector Runs", plan.collector_run_limit, collector_runs),
        ]

        return {
            "tenant_id": tenant_id,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "plan_tier": plan.tier,
            "subscription_status": sub.status,
            "billing_interval": sub.billing_interval,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "trial_ends_at": sub.trial_ends_at,
            "limits": limits,
            "over_limit": any(l["over_limit"] for l in limits),
            "over_limit_blocked": sub.over_limit_blocked,
        }

    async def check_limit(
        self, tenant_id: uuid.UUID, resource: str
    ) -> tuple[bool, dict | None]:
        """Check whether a tenant is at/over a specific resource limit.

        Returns (allowed, limit_info). If allowed is False, limit_info
        contains the upgrade CTA payload for a 402/429 response.
        """
        entitlements = await self.get_entitlements(tenant_id)
        for limit in entitlements["limits"]:
            if limit["resource"] == resource:
                if limit["over_limit"]:
                    return False, {
                        "resource": resource,
                        "limit": limit["limit"],
                        "used": limit["used"],
                        "plan": entitlements["plan_name"],
                        "plan_tier": entitlements["plan_tier"],
                        "message": f"Your {entitlements['plan_name']} plan allows {limit['limit']} {resource}. "
                        f"You are using {limit['used']}. Upgrade to add more.",
                    }
                return True, None
        return True, None

    # -- Helpers -----------------------------------------------------------

    async def _count(self, model, tenant_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(model).where(
            model.tenant_id == tenant_id, model.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return int(result.scalar_one())

    async def _count_active_users(self, tenant_id: uuid.UUID) -> int:
        """Count non-deleted, active users in a tenant (for the seats limit)."""
        stmt = select(func.count()).select_from(User).where(
            User.tenant_id == tenant_id,
            User.is_deleted.is_(False),
            User.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        return int(result.scalar_one())

    async def _sum_usage(
        self, tenant_id: uuid.UUID, event_type: str, since: datetime | None
    ) -> int:
        stmt = select(func.coalesce(func.sum(UsageCounter.count), 0)).where(
            UsageCounter.tenant_id == tenant_id,
            UsageCounter.event_type == event_type,
        )
        if since is not None:
            stmt = stmt.where(UsageCounter.bucket_start >= since)
        result = await self.db.execute(stmt)
        return int(result.scalar_one())

    @staticmethod
    def _limit(resource: str, label: str, limit: int, used: int) -> dict:
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        return {
            "resource": resource,
            "label": label,
            "limit": limit,
            "used": used,
            "pct": pct,
            "over_limit": used >= limit,
        }