"use client";

/**
 * CHOOSE A PLAN MODAL — the single place in the app where plan cards are
 * rendered for selection, plus the Compare Plans table beneath them.
 *
 * ALL names, prices, taglines, checklists and capability limits come from
 * @/lib/plans (PLANS + COMPARE_ROWS). Nothing here may hardcode a plan name,
 * price or limit — editing that one config file updates this modal, the
 * dashboard Subscription stat card, and the /subscription page together.
 *
 * Any button anywhere can open this modal without prop-drilling:
 *   openChoosePlanModal()   // exported below
 *
 * Selecting a plan calls createCheckoutSession(plan.tier) (the backend tier
 * key, NOT the display id) and redirects to the returned Stripe Checkout URL.
 */

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import {
  createCheckoutSession,
  type SubscriptionWithPlan,
} from "@/lib/usage-api";
import {
  subscribeToSubscription,
  refreshSubscription,
} from "@/lib/subscription-store";
import {
  PLANS,
  PLAN_IDS,
  COMPARE_ROWS,
  derivePlanIdFromTier,
  type Plan,
} from "@/lib/plans";

const OPEN_EVENT = "hsdeep-core-ai:open-choose-plan-modal";

/** Open the shared "Choose a Plan" modal from any component. */
export function openChoosePlanModal(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(OPEN_EVENT));
  }
}

type CardBusy = { id: Plan["id"]; phase: "loading" | "redirecting" } | null;

