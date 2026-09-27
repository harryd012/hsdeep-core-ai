"""
Stripe Webhook Handler (Epic B).

Receives Stripe events and syncs the subscriptions table:
  * checkout.session.completed -> link Stripe customer/subscription to tenant
  * invoice.paid -> mark subscription active, update billing period
  * invoice.payment_failed -> flip subscription to past_due (dunning)
  * customer.subscription.updated -> sync plan/status/period changes
  * customer.subscription.deleted -> mark subscription canceled

The webhook endpoint is intentionally unauthenticated (Stripe signs the
payload with STRIPE_WEBHOOK_SECRET). We verify the signature before
processing any event.

Idempotency: every successfully processed event_id is recorded in
`processed_stripe_events`. Stripe retries webhooks on any non-2xx or
timeout, so the same event can arrive multiple times. A duplicate event
is a no-op (returns 200 immediately without touching the DB).

Error handling: unknown event types are logged and ignored (return 200 -
Stripe should not retry). Any processing failure that Stripe SHOULD retry
(e.g. DB write failure) returns 500 so Stripe retries the event.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.repositories.plan_repository import PlanRepository
from app.repositories.processed_stripe_event_repository import ProcessedStripeEventRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.audit_service import AuditService
from app.services.billing_service import BillingService, InvalidTransition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/webhook", tags=["billing"])

# Event types we know how to handle. Anything else is logged and ignored
# (Stripe should NOT retry unknown event types).
_HANDLED_EVENT_TYPES = frozenset({
    "checkout.session.completed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})


def get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


@router.post("")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    billing: BillingService = Depends(get_billing_service),
):
    """Handle Stripe webhook events."""
    # Read the settings per request: a module-level snapshot froze the webhook
    # secret at import time, so a secret set later (or a cleared settings cache,
    # as several test modules do) left this route answering 501 "not
    # configured" instead of verifying the signature it had just been given.
    settings = get_settings()

    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=501,
            detail="Stripe webhook is not configured. Set STRIPE_WEBHOOK_SECRET.",
        )

    try:
        import stripe
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Stripe SDK not installed. Run `pip install stripe`.",
        )

    stripe.api_key = settings.stripe_secret_key

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError as exc:
        logger.warning(
            "Stripe webhook rejected: invalid payload (sig_header=%s)",
            sig_header[:32] if sig_header else "(none)",
        )
        await _audit_webhook_rejected(db, event_type=None, reason="invalid_payload")
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        logger.warning(
            "Stripe webhook rejected: signature verification failed (sig_header=%s)",
            sig_header[:32] if sig_header else "(none)",
        )
        await _audit_webhook_rejected(db, event_type=None, reason="invalid_signature")
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    event_id = event["id"]
    event_type = event["type"]
    data = event["data"]["object"]

    # ---- Idempotency check: skip if this event was already processed ----
    processed_repo = ProcessedStripeEventRepository(db)
    if await processed_repo.exists(event_id):
        logger.info(
            "Stripe webhook: duplicate event %s (%s) - skipping (already processed)",
            event_id,
            event_type,
        )
        return {"received": True, "duplicate": True}

    # ---- Unknown event type: log and ignore (Stripe should NOT retry) ----
    if event_type not in _HANDLED_EVENT_TYPES:
        logger.info(
            "Stripe webhook: ignoring unhandled event type=%s event_id=%s",
            event_type,
            event_id,
        )
        return {"received": True, "ignored": True}

    # ---- Process the event ----
    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(data, billing, db)
        elif event_type == "invoice.paid":
            await _handle_invoice_paid(data, billing, db)
        elif event_type == "invoice.payment_failed":
            await _handle_payment_failed(data, billing, db)
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(data, billing, db)
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(data, billing, db)

        # Record the event as processed ONLY after the handler succeeded.
        # If the handler raised, we roll back and return 500 so Stripe retries.
        await processed_repo.create(event_id)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - must return 500 for Stripe to retry
        await db.rollback()
        try:
            await _audit_webhook_rejected(db, event_type=event_type, reason="processing_failure")
            await db.commit()
        except Exception:  # noqa: BLE001 - audit must never mask the 500
            pass
        logger.error(
            "Stripe webhook processing failed: event_id=%s event_type=%s error=%s",
            event_id,
            event_type,
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    logger.info(
        "Stripe webhook processed: event_id=%s event_type=%s",
        event_id,
        event_type,
    )
    return {"received": True}


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

async def _handle_checkout_completed(data: dict, billing: BillingService, db: AsyncSession) -> None:
    """Link the Stripe customer + subscription to the tenant AND apply the
    plan/billing interval selected at checkout time.

    The plan tier and billing_interval were resolved SERVER-SIDE when the
    checkout session was created (never from the browser) and stored in the
    session metadata. Applying them here makes activation independent of
    Stripe event ordering: invoice.paid / customer.subscription.updated may
    arrive before or after this event, but the paid plan is always applied
    exactly once, through the existing change_plan() state machine (with
    downgrade-over-limit handling) - no parallel entitlement path.
    """
    tenant_id = _extract_tenant_id(data)
    if tenant_id is None:
        logger.warning("checkout.session.completed: no tenant_id in metadata")
        return

    sub = await billing.subscriptions.get_active(tenant_id)
    if sub is None:
        logger.warning("checkout.session.completed: no active subscription for tenant %s", tenant_id)
        return

    await billing.subscriptions.update_stripe_fields(
        sub,
        stripe_customer_id=data.get("customer"),
        stripe_subscription_id=data.get("subscription"),
    )

    # Apply the server-resolved plan tier (idempotent: change_plan() is a
    # no-op when the tier already matches).
    metadata = data.get("metadata") or {}
    plan_tier = metadata.get("plan_tier")
    if plan_tier:
        try:
            await billing.change_plan(sub, plan_tier, actor_email="system:stripe")
            await AuditService(db).record(
                tenant_id=sub.tenant_id,
                action="billing.subscription_activated",
                actor_email="system:stripe",
                entity_type="subscription",
                entity_id=sub.id,
                description=f"Checkout completed; plan {plan_tier!r} applied via webhook",
                new_values={"plan_tier": plan_tier},
            )
        except Exception as exc:  # noqa: BLE001 - unknown tier must not kill the link
            logger.warning(
                "checkout.session.completed: could not apply plan %r: %s", plan_tier, exc
            )

    # Apply the server-resolved billing interval (month/year only).
    billing_interval = metadata.get("billing_interval")
    if billing_interval in ("month", "year") and sub.billing_interval != billing_interval:
        sub.billing_interval = billing_interval
        await db.flush()
    await AuditService(db).record(
        tenant_id=sub.tenant_id,
        action="billing.checkout_completed",
        actor_email="system:stripe",
        entity_type="subscription",
        entity_id=sub.id,
        description="Stripe checkout completed; customer/subscription linked",
        new_values={
            "stripe_customer_id": data.get("customer"),
            "stripe_subscription_id": data.get("subscription"),
        },
    )
    logger.info("Linked Stripe customer %s to tenant %s", data.get("customer"), tenant_id)


async def _handle_invoice_paid(data: dict, billing: BillingService, db: AsyncSession) -> None:
    """Mark the subscription active and update the billing period."""
    stripe_sub_id = data.get("subscription")
    if not stripe_sub_id:
        return

    sub = await billing.subscriptions.get_by_stripe_subscription_id(stripe_sub_id)
    if sub is None:
        logger.warning("invoice.paid: no subscription for Stripe sub %s", stripe_sub_id)
        return

    period_start = _parse_timestamp(data.get("period_start"))
    period_end = _parse_timestamp(data.get("period_end"))

    await billing.subscriptions.update_stripe_fields(
        sub,
        current_period_start=period_start,
        current_period_end=period_end,
    )
    await _sync_status(billing, db, sub, "active", stripe_sub_id)
    logger.info("Marked subscription %s active", stripe_sub_id)


async def _handle_payment_failed(data: dict, billing: BillingService, db: AsyncSession) -> None:
    """Flip the subscription to past_due (dunning)."""
    stripe_sub_id = data.get("subscription")
    if not stripe_sub_id:
        return

    sub = await billing.subscriptions.get_by_stripe_subscription_id(stripe_sub_id)
    if sub is None:
        logger.warning("invoice.payment_failed: no subscription for Stripe sub %s", stripe_sub_id)
        return

    await _sync_status(billing, db, sub, "past_due", stripe_sub_id)
    await AuditService(db).record(
        tenant_id=sub.tenant_id,
        action="billing.payment_failed",
        actor_email="system:stripe",
        entity_type="subscription",
        entity_id=sub.id,
        description="Stripe invoice payment failed; subscription moved to past_due",
    )
    logger.warning("Subscription %s is now past_due", stripe_sub_id)


async def _handle_subscription_updated(data: dict, billing: BillingService, db: AsyncSession) -> None:
    """Sync plan/status/period changes from Stripe."""
    stripe_sub_id = data.get("id")
    if not stripe_sub_id:
        return

    sub = await billing.subscriptions.get_by_stripe_subscription_id(stripe_sub_id)
    if sub is None:
        logger.warning("customer.subscription.updated: no subscription for Stripe sub %s", stripe_sub_id)
        return

    # Stripe -> HSDEEP status mapping (documented decision):
    #   trialing/active/past_due/canceled/incomplete map 1:1.
    #   unpaid          -> past_due   (dunning: same operational semantics -
    #                    payment overdue but recoverable)
    #   incomplete_expired -> canceled (checkout never completed; terminal)
    #   paused          -> ignored for now (no HSDEEP equivalent; do not
    #                      silently invent a new state)
    _STRIPE_STATUS_MAP = {
        "trialing": "trialing",
        "active": "active",
        "past_due": "past_due",
        "unpaid": "past_due",
        "canceled": "canceled",
        "incomplete": "incomplete",
        "incomplete_expired": "canceled",
    }
    status_value = data.get("status")
    mapped = _STRIPE_STATUS_MAP.get(status_value)
    if mapped is not None:
        await _sync_status(billing, db, sub, mapped, stripe_sub_id)

    # Plan changes made in the Stripe Customer Portal: resolve the price ID
    # back to the internal plan catalog and apply change_plan() so downgrade
    # over-limit handling runs.
    plan_tier = await _tier_for_price(billing, data)
    if plan_tier is not None:
        try:
            await billing.change_plan(
                sub, plan_tier, actor_email="system:stripe"
            )
            await AuditService(db).record(
                tenant_id=sub.tenant_id,
                action="billing.plan_changed_via_stripe",
                actor_email="system:stripe",
                entity_type="subscription",
                entity_id=sub.id,
                description=f"Plan changed via Stripe to {plan_tier!r}",
                new_values={"plan_tier": plan_tier},
            )
        except Exception as exc:  # noqa: BLE001 - unknown tier must not kill the sync
            logger.warning(
                "customer.subscription.updated: could not apply plan %r: %s",
                plan_tier,
                exc,
            )

    period_start = _parse_timestamp(data.get("current_period_start"))
    period_end = _parse_timestamp(data.get("current_period_end"))
    await billing.subscriptions.update_stripe_fields(
        sub,
        current_period_start=period_start,
        current_period_end=period_end,
    )
    logger.info("Synced subscription %s status=%s", stripe_sub_id, status_value)


async def _handle_subscription_deleted(data: dict, billing: BillingService, db: AsyncSession) -> None:
    """Mark the subscription canceled."""
    stripe_sub_id = data.get("id")
    if not stripe_sub_id:
        return

    sub = await billing.subscriptions.get_by_stripe_subscription_id(stripe_sub_id)
    if sub is None:
        logger.warning("customer.subscription.deleted: no subscription for Stripe sub %s", stripe_sub_id)
        return

    await _sync_status(billing, db, sub, "canceled", stripe_sub_id)
    await AuditService(db).record(
        tenant_id=sub.tenant_id,
        action="billing.subscription_canceled_via_stripe",
        actor_email="system:stripe",
        entity_type="subscription",
        entity_id=sub.id,
        description="Stripe subscription deleted; HSDEEP subscription canceled",
    )
    logger.info("Subscription %s canceled", stripe_sub_id)


async def _sync_status(
    billing: BillingService,
    db: AsyncSession,
    sub,
    new_status: str,
    stripe_sub_id: str,
) -> None:
    """Apply a status change through the BillingService state machine.

    Never writes status directly: transition_status() validates the move
    against ALLOWED_TRANSITIONS and audits it. Invalid transitions (e.g.
    Stripe reporting a state the local machine has already passed) are
    logged and skipped rather than corrupting local state.
    """
    if sub.status == new_status:
        return
    try:
        await billing.transition_status(
            sub, new_status, actor_email="system:stripe",
            reason="Stripe webhook event",
        )
    except InvalidTransition as exc:
        logger.warning(
            "Stripe webhook: skipping invalid transition for %s: %s",
            stripe_sub_id,
            exc,
        )


async def _tier_for_price(billing: BillingService, data: dict) -> str | None:
    """Resolve the subscription's Stripe price ID back to an internal tier."""
    items = data.get("items") or {}
    price_ids = [
        item.get("price") for item in (items.get("data") or []) if item.get("price")
    ]
    if not price_ids:
        return None
    for plan in await billing.plans.list_public():
        if plan.stripe_price_id_monthly in price_ids or plan.stripe_price_id_annual in price_ids:
            return plan.tier
    return None


async def _audit_webhook_rejected(
    db: AsyncSession, *, event_type: str | None, reason: str
) -> None:
    """Audit a rejected/failed webhook. Tenant is unknown at rejection time,
    so this records a platform-level audit row (tenant_id NULL)."""
    try:
        await AuditService(db).record(
            tenant_id=None,
            action="billing.webhook_rejected",
            actor_email="system:stripe",
            entity_type="stripe_webhook",
            entity_id=None,
            description=f"Stripe webhook rejected ({reason})",
            new_values={"event_type": event_type, "reason": reason},
        )
        await db.commit()
    except Exception:  # noqa: BLE001 - audit failure must not mask the 400/500
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tenant_id(data: dict) -> uuid.UUID | None:
    """Extract tenant_id from Stripe metadata."""
    metadata = data.get("metadata") or {}
    tid = metadata.get("tenant_id")
    if not tid:
        return None
    try:
        return uuid.UUID(tid)
    except ValueError:
        return None


def _parse_timestamp(value) -> datetime | None:
    """Convert a Unix timestamp to a timezone-aware datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, TypeError):
        return None