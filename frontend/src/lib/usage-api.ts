/**
 * Typed API client for the usage metering endpoints.
 *
 * Exposes tenant consumption data for the billing dashboard and
 * subscription tier monitoring.
 */

import { ApiError, DEFAULT_TENANT_ID, apiFetch as authorizedFetch } from "./api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UsageSummary {
  tenant_id: string;
  total_api_calls: number;
  total_alerts_ingested: number;
  total_metrics_ingested: number;
  total_collector_runs: number;
  total_sop_executions: number;
  total_workflow_executions: number;
  total_auth_logins: number;
  grand_total: number;
  period_start: string;
  period_end: string;
}

export interface UsageBucket {
  event_type: string;
  bucket_start: string;
  count: number;
}

export interface UsageEventsResponse {
  tenant_id: string;
  events: UsageBucket[];
  total: number;
}

// ---------------------------------------------------------------------------
// Billing & Entitlements (Epic A-E)
// ---------------------------------------------------------------------------

export interface Plan {
  id: string;
  name: string;
  tier: string;
  description: string | null;
  price_monthly: number;
  price_annual: number;
  device_limit: number;
  sensor_limit: number;
  api_rate_limit: number;
  collector_limit: number;
  seats: number;
  alert_limit?: number;
  collector_run_limit?: number;
  display_order?: number;
  trial_eligible?: boolean;
  features_json: Record<string, unknown>;
  is_public: boolean;
}

export interface SubscriptionWithPlan {
  id: string;
  tenant_id: string;
  plan_id: string;
  status: string;
  stripe_subscription_id: string | null;
  stripe_customer_id: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  trial_ends_at: string | null;
  billing_interval: string;
  canceled_at: string | null;
  created_at: string;
  updated_at: string;
  plan_name: string;
  plan_tier: string;
  device_limit: number;
  sensor_limit: number;
  api_rate_limit: number;
  collector_limit: number;
  seats: number;
}

export interface EntitlementLimit {
  resource: string;
  label: string;
  limit: number;
  used: number;
  pct: number;
  over_limit: boolean;
}

export interface Entitlements {
  tenant_id: string;
  plan_id: string;
  plan_name: string;
  plan_tier: string;
  subscription_status: string;
  billing_interval: string;
  current_period_start: string | null;
  current_period_end: string | null;
  trial_ends_at: string | null;
  limits: EntitlementLimit[];
  over_limit: boolean;
  over_limit_blocked: boolean;
}

export interface SignupRequest {
  email: string;
  password: string;
  full_name?: string | null;
  org_name: string;
  plan_tier: string;
}

export interface SignupResponse {
  tenant_id: string;
  user_id: string;
  email: string;
  plan_tier: string;
  subscription_status: string;
  trial_ends_at: string | null;
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export function listPlans(): Promise<Plan[]> {
  return apiFetch<Plan[]>("/api/billing/plans");
}

export interface OnboardingCheck {
  label: string;
  done: boolean;
}

export interface OnboardingStatus {
  tenant_id: string;
  stage: "not_started" | "subscription_ready" | "source_configured" | "activated";
  activated: boolean;
  subscription_status: string | null;
  plan_tier: string | null;
  sources_configured: number;
  sources_connected: number;
  first_collection_at: string | null;
  next_action: string;
  checks: OnboardingCheck[];
}

/** Server-derived onboarding/activation progress (real backend state). */
export function getOnboardingStatus(): Promise<OnboardingStatus | null> {
  return apiFetchOrNull<OnboardingStatus>("/api/onboarding/status");
}

export function getSubscription(): Promise<SubscriptionWithPlan | null> {
  return apiFetchOrNull<SubscriptionWithPlan>("/api/billing/subscription");
}

export function getEntitlements(): Promise<Entitlements | null> {
  return apiFetchOrNull<Entitlements>("/api/billing/entitlements");
}

export function signup(payload: SignupRequest): Promise<SignupResponse> {
  return apiFetch<SignupResponse>("/api/billing/signup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface CheckoutSessionResponse {
  url: string;
  session_id: string;
}

/**
 * Create a Stripe Checkout session for the requested plan. The backend
 * resolves the price from its own trusted plan catalog - the client never
 * supplies a Stripe price ID. Redirect the browser to the returned URL.
 */
export function createCheckoutSession(
  planTier: string,
  billingInterval: "month" | "year" = "month",
): Promise<CheckoutSessionResponse> {
  return apiFetch<CheckoutSessionResponse>("/api/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier, billing_interval: billingInterval }),
  });
}

// ---------------------------------------------------------------------------
// Plan selection / subscription management (server-authoritative)
// ---------------------------------------------------------------------------

export interface SelectPlanResponse {
  status: "active" | "checkout_required" | "contact_sales";
  plan_tier: string;
  checkout_url: string | null;
  message: string | null;
}

/**
 * Select a plan. The client sends ONLY the tier - the backend resolves the
 * price and limits from its trusted catalog. Never send price/limits here.
 */
export function selectPlan(
  planTier: string,
  billingInterval: "month" | "year" = "month",
): Promise<SelectPlanResponse> {
  return apiFetch<SelectPlanResponse>("/api/billing/subscription/select", {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier, billing_interval: billingInterval }),
  });
}