export default function ChoosePlanModal() {
  // Visibility is driven purely by external events (no parent state), so any
  // button anywhere can open this modal without prop drilling.
  const [open, setOpen] = useState(false);
  const [currentSub, setCurrentSub] = useState<SubscriptionWithPlan | null>(null);
  const [subError, setSubError] = useState(false);
  // The store notifies synchronously on subscribe but its first network load
  // may still be in flight; only show the red "No active subscription" note
  // once a load has actually settled, so we never claim "no plan" during
  // boot or hide it behind a hang.
  const [settled, setSettled] = useState(false);
  const [busy, setBusy] = useState<CardBusy>(null);
  const [errFor, setErrFor] = useState<Plan["id"] | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const close = useCallback(() => {
    setOpen(false);
    setBusy(null);
    setErrFor(null);
    setErrMsg(null);
  }, []);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  useEffect(() => subscribeToSubscription((s) => {
    setCurrentSub(s.subscription);
    setSubError(Boolean(s.error));
  }), []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void refreshSubscription().finally(() => {
      if (!cancelled) setSettled(true);
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, close]);

  const handleSelect = useCallback(async (plan: Plan) => {
    setBusy({ id: plan.id, phase: "loading" });
    setErrFor(null);
    setErrMsg(null);
    try {
      const res = await createCheckoutSession(plan.tier, "month");
      if (!res.url) {
        throw new Error("No checkout URL was returned by the billing API.");
      }
      setBusy({ id: plan.id, phase: "redirecting" });
      window.location.href = res.url; // hand off to Stripe Checkout
    } catch (e) {
      const maybeErr = e as { status?: number; message?: string };
      const detail =
        maybeErr?.status === 401
          ? "Your session has expired. Please sign in again."
          : maybeErr?.status === 404
            ? "Billing is not available for this workspace yet. Please contact support."
            : maybeErr?.status === 422
              ? "This plan cannot be checked out right now. Please contact support."
              : `Could not start checkout (${maybeErr?.status ?? maybeErr?.message ?? "network"}). Please try again.`;
      setErrFor(plan.id);
      setErrMsg(detail);
      setBusy(null); // never fail silently, never leave a stuck spinner
    }
  }, []);

  if (!open) return null;

  const currentPlanId = derivePlanIdFromTier(currentSub?.plan_tier);
  const showNoSubNote = settled && !subError && !currentSub;

  return (
    <div
      onClick={close}
      style={{
        position: "fixed", inset: 0, zIndex: 300,
        background: "rgba(0,0,0,.65)", backdropFilter: "blur(3px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Choose a plan"
        style={{
          position: "relative",
          width: "100%", maxWidth: 1000, maxHeight: "90vh", overflowY: "auto",
          background: "#0f1720",
          border: "1px solid var(--line)",
          borderRadius: 12,
          padding: "24px 24px 28px",
          color: "#dbe7ee",
        }}
      >
        {/* Close */}
        <button
          onClick={close}
          aria-label="Close"
          style={{
            position: "absolute", top: 14, right: 14,
            width: 32, height: 32, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            background: "transparent", border: "none",
            color: "var(--muted)", borderRadius: 8,
          }}
        >
          <X size={20} />
        </button>

        {/* Header */}
        <h2 style={{
          margin: "0 0 6px", fontSize: 18, fontWeight: 700,
          textTransform: "uppercase", letterSpacing: 2,
        }}>
          CHOOSE A PLAN
        </h2>
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
          All plans include Windows monitoring, SNMP collection, AI root-cause analysis and the full NOC dashboard.
        </p>
        {/* Red note only while there is genuinely no active subscription */}
        {showNoSubNote && (
          <p style={{ margin: "8px 0 0", color: "var(--red)", fontSize: 12, fontWeight: 600 }}>
            No active subscription
          </p>
        )}
        {settled && subError && (
          <p style={{ margin: "8px 0 0", color: "var(--muted)", fontSize: 12 }}>
            Could not verify your current subscription status.
          </p>
        )}

        {/* Plan cards — driven entirely by PLANS config */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 16,
          marginTop: 20,
        }}>
          {PLAN_IDS.map((planId) => {
            const plan = PLANS[planId];
            const isCurrent =
              currentPlanId === plan.id &&
              currentSub != null &&
              currentSub.status !== "canceled";
            const isActiveLike =
              isCurrent &&
              (currentSub.status === "active" || currentSub.status === "trialing");
            const cardBusy = busy?.id === plan.id;

            return (
              <div
                key={plan.id}
                style={{
                  position: "relative",
                  display: "flex", flexDirection: "column", gap: 10,
                  padding: 16,
                  border: `1px solid ${plan.recommended ? "rgba(0,220,255,.55)" : "rgba(255,255,255,.10)"}`,
                  borderRadius: 10,
                  background: "rgba(255,255,255,.02)",
                }}
              >
                {/* RECOMMENDED badge — driven by plan.recommended, not card position */}
                {plan.recommended && (
                  <span style={{
                    position: "absolute", top: 10, right: 10,
                    padding: "3px 8px", borderRadius: 999,
                    background: "var(--cyan)", color: "#04121a",
                    fontSize: 10, fontWeight: 800, letterSpacing: .8,
                  }}>
                    RECOMMENDED
                  </span>
                )}
                {isCurrent && !plan.recommended && (
                  <span style={{
                    position: "absolute", top: 10, right: 10,
                    padding: "2px 8px", borderRadius: 999,
                    background: "transparent", border: "1px solid var(--green)",
                    color: "var(--green)",
                    fontSize: 10, fontWeight: 800, letterSpacing: .8,
                  }}>
                    CURRENT
                  </span>
                )}

                <b style={{ fontSize: 17, fontWeight: 700 }}>{plan.name}</b>
                <p style={{
                  margin: 0, color: "var(--muted)", fontSize: 12,
                  lineHeight: 1.5, minHeight: 36,
                }}>
                  {plan.tagline}
                </p>

                {/* Price — from config, never literal */}
                <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
                  <span style={{ fontSize: 30, fontWeight: 700 }}>${plan.priceMonthly}</span>
                  <span style={{ color: "var(--muted)", fontSize: 12 }}>/month</span>
                </div>
                <div style={{ marginTop: -6, color: "var(--muted)", fontSize: 12 }}>
                  or ${plan.priceAnnual.toLocaleString()}/year
                </div>

                {/* Checklist — from featuresListed */}
                <ul style={{
                  listStyle: "none", margin: 0, padding: 0,
                  display: "flex", flexDirection: "column", gap: 6,
                }}>
                  {plan.featuresListed.map((f) => (
                    <li
                      key={f}
                      style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}
                    >
                      <Check size={13} color="var(--green)" />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>

                {/* Inline error — surfaced inside the failing card, never silent */}
                {errFor === plan.id && errMsg && (
                  <p style={{ margin: 0, color: "var(--red)", fontSize: 11, lineHeight: 1.4 }}>
                    {errMsg}
                  </p>
                )}

                {/* SELECT PLAN — solid cyan on recommended, outline otherwise */}
                <button
                  onClick={() => handleSelect(plan)}
                  disabled={cardBusy || (isActiveLike && currentSub.status === "active")}
                  style={{
                    marginTop: "auto",
                    width: "100%",
                    padding: "10px 12px",
                    borderRadius: 8,
                    cursor: cardBusy ? "wait" : "pointer",
                    fontSize: 12,
                    fontWeight: 700,
                    letterSpacing: .6,
                    textTransform: "uppercase",
                    border: plan.recommended ? "none" : "1px solid rgba(0,220,255,.45)",
                    background: plan.recommended ? "var(--cyan)" : "transparent",
                    color: plan.recommended ? "#04121a" : "var(--cyan)",
                    opacity:
                      cardBusy || (isActiveLike && currentSub.status === "active") ? 0.7 : 1,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                  }}
                >
                  {cardBusy && (
                    <Loader2 size={14} style={{ animation: "spinReverse .9s linear infinite" }} />
                  )}
                  {isActiveLike && currentSub.status === "active"
                    ? "CURRENT PLAN"
                    : cardBusy && busy?.phase === "redirecting"
                      ? "REDIRECTING…"
                      : "SELECT PLAN"}
                </button>
              </div>
            );
          })}
        </div>

        <ComparePlansTable />
      </div>
    </div>
  );
}

/**
 * COMPARE PLANS — every row maps over COMPARE_ROWS (label + limits key), so
 * adding/removing a capability later means editing ONE array in plans.ts.
 */
function ComparePlansTable() {
  return (
    <div style={{ marginTop: 28 }}>
      <h3 style={{
        margin: "0 0 4px", color: "var(--cyan)",
        fontSize: 14, fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase",
      }}>
        COMPARE PLANS
      </h3>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 720, borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{
                textAlign: "left", padding: "10px 12px",
                color: "var(--muted)", fontSize: 12, fontWeight: 600,
              }}>
                Capability
              </th>
              {PLAN_IDS.map((planId) => (
                <th
                  key={planId}
                  style={{
                    textAlign: "left", padding: "10px 12px",
                    color: PLANS[planId].recommended ? "var(--cyan)" : "var(--muted)",
                    fontSize: 12, fontWeight: 600,
                  }}
                >
                  {PLANS[planId].name}
                </th>
              ))}
            </tr>
          </thead>
          {/* Horizontal row dividers only — no vertical borders */}
          <tbody>
            {COMPARE_ROWS.map((row) => (
              <tr key={row.key}>
                <td style={{
                  padding: "10px 12px", borderTop: "1px solid rgba(255,255,255,.06)",
                  color: "var(--muted)", fontSize: 13,
                }}>
                  {row.label}
                </td>
                {PLAN_IDS.map((planId) => {
                  const value = String(PLANS[planId].limits[row.key]);
                  return (
                    <td
                      key={`${planId}:${row.key}`}
                      style={{
                        padding: "10px 12px",
                        borderTop: "1px solid rgba(255,255,255,.06)",
                        fontSize: 13,
                        // "—" renders muted/dim; "Yes" and other values plain text
                        color: value === "—" ? "var(--muted)" : undefined,
                        opacity: value === "—" ? 0.55 : 1,
                      }}
                    >
                      {value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
