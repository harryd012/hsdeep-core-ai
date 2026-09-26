"""
Customer Onboarding & Activation API (Phase 2 v0.1).

GET /api/onboarding/status - derives the tenant's onboarding stage LIVE from
existing server-side state. No new models, no stored onboarding flag:

    authenticated
      -> subscription_ready   (valid subscription via BillingService)
      -> source_configured    (>=1 monitoring source)
      -> activated            (>=1 source with a SUCCESSFUL collection run)

The activation condition is deliberately "first real monitoring value"
(a successful collector run), not "user opened the dashboard".

Tenant is ALWAYS derived from the authenticated principal (get_tenant_id);
tenant_id is never accepted from client input. Read-only: requires the
existing `billing.read` permission. No secrets are exposed.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService, SubscriptionNotFound
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingCheck(BaseModel):
    label: str
    done: bool


class OnboardingStatusOut(BaseModel):
    tenant_id: uuid.UUID
    # not_started | subscription_ready | source_configured | activated
    stage: str
    activated: bool
    subscription_status: str | None
    plan_tier: str | None
    sources_configured: int
    sources_connected: int
    first_collection_at: str | None
    next_action: str
    checks: list[OnboardingCheck]


def _get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


@router.get("/status", response_model=OnboardingStatusOut)
async def get_onboarding_status(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("billing.read")),
    billing: BillingService = Depends(_get_billing_service),
    db: AsyncSession = Depends(get_db),
):
    """Derive the tenant's onboarding/activation progress from live state."""
    # 1. Subscription (source of truth: BillingService)
    try:
        entitlements = await billing.get_entitlements(tenant_id)
        subscription_status = entitlements["subscription_status"]
        plan_tier = entitlements["plan_tier"]
    except SubscriptionNotFound:
        subscription_status = None
        plan_tier = None

    subscription_ok = subscription_status in ("trialing", "active", "past_due")

    # 2. Monitoring sources (tenant-scoped by the repository)
    source_repo = MonitoringSourceRepository(db)
    sources = await source_repo.list(tenant_id=tenant_id)
    connected = [
        s for s in sources
        if s.last_successful_run_at is not None
    ]
    first_collection_at = (
        max((s.last_successful_run_at for s in connected), default=None)
    )

    # 3. Stage derivation (server-side truth, never client-reported)
    if not subscription_ok:
        stage = "not_started"
        next_action = "Choose a plan and start your subscription."
    elif not sources:
        stage = "subscription_ready"
        next_action = "Add your first monitoring source (e.g. PRTG core server)."
    elif not connected:
        stage = "source_configured"
        next_action = "Wait for the first collection run, or trigger a sync from Monitoring."
    else:
        stage = "activated"
        next_action = "You are activated. Explore alerts, incidents and reports."

    return OnboardingStatusOut(
        tenant_id=tenant_id,
        stage=stage,
        activated=stage == "activated",
        subscription_status=subscription_status,
        plan_tier=plan_tier,
        sources_configured=len(sources),
        sources_connected=len(connected),
        first_collection_at=first_collection_at.isoformat() if first_collection_at else None,
        next_action=next_action,
        checks=[
            OnboardingCheck(label="Account created", done=True),
            OnboardingCheck(label="Subscription active", done=subscription_ok),
            OnboardingCheck(label="Monitoring source configured", done=bool(sources)),
            OnboardingCheck(label="First collection completed", done=bool(connected)),
        ],
    )