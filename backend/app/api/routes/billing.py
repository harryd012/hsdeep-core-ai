"""
Billing & Entitlements API (Epic A-E).

Exposes:
  * Public plan catalog (GET /api/billing/plans)
  * Tenant subscription + entitlements (GET /api/billing/subscription, /entitlements)
  * Self-service signup (POST /api/billing/signup) - creates tenant + trial
  * Stripe Checkout / Customer Portal (Epic B) - stubbed when STRIPE_SECRET_KEY unset
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, get_tenant_id, require_permission
from app.core.config import get_settings
from app.core.db import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.assignment_repository import AssignmentRepository
from app.schemas.billing import (
    BillingPortalResponse,
    BillingUsageOut,
    CancelSubscriptionRequest,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    EntitlementsOut,
    PlanOut,
    SelectPlanRequest,
    SelectPlanResponse,
    SignupRequest,
    SignupResponse,
    SubscriptionWithPlanOut,
)
from app.core.auth import require_admin
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.billing_service import (
    BillingError,
    BillingService,
    InvalidTransition,
    PlanNotFound,
    SubscriptionNotFound,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

settings = get_settings()

# Signup rate limit: 5 signups per IP per hour (sliding window via Redis).
_SIGNUP_RATE_LIMIT = 5
_SIGNUP_RATE_WINDOW_SECONDS = 3600
_SIGNUP_RATE_KEY_PREFIX = "billing:signup:rate:"


def get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


# ---- Public plan catalog --------------------------------------------------

@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    service: BillingService = Depends(get_billing_service),
):
    """Public list of available subscription plans."""
    return await service.plans.list_public()


# ---- Authenticated subscription / entitlements ----------------------------

@router.get("/subscription", response_model=SubscriptionWithPlanOut)
async def get_subscription(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("billing.read")),
    service: BillingService = Depends(get_billing_service),
):
    """Return the tenant's current subscription with plan details.

    Uses get_current (any status) so a suspended/canceled tenant can still
    SEE their subscription status - enforcement (denying operations) is a
    separate concern handled by require_entitlement/get_active."""
    sub = await service.subscriptions.get_current(tenant_id)
    if sub is None or sub.plan is None:
        raise HTTPException(status_code=404, detail="No subscription")
    return SubscriptionWithPlanOut(
        id=sub.id,
        tenant_id=sub.tenant_id,
        plan_id=sub.plan_id,
        status=sub.status,
        stripe_subscription_id=sub.stripe_subscription_id,
        stripe_customer_id=sub.stripe_customer_id,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        trial_ends_at=sub.trial_ends_at,
        billing_interval=sub.billing_interval,
        canceled_at=sub.canceled_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        plan_name=sub.plan.name,
        plan_tier=sub.plan.tier,
        device_limit=sub.plan.device_limit,
        sensor_limit=sub.plan.sensor_limit,
        api_rate_limit=sub.plan.api_rate_limit,
        collector_limit=sub.plan.collector_limit,
        seats=sub.plan.seats,
    )


@router.get("/entitlements", response_model=EntitlementsOut)
async def get_entitlements(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("billing.read")),
    service: BillingService = Depends(get_billing_service),
):
    """Return plan limits + current usage for the tenant."""
    try:
        return await service.get_entitlements(tenant_id)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---- Usage for the current billing period ----------------------------------

@router.get("/usage", response_model=BillingUsageOut)
async def get_billing_usage(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("billing.read")),
    service: BillingService = Depends(get_billing_service),
):
    """Tenant usage for the current billing period against plan allowances.
    Tenant-scoped via the authenticated principal; no client-supplied IDs."""
    try:
        ent = await service.get_entitlements(tenant_id)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    usage = {l["resource"]: l["used"] for l in ent["limits"]}
    return BillingUsageOut(
        tenant_id=tenant_id,
        plan_name=ent["plan_name"],
        plan_tier=ent["plan_tier"],
        subscription_status=ent["subscription_status"],
        period_start=ent["current_period_start"],
        period_end=ent["current_period_end"],
        usage=usage,
        limits=ent["limits"],
    )


# ---- Plan selection (Part 6/7: payment-provider boundary) -------------------

@router.post("/subscription/select", response_model=SelectPlanResponse)
async def select_plan(
    payload: SelectPlanRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Select a plan. The client sends ONLY the tier - price and limits are
    resolved server-side from the trusted catalog; nothing from the browser
    is trusted.

    Payment boundary (no fake payment success):
      * Stripe configured  -> checkout_required + checkout_url. The plan is
        applied ONLY when the Stripe webhook confirms payment.
      * Stripe NOT configured:
          - free plan ($0)   -> applied immediately (audited).
          - paid plan        -> contact_sales response; NO subscription is
                                activated just because a button was clicked.
    """
    if payload.billing_interval not in ("month", "year"):
        raise HTTPException(status_code=422, detail="billing_interval must be 'month' or 'year'.")

    plan = await service.plans.get_by_tier(payload.plan_tier)
    if plan is None or not plan.is_public:
        # Do not leak whether the tier exists vs. is private.
        raise HTTPException(status_code=404, detail=f"Plan tier {payload.plan_tier!r} not found")

    sub = await service.get_active_subscription(tenant_id)
    if sub is None or sub.plan is None:
        raise HTTPException(status_code=404, detail="No active subscription")

    await AuditService(service.db).record(
        tenant_id=tenant_id,
        action="billing.plan_selected",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="plan",
        entity_id=plan.id,
        description=f"Plan {plan.name!r} selected ({payload.billing_interval})",
        new_values={"plan_tier": plan.tier, "billing_interval": payload.billing_interval},
    )

    if settings.stripe_secret_key:
        # Reuse the existing Stripe Checkout flow - the webhook applies the
        # plan on confirmation. We never mark a paid plan active here.
        session = await _create_stripe_checkout(service, tenant_id, principal, sub, plan, payload.billing_interval)
        await db_commit_helper(service)
        return SelectPlanResponse(
            status="checkout_required", plan_tier=plan.tier, checkout_url=session.url,
            message="Complete checkout to activate this plan.",
        )

    if float(plan.price_monthly) == 0.0:
        # Free plan: safe to apply without a payment provider (dev/test path).
        sub = await service.change_plan(
            sub, plan.tier, actor_id=principal.user_id, actor_email=principal.email
        )
        await db_commit_helper(service)
        return SelectPlanResponse(
            status="active", plan_tier=plan.tier,
            message=f"Plan {plan.name} is now active.",
        )

    # Paid plan with no payment provider configured: do NOT fake activation.
    await AuditService(service.db).record(
        tenant_id=tenant_id,
        action="billing.checkout_unavailable",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="plan",
        entity_id=plan.id,
        description=f"Paid plan {plan.name!r} requested but no payment provider configured",
    )
    await db_commit_helper(service)
    return SelectPlanResponse(
        status="contact_sales", plan_tier=plan.tier,
        message=(
            f"{plan.name} requires payment setup. Configure STRIPE_SECRET_KEY to "
            "enable self-service checkout, or contact sales for custom limits."
        ),
    )


