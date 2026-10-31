"use client";

import { useMemo } from "react";
import { Globe2, Radio, Server, Workflow } from "lucide-react";
import type {
  DeviceRow,
  InfrastructureOverviewData,
  SectionState,
} from "@/lib/dashboard/dashboardModels";

/**
 * AssetsSummary — presentational companion to the Infrastructure Operations
 * zone. Shows the estate totals (already computed by the dashboard data
 * adapter) and the distribution of real Device rows by device_type.
 *
 * No fetching, no fabricated values: when a total is unavailable it renders
 * an honest dash, and the device-type bars only appear when Device rows
 * actually exist.
 */

const MAX_TYPE_BARS = 4;

const AssetsSummary = function AssetsSummary({
  summary,
  devices,
  state,
}: {
  summary: InfrastructureOverviewData["summary"];
  devices: DeviceRow[];
  state: SectionState;
}) {
  const typeBars = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of devices) {
      const key = (d.type || "unknown").trim() || "unknown";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const total = devices.length;
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_TYPE_BARS)
      .map(([label, count]) => ({
        label,
        count,
        pct: total > 0 ? Math.round((count / total) * 100) : 0,
      }));
  }, [devices]);

  const tiles = [
    { label: "Devices", value: summary?.devices, Icon: Server },
    { label: "Sensors", value: summary?.sensors, Icon: Radio },
    { label: "Collectors", value: summary?.monitoringSources, Icon: Workflow },
    { label: "Sites", value: summary?.sites, Icon: Globe2 },
  ];

  return (
    <section className="assets-summary" aria-label="Assets Summary">
      <div className="assets-summary__tiles">
        {tiles.map(({ label, value, Icon }) => (
          <div key={label} className="assets-summary__tile">
            <span className="assets-summary__tile-icon">
              <Icon size={16} />
            </span>
            <b className="assets-summary__tile-value">
              {state === "loading" && value === undefined ? "…" : (value ?? "—").toLocaleString()}
            </b>
            <small className="assets-summary__tile-label">{label}</small>
          </div>
        ))}
      </div>

      <div className="assets-summary__types">
        <h3 className="assets-summary__types-title">Top Device Types</h3>
        {devices.length === 0 ? (
          <p className="assets-summary__empty">
            {state === "loading" ? "Loading devices…" : "No devices registered for this tenant yet."}
          </p>
        ) : (
          <ul className="assets-summary__bars">
            {typeBars.map((bar) => (
              <li key={bar.label} className="assets-summary__bar-row">
                <span className="assets-summary__bar-label" title={bar.label}>{bar.label}</span>
                <span className="assets-summary__bar-track">
                  <span className="assets-summary__bar-fill" style={{ width: `${Math.max(bar.pct, 4)}%` }} />
                </span>
                <span className="assets-summary__bar-value">{bar.pct}%</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default AssetsSummary;