/** Change plan (upgrade/downgrade). Backend validates + audits; downgrades
 * that leave the tenant over limits set over_limit_blocked server-side. */
export function changePlan(planTier: string): Promise<SubscriptionWithPlan> {
  return apiFetch<SubscriptionWithPlan>("/api/billing/subscription/change", {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier }),
  });
}

/** Cancel the tenant's subscription (state-machine validated, audited). */
export function cancelSubscription(reason?: string): Promise<SubscriptionWithPlan> {
  return apiFetch<SubscriptionWithPlan>("/api/billing/subscription/cancel", {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export interface BillingUsage {
  tenant_id: string;
  plan_name: string;
  plan_tier: string;
  subscription_status: string;
  period_start: string | null;
  period_end: string | null;
  usage: Record<string, number>;
  limits: EntitlementLimit[];
}

/** Usage for the current billing period against plan allowances. */
export function getBillingUsage(): Promise<BillingUsage> {
  return apiFetch<BillingUsage>("/api/billing/usage");
}

// ---------------------------------------------------------------------------
// API Functions
// ---------------------------------------------------------------------------

/**
 * Reuse the shared authenticated fetch from ./api so usage endpoints attach
 * the same Bearer token, 401 retry, and readable error handling as every
 * other API call. Previously this module had its own private fetch that
 * never attached the Authorization header, which is why Usage Overview
 * returned {"detail":"Not authenticated"} while dashboard widgets worked.
 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return authorizedFetch<T>(path, init);
}

/**
 * Fetch a resource, resolving `null` when the backend reports 404.
 *
 * A 404 from the billing/onboarding endpoints is a legitimate SaaS lifecycle
 * state ("this tenant has no subscription / entitlements / onboarding row
 * yet") - NOT an error. Only genuine failures (5xx, network, auth) throw, so
 * consuming pages can show a friendly "no active subscription" state instead
 * of a red error banner for a clean 404.
 */
async function apiFetchOrNull<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    return await apiFetch<T>(path, init);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/**
 * Get aggregated usage summary for a tenant over a lookback window.
 */
export function getUsageSummary(
  params: {
    tenantId?: string;
    days?: number;
  } = {},
): Promise<UsageSummary> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const days = params.days ?? 30;
  return apiFetch<UsageSummary>(
    `/api/v1/tenants/${tenantId}/usage/summary?days=${days}`,
  );
}

/**
 * Get hourly usage buckets for a tenant over a lookback window.
 */
export function getUsageEvents(
  params: {
    tenantId?: string;
    eventType?: string;
    days?: number;
  } = {},
): Promise<UsageEventsResponse> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const search = new URLSearchParams();
  if (params.days) search.set("days", String(params.days));
  if (params.eventType) search.set("event_type", params.eventType);
  return apiFetch<UsageEventsResponse>(
    `/api/v1/tenants/${tenantId}/usage/events?${search.toString()}`,
  );
}
