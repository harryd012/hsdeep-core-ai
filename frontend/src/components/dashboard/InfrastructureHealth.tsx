"use client";

import { memo } from "react";
import type { InfrastructureHealthRow } from "@/lib/dashboard/dashboardModels";
import Panel from "./Panel";

/**
 * InfrastructureHealth — PRESENTATIONAL (Zone 3).
 *
 * Semantic purpose: "What percentage of infrastructure is healthy?"
 * Health rows are computed by the dashboard data adapter from the SAME
 * backend responses used by every other dashboard section (single source of
 * truth) and passed in via props. Markup/classes unchanged.
 */
const InfrastructureHealth = memo(function InfrastructureHealth({
  rows,
}: {
  rows: InfrastructureHealthRow[];
}) {
  return (
    <Panel title="INFRASTRUCTURE HEALTH" status={panelStatus(rows)} className="infra-health">
      <div className="infra-health__rows">
        {rows.map((row) => (
          <div key={row.label} className="infra-health__row">
            <span className="infra-health__label">{row.label}</span>
            <div className="infra-health__bar">
              <div className="infra-health__fill" style={{ width: `${row.pct ?? 0}%`, backgroundColor: row.color }} />
            </div>
            <span className="infra-health__pct" style={{ color: row.color }}>
              {row.pct === null ? "—" : `${Math.round(row.pct)}%`}
            </span>
            <small className="infra-health__sub">{row.sub}</small>
          </div>
        ))}
      </div>
    </Panel>
  );
});

function panelStatus(rows: InfrastructureHealthRow[]): "critical" | "warning" | "healthy" {
  const pcts = rows.filter((r) => r.pct !== null).map((r) => r.pct as number);
  if (pcts.some((p) => p < 70)) return "critical";
  if (pcts.some((p) => p < 90)) return "warning";
  return "healthy";
}

export default InfrastructureHealth;
