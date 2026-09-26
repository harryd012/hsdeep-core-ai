"""
Plan Enforcement (Epic C).

FastAPI dependency that checks a tenant's plan entitlements before allowing
resource creation (devices, sensors, collectors). Returns a graceful 402
with an upgrade CTA payload when a limit is hit, rather than a raw DB error.

Also provides a per-tenant API rate-limit check that reuses the existing
usage_counters.api.call bucket.

Downgrade-over-limit: when a subscription is flagged `over_limit_blocked`
(a downgrade left the tenant over the new plan's limits), NEW resource
creation is blocked with a 402, but existing resources are never deleted
or disabled.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.models.subscription import Subscription
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService, SubscriptionNotFound


def get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


async def _audit_entitlement_denial(
    tenant_id: uuid.UUID,
    resource: str,
    error_code: str,
    detail_message: str,
) -> None:
    """Best-effort audit of an entitlement violation. Opens its OWN session
    because the caller's request transaction is about to be discarded when
    the 402 is raised."""
    try:
        from app.core.db import AsyncSessionLocal
        from app.services.audit_service import AuditService

        async with AsyncSessionLocal() as db:
            await AuditService(db).record(
                tenant_id=tenant_id,
                action="entitlement.denied",
                actor_type="system",
                entity_type="entitlement",
                description=f"Entitlement denied for resource {resource!r} ({error_code})",
                context={"resource": resource, "error": error_code, "message": detail_message},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - auditing must never break the denial path
        pass


# Subscription statuses that hard-block writes. past_due is deliberately
# NOT here - the 3-day grace period is intentional (see
# BillingService.cancel_past_due_after_grace); past_due tenants keep
# working so they can resolve payment.
BLOCKED_SUBSCRIPTION_STATUSES = frozenset({"suspended", "canceled", "incomplete"})


async def require_active_subscription(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    billing: BillingService = Depends(get_billing_service),
) -> None:
    """Dependency: block WRITE routes for tenants whose subscription is
    suspended/canceled/incomplete (402 "subscription_suspended").

    Read routes are intentionally NOT gated by this dependency so a
    suspended tenant can still view its data and reach the billing pages
    to reactivate. past_due tenants pass (grace period is intentional).
    """
    # NOTE: deliberately NOT get_active_subscription() - that excludes
    # suspended/canceled rows, which would misreport a suspended tenant as
    # having no subscription at all. We need the actual latest status.
    from sqlalchemy import select

    result = await billing.db.execute(
        select(Subscription)
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.is_deleted.is_(False),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "no_subscription",
                "message": "No active subscription. Please subscribe to continue.",
                "upgrade_url": "/usage",
            },
        )
    if sub.status in BLOCKED_SUBSCRIPTION_STATUSES:
        await _audit_entitlement_denial(
            tenant_id,
            "subscription",
            "subscription_suspended",
            f"Write blocked: subscription status {sub.status!r}",
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "subscription_suspended",
                "subscription_status": sub.status,
                "message": (
                    "Your subscription is not active "
                    f"({sub.status}). Please update your billing details to "
                    "reactivate. You can still view your data."
                ),
                "upgrade_url": "/usage",
            },
        )
    # past_due / trialing / active fall through: write allowed.


# ---------------------------------------------------------------------------
# CANONICAL ENTITLEMENT LOGIC (Phase 6.4B consolidation)
#
# This module is the SINGLE source of truth for entitlement decisions. Both the
# HTTP dependencies below and non-HTTP callers (collectors, workers, services)
# call these functions, so there is exactly one implementation of each rule.
#
# FEATURES come from ``plans.features_json`` using the REAL product keys:
#   sla, sso, snmp, ai_rca, audit_log, msp_features, cloud_monitoring,
#   windows_monitoring
# No speculative keys are invented here.
# ---------------------------------------------------------------------------

#: ``features_json`` values that mean "explicitly switched off". Any other
#: non-empty string marker (e.g. "limited", "advanced", "full") counts as ON,
#: matching the seeded plan data.
_DISABLED_FEATURE_VALUES = frozenset({"", "false", "none", "disabled", "no", "off"})


def feature_enabled(features: dict, feature: str) -> bool:
    """Pure evaluation of ONE feature against a resolved feature map.

    Fail-closed: anything other than an explicitly truthy value denies.
    """
    value = features.get((feature or "").strip().lower())
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in _DISABLED_FEATURE_VALUES
    # False, None, 0, [] and anything unexpected withhold the feature.
    return False


async def resolve_tenant_features(
    billing: BillingService, tenant_id: uuid.UUID
) -> dict | None:
    """The tenant's feature map, or ``None`` when it has no usable plan.

    ``None`` is the fail-closed signal and covers every unusable state: no
    active subscription at all (``get_active`` excludes suspended/canceled/
    incomplete rows), a plan row that is missing, or a status listed in
    ``BLOCKED_SUBSCRIPTION_STATUSES``.
    """
    sub = await billing.subscriptions.get_active(tenant_id)
    if sub is None:
        return None
    if sub.status in BLOCKED_SUBSCRIPTION_STATUSES:
        return None
    plan = getattr(sub, "plan", None)
    if plan is None:
        return None
    features = plan.features_json if isinstance(plan.features_json, dict) else {}
    return dict(features)


async def has_feature(
    billing: BillingService, tenant_id: uuid.UUID, feature: str
) -> bool:
    """Programmatic feature check for NON-HTTP code paths (no FastAPI needed)."""
    features = await resolve_tenant_features(billing, tenant_id)
    if features is None:
        return False
    return feature_enabled(features, feature)


class FeatureNotEntitled(Exception):
    """Raised by ``ensure_feature`` for non-HTTP callers."""

    def __init__(self, message: str, *, feature: str) -> None:
        super().__init__(message)
        self.feature = feature


async def ensure_feature(
    billing: BillingService, tenant_id: uuid.UUID, feature: str
) -> None:
    """Programmatic feature check that RAISES when the feature is not entitled.

    Fail-closed: no subscription, an unusable plan, or an unknown/disabled
    feature all raise.
    """
    if not await has_feature(billing, tenant_id, feature):
        raise FeatureNotEntitled(
            f"Tenant {tenant_id} is not entitled to feature {feature!r}.",
            feature=feature,
        )


async def evaluate_resource_limit(
    billing: BillingService, tenant_id: uuid.UUID, resource: str
) -> tuple[str, dict, str] | None:
    """CANONICAL resource-limit decision.

    Returns ``None`` when the operation is ALLOWED, otherwise a triple of
    ``(error_code, http_payload, audit_message)`` that ``require_entitlement``
    turns into its 402. Keeping the payload here means the HTTP and programmatic
    paths can never drift apart.

    Only resources with a canonical plan limit are evaluated; an unenforced
    resource name matches no limit and is allowed (no speculative enforcement
    is invented).
    """
    try:
        entitlements = await billing.get_entitlements(tenant_id)
    except SubscriptionNotFound as exc:
        return (
            "no_subscription",
            {
                "error": "no_subscription",
                "message": "No active subscription. Please subscribe to continue.",
                "upgrade_url": "/usage",
            },
            str(exc),
        )

    # Downgrade-over-limit: block NEW resource creation, never delete existing.
    if entitlements.get("over_limit_blocked"):
        return (
            "over_limit_blocked",
            {
                "error": "over_limit_blocked",
                "resource": resource,
                "plan": entitlements["plan_name"],
                "plan_tier": entitlements["plan_tier"],
                "message": (
                    f"You are over your {entitlements['plan_name']} plan's limits "
                    "after a plan change. Existing resources are preserved, but new "
                    "resource creation is blocked until you upgrade or reduce usage."
                ),
                "upgrade_url": "/usage",
            },
            f"Tenant over plan limits after downgrade; new {resource} creation blocked",
        )

    # Normal per-resource limit check.
    for limit in entitlements["limits"]:
        if limit["resource"] == resource and limit["over_limit"]:
            return (
                "plan_limit_exceeded",
                {
                    "error": "plan_limit_exceeded",
                    "resource": limit["resource"],
                    "limit": limit["limit"],
                    "used": limit["used"],
                    "plan": entitlements["plan_name"],
                    "plan_tier": entitlements["plan_tier"],
                    "message": (
                        f"Your {entitlements['plan_name']} plan allows "
                        f"{limit['limit']} {limit['resource']}. You are using "
                        f"{limit['used']}. Upgrade to add more."
                    ),
                    "upgrade_url": "/usage",
                },
                f"{limit['label']} limit {limit['limit']} reached (used {limit['used']})",
            )

    return None


async def has_resource_capacity(
    billing: BillingService, tenant_id: uuid.UUID, resource: str
) -> bool:
    """Programmatic limit check for NON-HTTP code paths."""
    return await evaluate_resource_limit(billing, tenant_id, resource) is None


def require_entitlement(resource: str):
    """Dependency factory: guard a route with a plan limit check.

    Usage:
        @router.post("/sources", dependencies=[Depends(require_entitlement("collectors"))])
        async def create_source(...):
            ...

    Raises 402 Payment Required with an upgrade CTA payload when the
    tenant is at/over the limit for `resource`, OR when the subscription
    is flagged `over_limit_blocked` (downgrade left the tenant over the
    new plan's limits - block NEW resource creation, never delete existing).
    """

    async def _dependency(
        tenant_id: uuid.UUID = Depends(get_tenant_id),
        billing: BillingService = Depends(get_billing_service),
    ) -> None:
        # Delegate to the canonical decision so this dependency and the
        # programmatic path share exactly ONE implementation.
        denial = await evaluate_resource_limit(billing, tenant_id, resource)
        if denial is None:
            return
        error_code, payload, audit_message = denial
        await _audit_entitlement_denial(tenant_id, resource, error_code, audit_message)
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=payload
        )

    return _dependency


def require_feature(feature: str):
    """Dependency factory: guard a route on a PLAN FEATURE.

    Usage:
        @router.post("/x", dependencies=[Depends(require_feature("snmp"))])
        async def create_x(...):
            ...

    Raises 402 with the same upgrade-CTA shape as ``require_entitlement`` and
    audits the denial through the same canonical helper, so the two guards
    behave identically.
    """

    async def _dependency(
        tenant_id: uuid.UUID = Depends(get_tenant_id),
        billing: BillingService = Depends(get_billing_service),
    ) -> None:
        features = await resolve_tenant_features(billing, tenant_id)
        if features is None:
            await _audit_entitlement_denial(
                tenant_id,
                feature,
                "no_subscription",
                f"No usable plan for feature {feature!r}",
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "no_subscription",
                    "message": "No active subscription. Please subscribe to continue.",
                    "upgrade_url": "/usage",
                },
            )
        if not feature_enabled(features, feature):
            await _audit_entitlement_denial(
                tenant_id,
                feature,
                "feature_not_entitled",
                f"Plan does not include {feature!r}",
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "feature_not_entitled",
                    "feature": feature,
                    "message": (
                        f"Your current plan does not include {feature!r}. "
                        "Upgrade to enable it."
                    ),
                    "upgrade_url": "/usage",
                },
            )

    return _dependency

