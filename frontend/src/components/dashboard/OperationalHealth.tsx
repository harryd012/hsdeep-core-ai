"use client";

import Link from "next/link";
import { memo } from "react";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  Globe2,
  HardDrive,
  Server,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import type { OperationalHealthData, OperationalHealthKpi, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * OperationalHealth — Zone 1 (presentational).
 *
 * Semantic purpose: "What is the health of my environment right now?"
 *
 * Renders the OperationalHealthKpi[] produced by the dashboard data adapter
 * from EXISTING backend responses. No fetching, no fabricated values.
 *
 * NULL vs ZERO (operational accuracy):
 *   - value === 0   → renders "0" (backend explicitly reports zero)
 *   - value === null → renders "N/A" (data currently unavailable)
 * These are visually distinct and never conflated.
 *
 * Per-KPI data ownership (documented in the adapter):
 *   Active Alerts      ← listAlerts(open) deduplicated by fingerprint
 *   Critical Incidents ← same alerts payload, severity == critical
 *   Devices Online     ← getDevices(), status == up
 *   Devices Offline    ← getDevices(), status down/warning
 *   Sensors Healthy    ← listSensors(), status == up
 *   Collector Success  ← getAutomationStatus(), success_rate_pct (24h)
 *   Sites Online       ← getNetworkStatus(), sites_online
 */

/** Presentation-only icon mapping (not data). */
const ICONS: Record<string, LucideIcon> = {
  "Active Alerts": Bell,
  "Critical Incidents": AlertTriangle,
  "Devices Online": Server,
  "Devices Offline": HardDrive,
  "Sensors Healthy": CheckCircle2,
  "Collector Success": Workflow,
  "Sites Online": Globe2,
};

const STATUS_CLASS: Record<string, string> = {
  healthy: "op-health__kpi--healthy",
  warning: "op-health__kpi--warning",
  critical: "op-health__kpi--critical",
  neutral: "op-health__kpi--neutral",
};

function Kpi({ kpi }: { kpi: OperationalHealthKpi }) {
  const Icon = ICONS[kpi.label] ?? Globe2;
  return (
    <li className={`op-health__kpi ${STATUS_CLASS[kpi.status]}`}>
      <Link href={kpi.href} className="op-health__link">
        <span className="op-health__icon"><Icon size={15} /></span>
        <small className="op-health__label">{kpi.label}</small>
        <b className="op-health__value">{kpi.value === null ? "N/A" : kpi.value}</b>
        {kpi.context && <small className="op-health__context">{kpi.context}</small>}
      </Link>
    </li>
  );
}

const OperationalHealth = memo(function OperationalHealth({
  data,
  state,
}: {
  data: OperationalHealthData;
  state: SectionState;
}) {
  const kpis = Object.values(data);

  return (
    <div className="op-health">
      {state === "loading" && (
        <div className="op-health__status">Loading operational health…</div>
      )}
      {state === "error" && (
        <div className="op-health__status op-health__status--error">
          Operational data unavailable.
        </div>
      )}
      <ul className="op-health__strip">
        {kpis.map((kpi) => (
          <Kpi key={kpi.label} kpi={kpi} />
        ))}
      </ul>
    </div>
  );
});

export default OperationalHealth;
