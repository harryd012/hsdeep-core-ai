/**
 * PLANS — the SINGLE SOURCE OF TRUTH for subscription plan presentation data.
 *
 * Every consumer (the "Choose a Plan" modal, the Compare Plans table, the
 * dashboard Subscription stat card, the /subscription active-plan display)
 * must read name/price/limit values from THIS object. No plan name, price,
 * or limit may be hardcoded anywhere else in the frontend.
 *
 * IMPORTANT — this catalog mirrors the backend billing seed
 * (backend/app/services/billing_service.py DEFAULT_PLANS), which is what
 * Stripe Checkout actually charges and what entitlement enforcement applies:
 *   - The middle plan's backend TIER KEY is "pro" (not "professional"), so
 *     each Plan carries both `id` (frontend key) and `tier` (backend key).
 *   - Annual prices below were taken from that seed ($490 / $1,990 / $9,990),
 *     NOT invented: the spec draft's guessed values ($2,388 / $10,788, i.e.
 *     monthly*12) contradict real billing and must NOT be displayed.
 *   - Professional sensor limit is 2,500 in the enforced backend catalog
 *     (spec draft said 3,000) — flagging: update BOTH places together if the
 *     product owner really wants 3K sensors.
 *   - Collector-run limits (5K / 50K / 1.0M) match the backend seed exactly.
 */

export type PlanId = "starter" | "professional" | "enterprise";

/** Machine-readable tier keys accepted by POST /api/billing/checkout. */
export type BackendTier = "starter" | "pro" | "enterprise";

export interface PlanFeatureLimits {
  seats: number;
  devices: number;
  sensors: number;
  sources: number;
  apiRequestsPerMonth: string;   // display string, e.g. "50K"
  alertsPerMonth: string;        // e.g. "25K"
  collectorRunsPerMonth: string; // e.g. "5K" — CONFIRMED against backend seed
                                 // (5000 / 50_000 / 1_000_000)
  windowsMonitoring: "Yes" | "No" | "Limited";
  snmp: "Yes" | "No" | "Limited";
  cloudMonitoring: "Yes" | "No" | "Limited";
  aiRca: "Yes" | "No" | "Limited" | "Advanced";
  mspFeatures: "Full" | "Limited" | "—";
  sso: "Yes" | "—";
  auditLog: "Yes" | "—";
}

export interface Plan {
  id: PlanId;
  /** Backend tier key ("starter" | "pro" | "enterprise") sent to checkout/select APIs. */
  tier: BackendTier;
  name: string;
  recommended: boolean;
  tagline: string;
  priceMonthly: number; // USD
  priceAnnual: number;  // USD — synced to backend DEFAULT_PLANS (see file header)
  featuresListed: string[]; // short checklist shown on the plan card itself
  limits: PlanFeatureLimits;
}

