"use client";

import { useState, useEffect } from "react";
import SubscriptionPlansSection from "@/components/subscription/SubscriptionPlansSection";
import SubscriptionStatusPanel from "@/components/dashboard/SubscriptionStatusPanel";
import PlanComparison from "@/components/subscription/PlanComparison";
import BillingHistory from "@/components/subscription/BillingHistory";
import PaymentMethod from "@/components/subscription/PaymentMethod";
import { subscribeToSubscription } from "@/lib/subscription-store";
import { derivePlanIdFromTier, type PlanId } from "@/lib/plans";
import type { SubscriptionWithPlan } from "@/lib/usage-api";

/**
 * SUBSCRIPTION — plan selection + current subscription status.
 *
 * Enhanced with animated usage bars, interactive plan comparison,
 * billing history, payment method management, and live trial countdown.
 */
export default function SubscriptionPageClient() {
  const [subscription, setSubscription] = useState<SubscriptionWithPlan | null>(null);
  const [isAnnual, setIsAnnual] = useState(false);

  useEffect(
    () => subscribeToSubscription((s) => setSubscription(s.subscription)),
    [],
  );

  const currentPlanId = derivePlanIdFromTier(subscription?.plan_tier) as PlanId | null;

  return (
    <div className="page-shell" style={{ paddingBottom: "var(--copilot-bar-height)", background: "#07131b" }}>
      {/* Page Header */}
      <div style={{
        borderBottom: "1px solid var(--line)",
        background: "rgba(7, 19, 27, 0.97)",
        backdropFilter: "blur(10px)",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px" }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800, color: "var(--text)", fontFamily: "var(--font-display)" }}>
            Subscription
          </h1>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--muted)" }}>
            Choose a plan, upgrade, or manage your billing. Payments are processed securely via Stripe.
          </p>
        </div>
      </div>

      {/* Content */}
      <div style={{
        maxWidth: 1200,
        margin: "0 auto",
        padding: "24px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 24,
      }}>
        {/* Status panel with animated usage bars */}
        <SubscriptionStatusPanel />

        {/* Interactive plan comparison */}
        <div style={{
          padding: 20,
          borderRadius: 10,
          border: "1px solid var(--line)",
          background: "rgba(7, 19, 27, 0.4)",
        }}>
          <h2 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 700, color: "var(--cyan)", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Available Plans
          </h2>
          <PlanComparison
            currentPlanId={currentPlanId}
            isAnnual={isAnnual}
            onBillingToggle={setIsAnnual}
          />
        </div>

        {/* Payment method */}
        <div style={{
          padding: 20,
          borderRadius: 10,
          border: "1px solid var(--line)",
          background: "rgba(7, 19, 27, 0.4)",
        }}>
          <PaymentMethod stripeCustomerId={subscription?.stripe_customer_id} />
        </div>

        {/* Billing history */}
        <div style={{
          padding: 20,
          borderRadius: 10,
          border: "1px solid var(--line)",
          background: "rgba(7, 19, 27, 0.4)",
        }}>
          <BillingHistory />
        </div>

        {/* Legacy plans section (cancel/change) */}
        <SubscriptionPlansSection />
      </div>
    </div>
  );
}
