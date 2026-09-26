"""
Billing scheduler jobs.

These run on the SAME AsyncIOScheduler instance as the monitoring collection
tick (see `app/scheduler/scheduler.py` - `start_scheduler()` registers them
alongside `tick`).

  * `expire_trials_job` - every 15 minutes: flip expired trials to past_due,
    and cancel past_due subscriptions whose 3-day grace period has elapsed.
  * `reconcile_subscriptions_job` - daily at 03:00 UTC: compare Stripe's
    active subscriptions against the local subscriptions table and log any
    drift (monitoring only - no auto-correction).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.db import AsyncSessionLocal
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService

logger = logging.getLogger(__name__)


async def expire_trials_job() -> None:
    """Flip expired trials to past_due, then cancel past_due subs past grace.

    Runs every 15 minutes on the shared scheduler. Logs every transition.
    """
    async with AsyncSessionLocal() as db:
        billing = BillingService(
            plans=PlanRepository(db),
            subscriptions=SubscriptionRepository(db),
            db=db,
        )
        now = datetime.now(timezone.utc)

        flipped = await billing.expire_trials(now)
        if flipped:
            logger.info("Trial expiry job: flipped %s expired trial(s) to past_due", flipped)

        canceled = await billing.cancel_past_due_after_grace(now)
        if canceled:
            logger.info("Trial expiry job: canceled %s past_due subscription(s) after grace", canceled)

        await db.commit()


async def reconcile_subscriptions_job() -> None:
    """Daily reconciliation: compare Stripe active subs vs local table.

    Lists active subscriptions in Stripe via the API and compares status /
    period dates against the local subscriptions table. Logs any mismatches
    at WARNING level with tenant_id and both statuses side by side. This is
    monitoring only - it does NOT auto-correct drift.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.stripe_secret_key:
        logger.info("Reconciliation job: STRIPE_SECRET_KEY not set - skipping.")
        return

    try:
        import stripe
    except ImportError:
        logger.warning("Reconciliation job: stripe SDK not installed - skipping.")
        return

    stripe.api_key = settings.stripe_secret_key

    async with AsyncSessionLocal() as db:
        sub_repo = SubscriptionRepository(db)
        local_subs = await sub_repo.list_active()

        # Build a map of stripe_subscription_id -> local subscription
        local_by_stripe_id = {
            s.stripe_subscription_id: s
            for s in local_subs
            if s.stripe_subscription_id
        }

        # Fetch all active subscriptions from Stripe (paginated)
        stripe_subs = []
        params: dict = {"status": "all", "limit": 100}
        while True:
            result = stripe.Subscription.list(**params)
            stripe_subs.extend(result.data)
            if not result.has_more:
                break
            params["starting_after"] = result.data[-1].id

        mismatches = 0
        for s in stripe_subs:
            local = local_by_stripe_id.get(s.id)
            if local is None:
                logger.warning(
                    "Reconciliation: Stripe sub %s (status=%s) has no local subscription row",
                    s.id,
                    s.status,
                )
                mismatches += 1
                continue

            if local.status != s.status:
                logger.warning(
                    "Reconciliation mismatch: tenant_id=%s stripe_sub_id=%s local_status=%s stripe_status=%s",
                    local.tenant_id,
                    s.id,
                    local.status,
                    s.status,
                )
                mismatches += 1

            # Compare period end (if both present)
            if s.current_period_end and local.current_period_end:
                stripe_period_end = datetime.fromtimestamp(s.current_period_end, tz=timezone.utc)
                if abs((stripe_period_end - local.current_period_end).total_seconds()) > 3600:
                    logger.warning(
                        "Reconciliation period mismatch: tenant_id=%s stripe_sub_id=%s "
                        "local_period_end=%s stripe_period_end=%s",
                        local.tenant_id,
                        s.id,
                        local.current_period_end,
                        stripe_period_end,
                    )
                    mismatches += 1

        if mismatches:
            logger.warning("Reconciliation job: %s mismatch(es) found (monitoring only - no auto-correct).", mismatches)
        else:
            logger.info("Reconciliation job: no mismatches found across %s Stripe subscription(s).", len(stripe_subs))