# ---- Plan change / cancel (canonical Part-17 paths) -------------------------

class PlanChangeRequest(BaseModel):
    plan_tier: str


@router.post("/subscription/change", response_model=SubscriptionWithPlanOut)
async def change_subscription_plan(
    payload: PlanChangeRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Canonical plan-change endpoint (alias of POST /billing/plan). Same
    server-side validation, downgrade-over-limit handling and auditing."""
    return await change_plan(payload, tenant_id, principal, service)


@router.post("/subscription/cancel", response_model=SubscriptionWithPlanOut)
async def cancel_subscription(
    payload: CancelSubscriptionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Tenant-initiated cancellation. Uses the existing state machine
    (transition_status), so invalid transitions are rejected with 409 and
    every cancellation is audited. Canceled subscriptions keep read access
    but lose write access (existing enforcement semantics)."""
    sub = await service.subscriptions.get_current(tenant_id)
    if sub is None or sub.plan is None:
        raise HTTPException(status_code=404, detail="No subscription")
    try:
        sub = await service.transition_status(
            sub, "canceled",
            actor_id=principal.user_id,
            actor_email=principal.email,
            reason=payload.reason or "Tenant-initiated cancellation",
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db_commit_helper(service)
    return _subscription_out(sub)


# ---- Plan change (upgrade/downgrade) ---------------------------------------

@router.post("/plan", response_model=SubscriptionWithPlanOut)
async def change_plan(
    payload: PlanChangeRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Upgrade/downgrade the tenant's plan (no payment gateway yet - the
    internal model a gateway will plug into). Downgrades that leave the
    tenant over the new plan's limits set over_limit_blocked, which
    require_entitlement() enforces. Audited with old/new plan."""
    sub = await service.get_active_subscription(tenant_id)
    if sub is None or sub.plan is None:
        raise HTTPException(status_code=404, detail="No active subscription")
    try:
        sub = await service.change_plan(
            sub,
            payload.plan_tier,
            actor_id=principal.user_id,
            actor_email=principal.email,
        )
    except PlanNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db_commit_helper(service)
    return _subscription_out(sub)


class StatusChangeRequest(BaseModel):
    status: str
    reason: str | None = None


@router.post("/subscription/status", response_model=SubscriptionWithPlanOut)
async def change_subscription_status(
    payload: StatusChangeRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),  # platform-admin only (superuser)
    service: BillingService = Depends(get_billing_service),
):
    """Platform-admin subscription lifecycle control (suspend/reactivate/
    cancel). Transitions are validated server-side against the state
    machine; every change is audited. NEVER trusts a client-claimed status
    for enforcement - enforcement always re-reads server-side state."""
    sub = await service.subscriptions.get_current(tenant_id)
    if sub is None or sub.plan is None:
        raise HTTPException(status_code=404, detail="No subscription")
    try:
        sub = await service.transition_status(
            sub, payload.status, reason=payload.reason
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db_commit_helper(service)
    return _subscription_out(sub)


# ---- Platform-admin customer management ------------------------------------

@router.get("/admin/customers")
async def list_customers(
    _: Principal = Depends(require_admin),  # platform-admin only (superuser)
    service: BillingService = Depends(get_billing_service),
):
    """Platform-admin customer list: every tenant with its latest plan,
    subscription status, created date and last-activity timestamp.
    NOT exposed to tenant users - gated behind superuser only."""
    from app.models.user import User
    from sqlalchemy import func as _func, select as _select

    subs = await service.subscriptions.list_all_with_tenants()
    out = []
    for sub in subs:
        tenant = sub.tenant
        last_activity = (
            await service.db.execute(
                _select(_func.max(User.last_login_at)).where(User.tenant_id == tenant.id)
            )
        ).scalar_one()
        out.append(
            {
                "tenant_id": str(tenant.id),
                "name": tenant.name,
                "slug": tenant.slug,
                "plan_name": sub.plan.name if sub.plan else None,
                "plan_tier": sub.plan.tier if sub.plan else None,
                "subscription_status": sub.status,
                "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
                "over_limit_blocked": sub.over_limit_blocked,
                "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
                "last_activity_at": last_activity.isoformat() if last_activity else None,
            }
        )
    return out


async def db_commit_helper(service: BillingService) -> None:
    """Commit the billing service's session (routes own the commit)."""
    await service.db.commit()


def _subscription_out(sub) -> SubscriptionWithPlanOut:
    return SubscriptionWithPlanOut(
        id=sub.id,
        tenant_id=sub.tenant_id,
        plan_id=sub.plan_id,
        status=sub.status,
        stripe_subscription_id=sub.stripe_subscription_id,
        stripe_customer_id=sub.stripe_customer_id,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        trial_ends_at=sub.trial_ends_at,
        billing_interval=sub.billing_interval,
        canceled_at=sub.canceled_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        plan_name=sub.plan.name,
        plan_tier=sub.plan.tier,
        device_limit=sub.plan.device_limit,
        sensor_limit=sub.plan.sensor_limit,
        api_rate_limit=sub.plan.api_rate_limit,
        collector_limit=sub.plan.collector_limit,
        seats=sub.plan.seats,
    )


# ---- Self-service signup (Epic E) -----------------------------------------

@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    billing: BillingService = Depends(get_billing_service),
    auth: AuthService = Depends(get_auth_service),
):
    """Create a new tenant + Owner user + trial subscription in one shot.

    This is the self-service onboarding entry point: a new user provides
    email/password/org name, we provision a tenant, seed RBAC roles, create
    the Owner user, and start a trial subscription on the requested plan.

    Rate-limited to 5 signups per IP per hour (sliding window via Redis).
    """
    if not get_settings().public_signup_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public sign-up is disabled. Ask your administrator for an account.",
        )
    # ---- Rate limit: 5 signups per IP per hour ----
    client_ip = request.client.host if request.client else "unknown"
    await _check_signup_rate_limit(client_ip)

    # 1. Create the tenant
    slug = _slugify(payload.org_name)
    tenant = Tenant(name=payload.org_name, slug=slug, is_active=True)
    db.add(tenant)
    await db.flush()

    # 2. Provision default RBAC roles for the tenant
    await auth.provision_tenant_rbac(tenant.id)

    # 3. Create the Owner user
    owner_role = await auth.roles.get_by_name(tenant.id, "Owner")
    role_ids = [owner_role.id] if owner_role else []
    try:
        user = await auth.create_user(
            tenant_id=tenant.id,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            is_superuser=False,
            role_ids=role_ids,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 3.5 Seed the default "General" AssignmentGroup with the Owner as
    # its sole member (strategy=round_robin). This gives every new tenant
    # a ready-to-use routing group for the Enterprise Assignment Engine.
    assignment_repo = AssignmentRepository(db)
    default_group = await assignment_repo.create_group(
        tenant_id=tenant.id,
        name="General",
        description="Default assignment group - all engineers",
        strategy="round_robin",
        is_active=True,
    )
    await assignment_repo.add_member(
        tenant_id=tenant.id,
        group_id=default_group.id,
        user_id=user.id,
        skills_json=[],
        capacity_weight=1,
        is_active=True,
    )

    # 4. Create the trial subscription
    try:
        sub = await billing.create_trial_subscription(
            tenant_id=tenant.id, plan_tier=payload.plan_tier
        )
    except PlanNotFound as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 4.5 Self-service accounts confirm their email before their next sign-in.
    # Only when email can actually be delivered: otherwise the user could
    # never confirm and would be locked out.
    from app.services.account_service import AccountService
    from app.services.email.outbox import email_enabled

    verification_sent = False
    if email_enabled():
        user.requires_email_verification = True
        await AccountService(db).send_verification(user)
        verification_sent = True

    # 5. Issue tokens for the new user
    access, refresh, expires_in = await auth._issue_tokens(user)

    await db.commit()

    return SignupResponse(
        tenant_id=tenant.id,
        user_id=user.id,
        email=user.email,
        plan_tier=sub.plan.tier if sub.plan else payload.plan_tier,
        subscription_status=sub.status,
        trial_ends_at=sub.trial_ends_at,
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        email_verification_sent=verification_sent,
    )


# ---- Stripe integration (Epic B) ------------------------------------------

@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    payload: CheckoutSessionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Create a Stripe Checkout session for upgrading/downgrading.

    Requires STRIPE_SECRET_KEY to be configured. Returns a 501 with a
    helpful message when Stripe is not configured (dev mode).
    """
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=501,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY to enable billing.",
        )

    try:
        import stripe
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Stripe SDK not installed. Run `pip install stripe`.",
        )

    stripe.api_key = settings.stripe_secret_key

    # Input validation first - the requested plan comes from the trusted
    # server-side plan catalog, never an arbitrary client-supplied price ID.
    if payload.billing_interval not in ("month", "year"):
        raise HTTPException(
            status_code=422,
            detail="billing_interval must be 'month' or 'year'.",
        )

    sub = await service.get_active_subscription(tenant_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="No active subscription")

    try:
        plan = await service.plans.get_by_tier(payload.plan_tier)
    except PlanNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if plan is None:
        raise HTTPException(
            status_code=404, detail=f"Plan tier {payload.plan_tier!r} not found"
        )

    price_id = (
        plan.stripe_price_id_annual
        if payload.billing_interval == "year"
        else plan.stripe_price_id_monthly
    )
    if not price_id:
        raise HTTPException(
            status_code=422,
            detail=f"Plan {plan.tier!r} has no Stripe price configured for {payload.billing_interval} billing.",
        )

    session = await _create_stripe_checkout(service, tenant_id, principal, sub, plan, payload.billing_interval)
    return CheckoutSessionResponse(url=session.url, session_id=session.id)


async def _create_stripe_checkout(service, tenant_id, principal, sub, plan, billing_interval: str):
    """Shared Stripe Checkout session creation used by /checkout and
    /subscription/select. Resolves the price from the trusted plan row."""
    import stripe

    stripe.api_key = settings.stripe_secret_key

    price_id = (
        plan.stripe_price_id_annual
        if billing_interval == "year"
        else plan.stripe_price_id_monthly
    )
    if not price_id:
        raise HTTPException(
            status_code=422,
            detail=f"Plan {plan.tier!r} has no Stripe price configured for {billing_interval} billing.",
        )

    # Ensure a Stripe Customer exists for this tenant (created once, then
    # reused). The mapping lives on the subscription row - never derived
    # from client input.
    stripe_customer_id = sub.stripe_customer_id
    if not stripe_customer_id:
        customer = stripe.Customer.create(
            metadata={"tenant_id": str(tenant_id)},
        )
        stripe_customer_id = customer.id
        await service.subscriptions.update_stripe_fields(
            sub, stripe_customer_id=stripe_customer_id
        )

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=stripe_customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        # Dedicated payment-result states. The success page NEVER activates
        # the subscription - it polls backend status; only the verified
        # Stripe webhook applies the plan (see stripe_webhook.py).
        success_url=f"{settings.frontend_url}/billing/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.frontend_url}/billing/payment/cancelled",
        metadata={
            "tenant_id": str(tenant_id),
            "subscription_id": str(sub.id),
            "plan_tier": plan.tier,
            "billing_interval": billing_interval,
        },
        subscription_data={
            "metadata": {
                "tenant_id": str(tenant_id),
                "subscription_id": str(sub.id),
            }
        },
    )
    await AuditService(service.db).record(
        tenant_id=tenant_id,
        action="billing.checkout_initiated",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="subscription",
        entity_id=sub.id,
        description=(
            f"Stripe checkout initiated for plan {plan.tier!r} "
            f"({billing_interval})"
        ),
        new_values={
            "checkout_session_id": session.id,
            "plan_tier": plan.tier,
            "billing_interval": billing_interval,
        },
    )
    await service.db.commit()
    return session


@router.get("/portal", response_model=BillingPortalResponse)
async def get_billing_portal(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("billing.write")),
    service: BillingService = Depends(get_billing_service),
):
    """Return a Stripe Customer Portal URL for self-service billing management."""
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=501,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY to enable billing.",
        )

    try:
        import stripe
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Stripe SDK not installed. Run `pip install stripe`.",
        )

    stripe.api_key = settings.stripe_secret_key

    sub = await service.get_active_subscription(tenant_id)
    if sub is None or not sub.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer linked to this subscription")

    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=f"{settings.frontend_url}/usage",
    )
    return BillingPortalResponse(url=session.url)


# ---- Helpers ---------------------------------------------------------------

class _LocalWindow:
    """In-process sliding window, the fallback when Redis is unavailable.

    Same limit as the shared one, counted per API process: with N replicas an
    IP can reach at most N x the limit during a Redis outage. That is a bound,
    unlike failing open, which allowed unlimited signups.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        if len(self._hits) > 100_000:  # bound memory under a flood of IPs
            self._hits.clear()
        return True


_signup_local_window = _LocalWindow()


def _signup_rate_exceeded() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Too many signups from this IP. Please try again in "
            f"{_SIGNUP_RATE_WINDOW_SECONDS // 3600} hour(s)."
        ),
    )


