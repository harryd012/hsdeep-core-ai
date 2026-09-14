"use client";

import { useState } from "react";
import { Check, ArrowRight } from "lucide-react";
import { PLANS, PLAN_IDS, type Plan, type PlanId } from "@/lib/plans";
import { openChoosePlanModal } from "./ChoosePlanModal";
import PlanCard from "./PlanCard";

interface PlanComparisonProps {
  currentPlanId: PlanId | null;
  isAnnual: boolean;
  onBillingToggle: (annual: boolean) => void;
}

export default function PlanComparison({ currentPlanId, isAnnual, onBillingToggle }: PlanComparisonProps) {
  const [hoveredPlan, setHoveredPlan] = useState<PlanId | null>(null);

  const getPrice = (plan: Plan) => isAnnual ? plan.priceAnnual : plan.priceMonthly;

  const getDiffSummary = (plan: Plan): string[] => {
    if (!currentPlanId) return [];
    const current = PLANS[currentPlanId];
    const diffs: string[] = [];
    if (plan.limits.devices > current.limits.devices) {
      diffs.push(`+${plan.limits.devices - current.limits.devices} devices`);
    }
    if (plan.limits.sensors > current.limits.sensors) {
      diffs.push(`+${(plan.limits.sensors - current.limits.sensors).toLocaleString()} sensors`);
    }
    if (plan.limits.seats > current.limits.seats) {
      diffs.push(`+${plan.limits.seats - current.limits.seats} seats`);
    }
    return diffs;
  };

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, marginBottom: 20 }}>
        <span style={{ fontSize: 13, fontWeight: !isAnnual ? 700 : 400, color: !isAnnual ? "var(--cyan)" : "var(--muted)" }}>
          Monthly
        </span>
        <button onClick={() => onBillingToggle(!isAnnual)} aria-label="Toggle billing" style={{ position: "relative", width: 48, height: 26, borderRadius: 13, border: "1px solid var(--line)", background: isAnnual ? "var(--cyan)" : "rgba(255,255,255,0.1)", cursor: "pointer", padding: 0 }}>
          <div style={{ position: "absolute", top: 3, left: isAnnual ? 24 : 3, width: 18, height: 18, borderRadius: 9, background: "#fff", transition: "left 0.2s" }} />
        </button>
        <span style={{ fontSize: 13, fontWeight: isAnnual ? 700 : 400, color: isAnnual ? "var(--cyan)" : "var(--muted)" }}>
          Annual
        </span>
        {isAnnual && <span style={{ fontSize: 11, fontWeight: 700, color: "var(--green)", background: "rgba(0,211,141,0.12)", padding: "3px 8px", borderRadius: 4 }}>Save ~20%</span>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
        {PLAN_IDS.map((planId) => (
          <PlanCard key={planId} plan={PLANS[planId]} isActive={planId === currentPlanId} isAnnual={isAnnual} isHovered={hoveredPlan === planId} diffSummary={hoveredPlan === planId && planId !== currentPlanId ? getDiffSummary(PLANS[planId]) : []} onHover={() => setHoveredPlan(planId)} onLeave={() => setHoveredPlan(null)} />
        ))}
      </div>
    </div>
  );
}