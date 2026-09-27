"use client";

import { memo } from "react";
import { Cloud, Database, Globe2, Network, Radio, Server } from "lucide-react";
import Panel from "./Panel";

/**
 * InfrastructureMap — PRESENTATIONAL (Zone 3).
 *
 * Semantic purpose: "Where are infrastructure problems occurring?" / "How is
 * the estate distributed?" The summary comes from the dashboard data adapter
 * (GET /api/monitoring/dashboard-summary + network status) — no fetching here.
 * Markup/classes unchanged from the original.
 */
type MapCell = {
  label: string;
  value: number | string;
  Icon: typeof Globe2;
};

const InfrastructureMap = memo(function InfrastructureMap({
  summary,
}: {
  summary: {
    sites?: number | null;
    devices?: number | null;
    monitoringSources?: number | null;
    cloudResources?: number | null;
    sensors?: number | null;
    regions?: number | null;
  } | null;
}) {
  // Every cell shows only what the backend actually reported.
  //
  // The previous form (`summary?.sites ?? 0`) turned "this field is missing
  // from the API response" into a confident 0 - which is how CLOUD read 0 while
  // the estate simply had no cloud data, and it is indistinguishable from a
  // genuinely measured zero. An absent or non-finite value is therefore
  // labelled N/A; the loading state (no summary at all) stays "…".
  const cellValue = (value: number | null | undefined): number | string => {
    if (value === null || value === undefined) return "N/A";
    if (typeof value !== "number" || !Number.isFinite(value)) return "N/A";
    return value;
  };

  const cells: MapCell[] = [
    { label: "SITES", value: cellValue(summary?.sites), Icon: Globe2 },
    { label: "DEVICES", value: cellValue(summary?.devices), Icon: Server },
    { label: "SOURCES", value: cellValue(summary?.monitoringSources), Icon: Database },
    { label: "CLOUD", value: cellValue(summary?.cloudResources), Icon: Cloud },
    { label: "SENSORS", value: cellValue(summary?.sensors), Icon: Radio },
    { label: "REGIONS", value: cellValue(summary?.regions), Icon: Network },
  ];

  return (
    <Panel
      title="INFRASTRUCTURE MAP"
      // The tile grid only reports what the API sent, so a green status while
      // there is no summary at all would assert health from nothing.
      status={summary ? "healthy" : "info"}
      className="infra-map"
    >
      <div className="infra-map__grid">
        {cells.map((cell) => (
          <div key={cell.label} className="infra-map__cell">
            <div className="infra-map__icon">
              <cell.Icon size={18} />
            </div>
            <b className="infra-map__value">{summary ? cell.value : "…"}</b>
            <small className="infra-map__label">{cell.label}</small>
          </div>
        ))}
      </div>
    </Panel>
  );
});

export default InfrastructureMap;