async def _check_signup_rate_limit(client_ip: str) -> None:
    """Enforce 5 signups per IP per hour using a Redis sliding window.

    Returns 429 when the limit is exceeded. If Redis is unavailable the
    request is checked against an in-process window with the same limit
    (never allowed unchecked) and the outage is logged at error level.
    """
    from app.core.redis import redis_client

    key = f"{_SIGNUP_RATE_KEY_PREFIX}{client_ip}"
    now = time.time()
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, now - _SIGNUP_RATE_WINDOW_SECONDS)
        pipe.zcard(key)
        count = (await pipe.execute())[1]
        if count >= _SIGNUP_RATE_LIMIT:
            raise _signup_rate_exceeded()
        # Unique member per attempt so two signups in the same second count twice.
        await redis_client.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        await redis_client.expire(key, _SIGNUP_RATE_WINDOW_SECONDS)
        return
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - any Redis failure uses the local window
        logger.error(
            "Signup rate limit: Redis unavailable (%s); enforcing the per-process limit instead.",
            type(exc).__name__,
        )
    if not _signup_local_window.acquire(key, _SIGNUP_RATE_LIMIT, _SIGNUP_RATE_WINDOW_SECONDS):
        raise _signup_rate_exceeded()


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from an org name."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = f"tenant-{uuid.uuid4().hex[:8]}"
    return slug