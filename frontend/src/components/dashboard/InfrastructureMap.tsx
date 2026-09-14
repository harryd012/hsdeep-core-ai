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
    sites: number;
    devices: number;
    monitoringSources: number;
    cloudResources: number;
    sensors: number;
    regions: number;
  } | null;
}) {
  const cells: MapCell[] = [
    { label: "SITES", value: summary?.sites ?? 0, Icon: Globe2 },
    { label: "DEVICES", value: summary?.devices ?? 0, Icon: Server },
    { label: "SOURCES", value: summary?.monitoringSources ?? 0, Icon: Database },
    { label: "CLOUD", value: summary?.cloudResources ?? 0, Icon: Cloud },
    { label: "SENSORS", value: summary?.sensors ?? 0, Icon: Radio },
    { label: "REGIONS", value: summary?.regions ?? 0, Icon: Network },
  ];

  return (
    <Panel title="INFRASTRUCTURE MAP" status="healthy" className="infra-map">
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
