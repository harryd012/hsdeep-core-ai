"""
Pydantic v2 schemas for the Billing & Entitlements API (Epic A-E).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---- Plans ----------------------------------------------------------------

class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tier: str
    description: str | None
    price_monthly: float
    price_annual: float
    device_limit: int
    sensor_limit: int
    api_rate_limit: int
    collector_limit: int
    seats: int
    alert_limit: int = 0
    collector_run_limit: int = 0
    display_order: int = 0
    trial_eligible: bool = True
    features_json: dict
    is_public: bool


# ---- Subscriptions --------------------------------------------------------

class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    status: str
    stripe_subscription_id: str | None
    stripe_customer_id: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    billing_interval: str
    canceled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubscriptionWithPlanOut(SubscriptionOut):
    """Subscription plus the denormalized plan limits for the UI."""

    plan_name: str
    plan_tier: str
    device_limit: int
    sensor_limit: int
    api_rate_limit: int
    collector_limit: int
    seats: int


# ---- Entitlements ---------------------------------------------------------

class EntitlementLimit(BaseModel):
    """A single resource limit with current usage."""

    resource: str
    label: str
    limit: int
    used: int
    pct: float
    over_limit: bool


class EntitlementsOut(BaseModel):
    """Combined plan limits + current usage for a tenant."""

    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    plan_name: str
    plan_tier: str
    subscription_status: str
    billing_interval: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    limits: list[EntitlementLimit]
    over_limit: bool
    over_limit_blocked: bool = False


# ---- Plan selection / subscription management ------------------------------

class SelectPlanRequest(BaseModel):
    """Client sends ONLY the plan tier. Price/limits are resolved server-side
    from the trusted plan catalog - never trusted from the browser."""
    plan_tier: str = Field(..., min_length=1)
    billing_interval: str = Field(default="month", description="month | year")


class SelectPlanResponse(BaseModel):
    """Result of a plan-selection request.

    - status="active": the plan was applied immediately (free plan, or an
      approved internal/test activation path). No payment needed.
    - status="checkout_required": a payment provider session was created;
      redirect the user to checkout_url. The subscription only changes when
      the provider webhook confirms payment.
    - status="contact_sales": the plan requires sales involvement (custom
      limits) and no self-service activation is available.
    """
    status: str  # active | checkout_required | contact_sales
    plan_tier: str
    checkout_url: str | None = None
    message: str | None = None


class CancelSubscriptionRequest(BaseModel):
    reason: str | None = None


class BillingUsageOut(BaseModel):
    """Tenant usage for the current billing period against plan allowances."""

    tenant_id: uuid.UUID
    plan_name: str
    plan_tier: str
    subscription_status: str
    period_start: datetime | None
    period_end: datetime | None
    usage: dict  # {api_calls, alerts, collector_runs, devices, sensors, sources, seats}
    limits: list[EntitlementLimit]


# ---- Signup / self-service ------------------------------------------------

class SignupRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    full_name: str | None = None
    org_name: str = Field(..., min_length=1)
    plan_tier: str = Field(default="starter", description="starter | pro | enterprise")


class SignupResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    plan_tier: str
    subscription_status: str
    trial_ends_at: datetime | None
    access_token: str
    refresh_token: str
    expires_in: int
    # True when a confirmation email was queued; the user must confirm before
    # their next sign-in (this response's session works until it expires).
    email_verification_sent: bool = False


# ---- Stripe ---------------------------------------------------------------

class CheckoutSessionRequest(BaseModel):
    plan_tier: str = Field(..., description="starter | pro | enterprise")
    billing_interval: str = Field(default="month", description="month | year")


class CheckoutSessionResponse(BaseModel):
    url: str
    session_id: str


class BillingPortalResponse(BaseModel):
    url: str