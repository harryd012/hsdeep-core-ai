"use client";

import DashboardMasthead from "./DashboardMasthead";
import DashboardFooter from "./DashboardFooter";
import OperationalHealth from "./OperationalHealth";
import AlertSummaryPanel from "./AlertSummaryPanel";
import AiOperations from "./AiOperations";
import InfrastructureOverview from "./InfrastructureOverview";
import ChartsPanel from "./ChartsPanel";
import ActivityFeed from "./ActivityFeed";
import CommandCenterTopology from "./CommandCenterTopology";
import { useDashboardData } from "@/hooks/useDashboardData";

/**
 * CommandCenter — the /dashboard page composition.
 *
 * SEMANTIC ZONES (single source of truth = useDashboardData):
 *
 *   HEADER            → DashboardMasthead (tenant/user/API context)
 *   OPERATIONAL HEALTH→ OperationalHealth KPI strip + AlertSummaryPanel
 *   AI OPERATIONS     → AiOperations (summary + compact copilot)
 *   INFRASTRUCTURE    → InfrastructureOverview (tabbed)
 *   TRENDS + ACTIVITY → ChartsPanel (one chart) + ActivityFeed
 *   FOOTER            → DashboardFooter (existing routes/links only)
 *
 * All data flows through dashboardDataAdapter → semantic models; child
 * components are presentational and fetch nothing. The detailed AI workforce
 * view lives on /ai-copilot; detailed infrastructure on /infrastructure etc.
 */
export default function CommandCenter() {
  const {
    header,
    operationalHealth,
    alertSummary,
    aiOperations,
    infrastructure,
    trends,
    activity,
    states,
  } = useDashboardData();

  return (
    <div className="hs-dashboard">
      {/* ZONE 0 — HEADER */}
      <DashboardMasthead header={header} />

      {/* ZONE 1 — OPERATIONAL HEALTH (one row: 6 KPIs + compact alert summary) */}
      <section className="hs-zone hs-zone--health" aria-label="Operational Health">
        <h2 className="hs-zone__title">OPERATIONAL HEALTH</h2>
        <div className="hs-zone__status-row">
          <OperationalHealth data={operationalHealth} state={states.operationalHealth} />
          <AlertSummaryPanel data={alertSummary} state={states.alertSummary} />
        </div>
      </section>

      {/* ZONE 2 — AI OPERATIONS (summary + compact copilot + live agent topology) */}
      <section className="hs-zone hs-zone--ai" aria-label="AI Operations">
        <AiOperations data={aiOperations} state={states.aiOperations} />
        {/* Shared AI Control Plane topology — the exact same real agents,
            managers, tasks and live states as /ai-copilot (same cache /
            useCommandCenterOverview). Rendering it here does not add a fetch. */}
        <CommandCenterTopology />
      </section>

      {/* ZONE 3 — INFRASTRUCTURE (tabbed overview) */}
      <section className="hs-zone hs-zone--infrastructure" aria-label="Infrastructure">
        <h2 className="hs-zone__title">INFRASTRUCTURE</h2>
        <InfrastructureOverview data={infrastructure} state={states.infrastructure} />
      </section>

      {/* ZONE 4 — TRENDS + ACTIVITY (70/30) */}
      <section className="hs-zone hs-zone--trends" aria-label="Trends and Activity">
        <h2 className="hs-zone__title">TRENDS &amp; ACTIVITY</h2>
        <div className="hs-zone__trends-grid">
          <ChartsPanel charts={trends} state={states.trends} />
          <ActivityFeed items={activity} state={states.activity} />
        </div>
      </section>

      {/* ZONE 5 — FOOTER */}
      <DashboardFooter />
    </div>
  );
}
