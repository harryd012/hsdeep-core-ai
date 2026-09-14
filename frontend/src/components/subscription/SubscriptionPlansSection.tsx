"use client";

/**
 * SUBSCRIPTION PLANS SECTION — /subscription page active-plan display.
 *
 * The active plan is resolved as PLANS[derivePlanIdFromTier(plan_tier)], so
 * every name/price/limit shown here comes from the single PLANS config in
 * @/lib/plans (no duplicated limits object, no separate fetch for catalog
 * data). "CHOOSE A PLAN" opens the shared ChoosePlanModal instead of
 * rendering a second copy of plan-card markup. Cancel uses the existing
 * billing state machine endpoint and broadcasts via the shared store.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { cancelSubscription } from "@/lib/usage-api";
import {
  subscribeToSubscription,
  refreshSubscription,
  setSubscription as pushSubscription,
} from "@/lib/subscription-store";
import type { SubscriptionWithPlan } from "@/lib/usage-api";
import { PLANS, COMPARE_ROWS, derivePlanIdFromTier } from "@/lib/plans";
import ChoosePlanModal, { openChoosePlanModal } from "./ChoosePlanModal";

const CANCELABLE = new Set(["trialing", "active", "past_due", "incomplete"]);

export default function SubscriptionPlansSection() {
  const [currentSub, setCurrentSub] = useState<SubscriptionWithPlan | null>(null);
  const [subError, setSubError] = useState(false);
  const [settled, setSettled] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => subscribeToSubscription((s) => {
    setCurrentSub(s.subscription);
    setSubError(Boolean(s.error));
    if (!s.error) setSettled(true);
  }), []);

  useEffect(() => {
    void refreshSubscription().finally(() => setSettled(true));
  }, []);

  // Single source of truth: currentPlan is looked up in PLANS via the
  // backend tier — never assembled from ad-hoc fields.
  const currentPlanId = derivePlanIdFromTier(currentSub?.plan_tier);
  const currentPlan = currentPlanId ? PLANS[currentPlanId] : null;
  const isAnnual = currentSub?.billing_interval === "year";

  const handleCancel = useCallback(async () => {
    if (!currentSub) return;
    if (!confirm("Cancel subscription? This will stop future billing but keep read access.")) return;
    setCancelling(true);
    setNotice(null);
    try {
      const updated = await cancelSubscription("User requested cancellation via UI");
      setCurrentSub(updated);
      pushSubscription(updated); // broadcast to dashboard card + status panel
      setNotice("Subscription canceled.");
    } catch {
      setNotice("Could not cancel subscription.");
    } finally {
      setCancelling(false);
    }
  }, [currentSub]);

  return (
    <div className="panel" style={{ padding: 16 }}>
      <div style={{
        display: "flex", alignItems: "center",
        justifyContent: "space-between", gap: 12,
        marginBottom: 14, flexWrap: "wrap",
      }}>
        <h2 style={{ color: "var(--cyan)", fontSize: 15, margin: 0 }}>YOUR PLAN</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {currentSub && CANCELABLE.has(currentSub.status) && (
            <button onClick={handleCancel} disabled={cancelling} className="btn" style={{ fontSize: 12 }}>
              {cancelling ? (
                <>
                  <Loader2 size={13} style={{ animation: "spinReverse .9s linear infinite" }} />{" "}
                  Cancelling…
                </>
              ) : "Cancel subscription"}
            </button>
          )}
          {/* Same modal everywhere — never a second copy of the card markup */}
          <button
            onClick={openChoosePlanModal}
            className="btn btn-primary"
            style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 8 }}
          >
            {currentPlan ? "CHANGE PLAN" : "CHOOSE A PLAN"}
          </button>
        </div>
      </div>

      <ChoosePlanModal />

      {subError && settled && (
        <p style={{ color: "var(--red)", fontSize: 12 }}>
          Unable to load your subscription right now.
        </p>
      )}
      {!settled && !subError && !currentPlan && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading your plan…</p>
      )}

      {notice && (
        <p style={{
          color: notice.startsWith("Could not") ? "var(--red)" : "var(--green)",
          fontSize: 12, marginTop: 0,
        }}>
          {notice}
        </p>
      )}

      {/* Active-plan display — rendered directly from PLANS[currentPlanId] */}
      {currentPlan && (
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <b style={{ fontSize: 22 }}>{currentPlan.name}</b>
            {currentSub && (
              <span style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase" }}>
                {currentSub.status}
                {" · "}
                {isAnnual
                  ? `$${currentPlan.priceAnnual.toLocaleString()}/yr`
                  : `$${currentPlan.priceMonthly}/mo`}
              </span>
            )}
          </div>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: "4px 0 14px" }}>
            {currentPlan.tagline}
          </p>

          {/* Limits grid straight from PlanFeatureLimits */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: "10px 16px",
          }}>
            {COMPARE_ROWS.slice(0, 7).map((row) => (
              <div key={row.key} style={{ fontSize: 12 }}>
                <div style={{ color: "var(--muted)" }}>{row.label}</div>
                <b>{String(currentPlan.limits[row.key])}</b>
              </div>
            ))}
          </div>

          <ul style={{
            listStyle: "none", margin: "16px 0 0", padding: 0,
            display: "flex", flexDirection: "column", gap: 6,
          }}>
            {currentPlan.featuresListed.map((f) => (
              <li key={f} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                <Check size={13} color="var(--green)" />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
