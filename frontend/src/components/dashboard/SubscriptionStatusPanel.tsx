"use client";

import { memo, useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { CreditCard, AlertTriangle, CheckCircle2, ChevronRight } from "lucide-react";
import {
  getEntitlements,
  Entitlements,
  type SubscriptionWithPlan,
} from "@/lib/usage-api";
import {
  subscribeToSubscription,
  refreshSubscription,
} from "@/lib/subscription-store";
import { openChoosePlanModal } from "@/components/subscription/ChoosePlanModal";
import { PLANS, derivePlanIdFromTier } from "@/lib/plans";
import { useAutoRefresh } from "@/hooks/useAutoRefresh";
import Panel from "./Panel";
import UsageBar from "@/components/subscription/UsageBar";
import TrialCountdown from "@/components/subscription/TrialCountdown";
import UsageDrilldown from "@/components/subscription/UsageDrilldown";

const STATUS_LABELS: Record<string, string> = {
  trialing: "Trialing",
  active: "Active",
  past_due: "Past Due",
  suspended: "Suspended",
  canceled: "Canceled",
  incomplete: "Incomplete",
};

const PROBLEM_STATUSES = new Set(["past_due", "suspended", "canceled", "incomplete"]);

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
}

const SubscriptionStatusPanel = memo(function SubscriptionStatusPanel() {
  const [subscription, setSubscription] = useState<SubscriptionWithPlan | null>(null);
  const [entitlements, setEntitlements] = useState<Entitlements | null>(null);
  const [subError, setSubError] = useState(false);
  const [entError, setEntError] = useState(false);
  const [subLoaded, setSubLoaded] = useState(false);
  const [entLoaded, setEntLoaded] = useState(false);
  const [drilldownResource, setDrilldownResource] = useState<{ label: string; used: number; limit: number } | null>(null);

  useEffect(
    () =>
      subscribeToSubscription((s) => {
        setSubscription(s.subscription);
        setSubError(Boolean(s.error));
        setSubLoaded(true);
      }),
    [],
  );

  useAutoRefresh(async () => {
    getEntitlements()
      .then((ent) => { setEntitlements(ent); setEntError(false); setEntLoaded(true); })
      .catch(() => { setEntError(true); setEntLoaded(true); });
    void refreshSubscription();
  });

  const handleUsageClick = useCallback((label: string, used: number, limit: number) => {
    setDrilldownResource({ label, used, limit });
  }, []);

  const statusLabel = subscription ? (STATUS_LABELS[subscription.status] ?? subscription.status.toUpperCase()) : null;
  const intervalLabel =
    subscription?.billing_interval === "year" ? "ANNUAL" :
    subscription?.billing_interval === "month" ? "MONTHLY" : null;
  const periodStart = entitlements?.current_period_start ?? subscription?.current_period_start ?? null;
  const periodEnd = entitlements?.current_period_end ?? subscription?.current_period_end ?? null;
  // Plan NAME resolves through the single-source PLANS config first
  // (frontend key derived from backend tier); server labels remain fallback.
  const configPlanId = derivePlanIdFromTier(subscription?.plan_tier);
  const planName =
    (configPlanId ? PLANS[configPlanId].name : null) ??
    subscription?.plan_name ??
    subscription?.plan_tier ?? "NO PLAN";
  const isProblem = subscription ? PROBLEM_STATUSES.has(subscription.status) : false;

  return (
    <Panel
      title="SUBSCRIPTION STATUS"
      icon={<CreditCard size={18} />}
      status={isProblem ? "critical" : subscription?.status === "trialing" ? "warning" : "healthy"}
    >
      {/* Problem banner (exact backend states only) */}
      {subscription && isProblem && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
          borderRadius: 8, marginBottom: 16,
          background: "rgba(255,80,80,.12)", border: "1px solid var(--red)",
        }}>
          <AlertTriangle size={18} color="var(--red)" />
          <div>
            <b style={{ color: "var(--red)", fontSize: 13 }}>
              SUBSCRIPTION {statusLabel?.toUpperCase()}
            </b>
            <p style={{ color: "var(--muted)", fontSize: 12, margin: "2px 0 0" }}>
              {subscription.status === "past_due" && "Your subscription requires attention. Please review your billing information."}
              {subscription.status === "suspended" && "Access to subscription-controlled resources may be restricted."}
              {subscription.status === "canceled" && "Your subscription is no longer active."}
              {subscription.status === "incomplete" && "Your subscription setup is incomplete. Please review your billing information."}
            </p>
          </div>
        </div>
      )}
      {subscription?.status === "trialing" && (
        <TrialCountdown trialEndsAt={subscription.trial_ends_at} />
      )}

      {/* Current plan / status / dates */}
      {subError && (
        <p style={{ color: "var(--red)", fontSize: 12 }}>Unable to load subscription information.</p>
      )}
      {!subError && !subLoaded && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading subscription…</p>
      )}
      {!subError && subLoaded && !subscription && (
        <div style={{ marginBottom: 12 }}>
          <p style={{ color: "var(--muted)", fontSize: 12, marginBottom: 8 }}>No active subscription.</p>
          {/* Opens the shared config-driven ChoosePlanModal mounted on this page */}
          <button onClick={openChoosePlanModal} className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block" }}>
            CHOOSE A PLAN
          </button>
        </div>
      )}
      {subscription && (
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 14, marginBottom: 16,
        }}>
          <div>
            <small style={{ color: "var(--muted)", fontSize: 11 }}>PLAN</small>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#c5f3ff" }}>{planName}</div>
          </div>
          <div>
            <small style={{ color: "var(--muted)", fontSize: 11 }}>STATUS</small>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>{statusLabel}</div>
          </div>
          {intervalLabel && (
            <div>
              <small style={{ color: "var(--muted)", fontSize: 11 }}>BILLING</small>
              <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>{intervalLabel}</div>
            </div>
          )}
          {subscription.status === "trialing" && subscription.trial_ends_at && (
            <div>
              <small style={{ color: "var(--muted)", fontSize: 11 }}>TRIAL ENDS</small>
              <div style={{ fontSize: 13, marginTop: 4 }}>{formatDate(subscription.trial_ends_at)}</div>
            </div>
          )}
          {!["trialing"].includes(subscription.status) && periodEnd && (
            <div>
              <small style={{ color: "var(--muted)", fontSize: 11 }}>RENEWAL</small>
              <div style={{ fontSize: 13, marginTop: 4 }}>{formatDate(periodEnd)}</div>
            </div>
          )}
        </div>
      )}

      {/* Usage limits (generic over backend limits[]) */}
      <h3 style={{ color: "#c5f3ff", fontSize: 12, marginBottom: 8 }}>USAGE LIMITS</h3>
      {entError && (
        <p style={{ color: "var(--red)", fontSize: 12 }}>Unable to load plan usage.</p>
      )}
      {!entError && !entLoaded && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading usage…</p>
      )}
      {!entError && entLoaded && !entitlements && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Usage details will appear once a subscription is active.</p>
      )}
      {entitlements && (
        <div style={{ display: "grid", gap: 12 }}>
          {entitlements.limits.map((l) => (
            <div key={l.resource}>
              <UsageBar
                label={l.label}
                used={l.used}
                limit={l.limit}
                onClick={() => handleUsageClick(l.label, l.used, l.limit)}
              />
              {l.pct >= 90 && (
                <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <button
                    onClick={openChoosePlanModal}
                    style={{ fontSize: 11, color: "var(--cyan)", background: "none", border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}
                  >
                    Upgrade to increase limit
                  </button>
                  <ChevronRight size={10} style={{ color: "var(--cyan)" }} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: 16 }}>
        <Link href="/subscription" className="btn btn-secondary" style={{ textDecoration: "none", display: "inline-block" }}>
          MANAGE SUBSCRIPTION
        </Link>
      </div>

      {/* Drill-down side panel */}
      {drilldownResource && (
        <UsageDrilldown
          resource={drilldownResource.label}
          used={drilldownResource.used}
          limit={drilldownResource.limit}
          onClose={() => setDrilldownResource(null)}
        />
      )}
    </Panel>
  );
});

export default SubscriptionStatusPanel;