export const PLANS: Record<PlanId, Plan> = {
  starter: {
    id: "starter",
    tier: "starter",
    name: "Starter",
    recommended: false,
    tagline: "For small environments getting started with infrastructure monitoring.",
    priceMonthly: 49,
    priceAnnual: 490,
    featuresListed: [
      "5 seats",
      "25 devices · 250 sensors",
      "5 monitoring sources",
      "50K API requests/mo",
      "25K alerts/mo",
      "No SSO",
    ],
    limits: {
      seats: 5,
      devices: 25,
      sensors: 250,
      sources: 5,
      apiRequestsPerMonth: "50K",
      alertsPerMonth: "25K",
      collectorRunsPerMonth: "5K",
      windowsMonitoring: "Yes",
      snmp: "Yes",
      cloudMonitoring: "Limited",
      aiRca: "Yes",
      mspFeatures: "—",
      sso: "—",
      auditLog: "—",
    },
  },
  professional: {
    id: "professional",
    tier: "pro",
    name: "Professional",
    recommended: true,
    tagline: "For growing IT teams and MSPs that need advanced correlation and automation.",
    priceMonthly: 199,
    priceAnnual: 1990, // matches backend price_annual=1_990 (NOT 199*12=2388)
    featuresListed: [
      "25 seats",
      "250 devices · 2.5K sensors", // backend-enforced sensor_limit=2500 (flagged vs. spec's 3K)
      "25 monitoring sources",
      "500K API requests/mo",
      "250K alerts/mo",
      "Audit log included",
    ],
    limits: {
      seats: 25,
      devices: 250,
      sensors: 2500, // backend-enforced value; see file-header FLAG about "3K"
      sources: 25,
      apiRequestsPerMonth: "500K",
      alertsPerMonth: "250K",
      collectorRunsPerMonth: "50K",
      windowsMonitoring: "Yes",
      snmp: "Yes",
      cloudMonitoring: "Yes",
      aiRca: "Yes",
      mspFeatures: "Limited",
      sso: "—",
      auditLog: "Yes",
    },
  },
  enterprise: {
    id: "enterprise",
    tier: "enterprise",
    name: "Enterprise",
    recommended: false,
    tagline: "For MSPs/enterprise portfolios: high-scale monitoring, custom limits, SSO, audit, custom SLAs.",
    priceMonthly: 999,
    priceAnnual: 9990, // matches backend price_annual=9_990 (NOT 999*12=10788)
    featuresListed: [
      "500 seats",
      "10K devices · 100K sensors",
      "250 monitoring sources",
      "5.0M API requests/mo",
      "2.5M alerts/mo",
      "SSO & custom SLAs",
    ],
    limits: {
      seats: 500,
      devices: 10000,
      sensors: 100000,
      sources: 250,
      apiRequestsPerMonth: "5.0M",
      alertsPerMonth: "2.5M",
      collectorRunsPerMonth: "1.0M",
      windowsMonitoring: "Yes",
      snmp: "Yes",
      cloudMonitoring: "Yes",
      aiRca: "Advanced",
      mspFeatures: "Full",
      sso: "Yes",
      auditLog: "Yes",
    },
  },
};

/** Canonical column/card ordering for every surface that lists plans. */
export const PLAN_IDS: readonly PlanId[] = ["starter", "professional", "enterprise"];

/** A capability row of the Compare Plans table. Add/remove rows HERE ONLY. */
export interface CompareRow {
  label: string;
  key: keyof PlanFeatureLimits;
}

export const COMPARE_ROWS: readonly CompareRow[] = [
  { label: "Seats", key: "seats" },
  { label: "Devices", key: "devices" },
  { label: "Sensors", key: "sensors" },
  { label: "Sources", key: "sources" },
  { label: "API Requests", key: "apiRequestsPerMonth" },
  { label: "Alerts", key: "alertsPerMonth" },
  { label: "Collector Runs", key: "collectorRunsPerMonth" },
  { label: "Windows Monitoring", key: "windowsMonitoring" },
  { label: "SNMP", key: "snmp" },
  { label: "Cloud Monitoring", key: "cloudMonitoring" },
  { label: "AI RCA", key: "aiRca" },
  { label: "MSP Features", key: "mspFeatures" },
  { label: "SSO", key: "sso" },
  { label: "Audit Log", key: "auditLog" },
];

const TIER_TO_PLAN_ID: Record<string, PlanId> = {
  starter: "starter",
  pro: "professional",
  professional: "professional",
  enterprise: "enterprise",
};

/**
 * Map any backend tier string (subscription.plan_tier / entitlements.plan_tier)
 * onto the frontend PlanId. Returns null for unknown/absent tiers so callers
 * can fall back to server-provided labels.
 */
export function derivePlanIdFromTier(
  tier: string | null | undefined,
): PlanId | null {
  if (!tier) return null;
  return TIER_TO_PLAN_ID[tier.toLowerCase()] ?? null;
}
