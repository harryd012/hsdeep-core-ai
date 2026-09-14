"use client";

/**
 * PLAN SELECTOR — plan cards + Stripe Checkout hand-off.
 *
 * Fetches the public plan catalog via listPlans() and, on Select, calls
 * POST /api/billing/subscription/select. The backend resolves price/limits
 * server-side from its trusted catalog and returns one of:
 *   - checkout_required -> redirect the browser to checkout_url (Stripe)
 *   - active            -> plan applied immediately (free tier / dev mode)
 *   - contact_sales     -> payment provider not configured; show message
 *
 * A 404 on plans/entitlements is a lifecycle state, not an error — handled
 * with the apiFetchOrNull null-fold pattern (no raw error banners).
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import {
  listPlans,
  selectPlan,
  cancelSubscription,
  Plan,
  SelectPlanResponse,
  SubscriptionWithPlan,
} from "@/lib/usage-api";
import {
  subscribeToSubscription,
  refreshSubscription,
  setSubscription as pushSubscription,
} from "@/lib/subscription-store";
import PlanComparison from "./PlanComparison";

function formatFeatureList(features: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(features ?? {})) {
    if (value === true) out.push(key.replace(/_/g, " "));
    else if (typeof value === "string" || typeof value === "number") {
      out.push(`${key.replace(/_/g, " ")}: ${value}`);
    }
    // arrays/objects are skipped to keep cards compact
  }
  return out;
}

const fmtLimit = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`
  : n >= 1_000 ? `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}k`
  : String(n);

type Interval = "month" | "year";
type SelectState =
  | { kind: "idle" }
  | { kind: "loading"; tier: string }
  | { kind: "redirecting" }
  | { kind: "message"; text: string; tone: "ok" | "warn" };

export default function PlanSelector() {
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [plansError, setPlansError] = useState(false);
  const [interval, setInterval] = useState<Interval>("month");
  const [selectState, setSelectState] = useState<SelectState>({ kind: "idle" });
  const [currentSub, setCurrentSub] = useState<SubscriptionWithPlan | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Plans catalog loads independently. The CURRENT subscription now comes
    // from the shared store (same instance as the dashboard card and status
    // panel) instead of a private getSubscription() call — one request, one
    // source of truth, and select/cancel mutations propagate everywhere.
    listPlans()
      .then((p) => { if (!cancelled) setPlans(p.filter((pl) => pl.is_public)); })
      .catch(() => { if (!cancelled) setPlansError(true); });
    const unsubscribe = subscribeToSubscription((s) => {
      if (!cancelled) setCurrentSub(s.subscription);
    });
    return () => { cancelled = true; unsubscribe(); };
  }, []);

  const handleCancel = useCallback(async () => {
    if (!currentSub) return;
    if (!confirm("Cancel subscription? This will stop future billing but keep read access.")) return;
    setSelectState({ kind: "loading", tier: currentSub.plan_tier });
    try {
      const updated = await cancelSubscription("User requested cancellation via UI");
      setCurrentSub(updated);
      pushSubscription(updated); // broadcast cancel to dashboard card + status panel
      setSelectState({ kind: "message", text: `Subscription canceled.`, tone: "ok" });
    } catch (e) {
      setSelectState({ kind: "message", text: "Could not cancel subscription.", tone: "warn" });
    }
  }, [currentSub]);

  const handleSelect = useCallback(async (plan: Plan) => {
    setSelectState({ kind: "loading", tier: plan.tier });
    try {
      const res: SelectPlanResponse = await selectPlan(plan.tier, interval);
      if (res.status === "checkout_required" && res.checkout_url) {
        setSelectState({ kind: "redirecting" });
        window.location.href = res.checkout_url;
        return;
      }
      if (res.status === "active") {
        setSelectState({ kind: "message", text: res.message ?? `${plan.name} is now active.`, tone: "ok" });
        // Push the new plan into the shared store so the dashboard card and
        // status panel update instantly, then re-verify against the backend.
        void refreshSubscription();
        return;
      }
      // contact_sales
      setSelectState({ kind: "message", text: res.message ?? "This plan requires sales assistance.", tone: "warn" });
    } catch (e) {
      const maybeErr = e as { status?: number };
      const detail =
        maybeErr?.status === 404
          ? "No active subscription found for this workspace. Start from signup or contact support."
          : maybeErr?.status === 422
            ? "That plan has no price configured for the selected billing interval."
            : `Could not start checkout (${maybeErr?.status ?? "network"}). Please try again.`;
      setSelectState({ kind: "message", text: detail, tone: "warn" });
    }
  }, [interval]);

  if (plansError) {
    return (
      <div className="panel" style={{ padding: 16 }}>
        <h2 style={{ color: "var(--cyan)", fontSize: 15, margin: 0 }}>PLANS</h2>
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          Unable to load the plan catalog right now.
        </p>
      </div>
    );
  }

  if (!plans) {
    return (
      <div className="panel" style={{ padding: 16 }}>
        <h2 style={{ color: "var(--cyan)", fontSize: 15, margin: 0 }}>PLANS</h2>
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading plans…</p>
      </div>
    );
  }

  return (
    <div className="panel" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <h2 style={{ color: "var(--cyan)", fontSize: 15, margin: 0 }}>CHOOSE YOUR PLAN</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {currentSub && (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              Current: <b style={{ color: "var(--green)" }}>{currentSub.plan_name}</b>
            </div>
          )}
          {currentSub && currentSub.status !== "canceled" && (
            <button onClick={handleCancel} className="btn" style={{ fontSize: 12 }}>
              Cancel subscription
            </button>
          )}
        </div>

        {/* Monthly / annual toggle */}
        <div style={{ display: "flex", border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
          {(["month", "year"] as Interval[]).map((iv) => (
            <button
              key={iv}
              onClick={() => setInterval(iv)}
              disabled={selectState.kind === "loading" || selectState.kind === "redirecting"}
              style={{
                padding: "6px 14px", fontSize: 12, cursor: "pointer",
                border: "none",
                background: interval === iv ? "rgba(0,229,255,.15)" : "transparent",
                color: interval === iv ? "var(--cyan)" : "var(--muted)",
                fontWeight: interval === iv ? 700 : 400,
              }}
            >
              {iv === "month" ? "MONTHLY" : "ANNUAL"}
            </button>
          ))}
        </div>
      </div>

      {/* Status line for in-flight selection / result messages */}
      {selectState.kind === "redirecting" && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <Loader2 size={14} color="var(--cyan)" />
          <span style={{ color: "var(--cyan)", fontSize: 12 }}>Redirecting to secure checkout…</span>
        </div>
      )}
      {selectState.kind === "message" && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, marginBottom: 12, padding: "8px 12px",
          borderRadius: 8,
          background: selectState.tone === "ok" ? "rgba(0,255,170,.08)" : "rgba(255,176,32,.08)",
          border: `1px solid ${selectState.tone === "ok" ? "var(--green)" : "var(--amber)"}`,
        }}>
          <span style={{ color: selectState.tone === "ok" ? "var(--green)" : "var(--amber)", fontSize: 12 }}>
            {selectState.text}
          </span>
        </div>
      )}

      {/* Plan cards */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14,
      }}>
        {plans.map((plan) => {
          const isCurrent = currentSub?.plan_tier === plan.tier &&
            (currentSub.status === "active" || currentSub.status === "trialing");
          const busy = selectState.kind === "loading" && selectState.tier === plan.tier;
          const price = interval === "year" ? plan.price_annual : plan.price_monthly;
          const features = formatFeatureList(plan.features_json);

          return (
            <div key={plan.id} style={{
              display: "flex", flexDirection: "column", gap: 10,
              padding: 16, borderRadius: 10,
              border: `1px solid ${isCurrent ? "var(--green)" : "var(--line)"}`,
              background: "rgba(255,255,255,.02)",
            }}>
              {/* Header */}
              <div>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
                  <b style={{ color: "var(--cyan)", fontSize: 15 }}>{plan.name.toUpperCase()}</b>
                  {isCurrent && (
                    <span style={{ color: "var(--green)", fontSize: 11, fontWeight: 700 }}>CURRENT</span>
                  )}
                </div>
                {plan.description && (
                  <p style={{ color: "var(--muted)", fontSize: 12, margin: "4px 0 0" }}>{plan.description}</p>
                )}
              </div>

              {/* Price */}
              <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 26, fontWeight: 700 }}>${price.toFixed(0)}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>
                  /{interval === "year" ? "yr" : "mo"}
                </span>
                {interval === "year" && plan.price_monthly > 0 && (
                  <span style={{ color: "var(--green)", fontSize: 11 }}>
                    ~${(plan.price_annual / 12).toFixed(0)}/mo billed annually
                  </span>
                )}
              </div>

              {/* Limits */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", fontSize: 12 }}>
                <span style={{ color: "var(--muted)" }}>Devices: <b>{fmtLimit(plan.device_limit)}</b></span>
                <span style={{ color: "var(--muted)" }}>Sensors: <b>{fmtLimit(plan.sensor_limit)}</b></span>
                <span style={{ color: "var(--muted)" }}>API rate: <b>{fmtLimit(plan.api_rate_limit)}/min</b></span>
                <span style={{ color: "var(--muted)" }}>Collectors: <b>{plan.collector_limit}</b></span>
                <span style={{ color: "var(--muted)" }}>Seats: <b>{plan.seats}</b></span>
                {typeof plan.alert_limit === "number" && plan.alert_limit > 0 && (
                  <span style={{ color: "var(--muted)" }}>Alerts/mo: <b>{fmtLimit(plan.alert_limit)}</b></span>
                )}
              </div>

              {/* Feature list */}
              {features.length > 0 && (
                <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 5 }}>
                  {features.map((f) => (
                    <li key={f} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                      <Check size={13} color="var(--green)" />
                      <span style={{ color: "var(--muted)" }}>{f}</span>
                    </li>
                  ))}
                </ul>
              )}

              {/* Select button */}
              <button
                onClick={() => handleSelect(plan)}
                disabled={busy || selectState.kind === "redirecting" || isCurrent}
                className="btn btn-primary"
                style={{
                  marginTop: "auto",
                  opacity: isCurrent ? 0.55 : undefined,
                  cursor: isCurrent ? "default" : "pointer",
                  textDecoration: "none",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                }}
              >
                {busy && <Loader2 size={14} />}
                {isCurrent ? "CURRENT PLAN" : busy ? "STARTING…" : "SELECT"}
              </button>
            </div>
          );
        })}
      </div>
      
      {/* Comparison table */}
      <PlanComparison plans={plans} />
    </div>
  );
}

