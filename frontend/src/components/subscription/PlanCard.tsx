"use client";

import { Check, ArrowRight } from "lucide-react";
import { type Plan } from "@/lib/plans";
import { openChoosePlanModal } from "./ChoosePlanModal";

interface PlanCardProps {
  plan: Plan;
  isActive: boolean;
  isAnnual: boolean;
  isHovered: boolean;
  diffSummary: string[];
  onHover: () => void;
  onLeave: () => void;
}

export default function PlanCard({ plan, isActive, isAnnual, isHovered, diffSummary, onHover, onLeave }: PlanCardProps) {
  const price = isAnnual ? plan.priceAnnual : plan.priceMonthly;
  const priceLabel = isAnnual ? "/yr" : "/mo";

  return (
    <div
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      style={{
        position: "relative",
        padding: 20,
        borderRadius: 10,
        border: isActive ? "2px solid var(--cyan)" : "1px solid var(--line)",
        background: isActive ? "rgba(0,220,255,0.06)" : "rgba(7, 19, 27, 0.6)",
        cursor: "pointer",
        transition: "all 0.2s",
        boxShadow: isActive ? "0 0 20px rgba(0,220,255,0.15)" : "none",
      }}
    >
      {plan.recommended && (
        <span style={{ position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)", fontSize: 10, fontWeight: 700, color: "#02121b", background: "var(--cyan)", padding: "2px 10px", borderRadius: 4, whiteSpace: "nowrap" }}>
          RECOMMENDED
        </span>
      )}
      <h3 style={{ margin: "0 0 4px", fontSize: 16, fontWeight: 700, color: "var(--text)" }}>{plan.name}</h3>
      <p style={{ margin: "0 0 12px", fontSize: 11, color: "var(--muted)", lineHeight: 1.4 }}>{plan.tagline}</p>
      <div style={{ marginBottom: 14 }}>
        <span style={{ fontSize: 28, fontWeight: 800, color: "var(--text)" }}>${price.toLocaleString()}</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{priceLabel}</span>
      </div>
      <ul style={{ listStyle: "none", margin: "0 0 16px", padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {plan.featuresListed.slice(0, 5).map((f, i) => (
          <li key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <Check size={12} style={{ color: "var(--green)", flexShrink: 0 }} />
            <span style={{ color: "var(--text)" }}>{f}</span>
          </li>
        ))}
      </ul>
      {isHovered && diffSummary.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--cyan)", marginBottom: 10, padding: "6px 8px", borderRadius: 4, background: "rgba(0,220,255,0.08)" }}>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>vs your plan:</div>
          {diffSummary.map((d, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}><ArrowRight size={10} /> {d}</div>
          ))}
        </div>
      )}
      <button
        onClick={openChoosePlanModal}
        style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: isActive ? "var(--cyan)" : "transparent", color: isActive ? "#02121b" : "var(--cyan)", fontSize: 12, fontWeight: 600, cursor: "pointer", border: isActive ? "none" : "1px solid var(--cyan)" }}
      >
        {isActive ? "Current Plan" : "Switch Plan"}
      </button>
    </div>
  );
}
