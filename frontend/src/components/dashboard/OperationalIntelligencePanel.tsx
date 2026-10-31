"use client";

import { memo } from "react";
import CommandKpiCards from "@/components/dashboard/CommandKpiCards";
import { useOperationalIntelligence } from "@/hooks/useOperationalIntelligence";
import { buildOperationalIntelligenceKpis } from "@/lib/dashboard/operationalIntelligenceModel";

/**
 * OperationalIntelligencePanel — PHASE 6A Zone 3 (presentational shell).
 *
 * Fills the right-side OPERATIONAL INTELLIGENCE zone with real
 * backend-driven cards from GET /api/dashboard/operational-intelligence.
 * Presentational only: the hook above fetches, the pure builder maps, and
 * the EXISTING CommandKpiCards renderer draws — no new card chrome, no
 * fetching in components, no fabricated values.
 *
 * Honest states: loading shows "Loading operational intelligence…",
 * a failed read shows "Operational intelligence unavailable.", and
 * null rates (zero runs / zero sources / no timestamps) render as N/A
 * against each card's own definition — never 0% or 100%.
 */
const OperationalIntelligencePanel = memo(function OperationalIntelligencePanel() {
  const { data, isLoading, error } = useOperationalIntelligence();

  if (isLoading && data === undefined) {
    return <div className="op-health__status">Loading operational intelligence…</div>;
  }
  if (error) {
    return (
      <div className="op-health__status op-health__status--error">
        Operational intelligence unavailable.
      </div>
    );
  }

  const kpis = buildOperationalIntelligenceKpis(data);
  return <CommandKpiCards kpis={kpis} label="Operational intelligence KPIs" />;
});

export default OperationalIntelligencePanel;
