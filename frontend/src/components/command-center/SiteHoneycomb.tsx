"use client";

import type { SiteHealthItem, SiteWorstHealth } from "@/lib/api";

/**
 * Sites honeycomb: one hexagon per site, filled by the worst device-health
 * state at that site and labelled with the site code + active incident count.
 * Colour is never the only signal — every hex carries its state in its
 * accessible name and tooltip. Selecting a hex sets the dashboard site filter.
 */

export const HEALTH_COLOR: Record<SiteWorstHealth, string> = {
  down: "var(--ecc-down)",
  critical: "var(--ecc-bad)",
  warning: "var(--ecc-warn)",
  healthy: "var(--ecc-good)",
  unknown: "var(--ecc-unknown)",
};

export const HEALTH_LABEL: Record<SiteWorstHealth, string> = {
  down: "Down",
  critical: "Critical",
  warning: "Warning",
  healthy: "Healthy",
  unknown: "No data",
};

const MAX_HEXES = 11;
const COLS = 4;
const R = 30; // circumradius
const W = Math.sqrt(3) * R;
const H = 2 * R;

function hexPoints(cx: number, cy: number): string {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 90);
    return `${cx + R * Math.cos(a)},${cy + R * Math.sin(a)}`;
  }).join(" ");
}

export default function SiteHoneycomb({
  sites,
  selectedSiteId,
  onSelect,
}: {
  sites: SiteHealthItem[];
  selectedSiteId: string | null;
  onSelect: (siteId: string | null) => void;
}) {
  const shown = sites.slice(0, sites.length > MAX_HEXES + 1 ? MAX_HEXES : MAX_HEXES + 1);
  const overflow = sites.length - shown.length;
  const cells: ({ kind: "site"; site: SiteHealthItem } | { kind: "more"; count: number })[] = [
    ...shown.map((site) => ({ kind: "site" as const, site })),
    ...(overflow > 0 ? [{ kind: "more" as const, count: overflow }] : []),
  ];
  const rows = Math.ceil(cells.length / COLS);
  const cols = Math.min(COLS, cells.length);
  const vbW = cols * W + (rows > 1 ? W / 2 : 0) + 4;
  const vbH = H + (rows - 1) * H * 0.75 + 4;
  const present = Array.from(new Set(sites.map((s) => s.worst_health)));

  return (
    <div>
      <svg
        viewBox={`0 0 ${vbW} ${vbH}`}
        width="100%"
        style={{ maxHeight: 190, display: "block" }}
        role="group"
        aria-label={`Site health for ${sites.length} sites`}
      >
        {cells.map((cell, i) => {
          const row = Math.floor(i / COLS);
          const col = i % COLS;
          const cx = 2 + W / 2 + col * W + (row % 2 ? W / 2 : 0);
          const cy = 2 + R + row * H * 0.75;
          if (cell.kind === "more") {
            return (
              <a key="more" href="/infrastructure" className="ecc-hex" aria-label={`${cell.count} more sites`}>
                <polygon points={hexPoints(cx, cy)} fill="var(--ecc-surface-raised)" />
                <text x={cx} y={cy + 5} textAnchor="middle" fontSize={14} fill="var(--ecc-text)">
                  +{cell.count}
                </text>
              </a>
            );
          }
          const s = cell.site;
          const selected = s.site_id === selectedSiteId;
          const name = `${s.code} ${s.name}: ${HEALTH_LABEL[s.worst_health]}, ${s.open_incidents} active incidents, ${s.devices.total} devices`;
          const dark = s.worst_health === "warning";
          return (
            <g
              key={s.site_id}
              className={`ecc-hex${selected ? " ecc-hex--selected" : ""}`}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={name}
              onClick={() => onSelect(selected ? null : s.site_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(selected ? null : s.site_id);
                }
              }}
            >
              <title>{name}</title>
              <polygon points={hexPoints(cx, cy)} fill={HEALTH_COLOR[s.worst_health]} />
              <text x={cx} y={cy - 2} textAnchor="middle" fontSize={11} fill={dark ? "#1f1300" : "#fff"}>
                {s.code.slice(0, 5)}
              </text>
              <text x={cx} y={cy + 14} textAnchor="middle" fontSize={13} fill={dark ? "#1f1300" : "#fff"}>
                {s.open_incidents}
              </text>
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8, fontSize: 11, color: "var(--ecc-text-muted)" }}>
        {(["healthy", "warning", "critical", "down", "unknown"] as SiteWorstHealth[])
          .filter((h) => present.includes(h))
          .map((h) => (
            <span key={h}>
              <i style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: HEALTH_COLOR[h], marginRight: 4 }} />
              {HEALTH_LABEL[h]}
            </span>
          ))}
        <span style={{ marginLeft: "auto" }}>number = active incidents</span>
      </div>
    </div>
  );
}
