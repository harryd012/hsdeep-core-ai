"use client";

/**
 * ThreatTrendChart — SVG area chart (critical/high/medium/low series).
 * Real component, no chart library in the project; data comes from the
 * socAdapter (currently typed fallback until the threat-trend API exists).
 */
import type { SocTrendPoint } from "@/lib/soc/socModels";
import { sevColor } from "@/lib/soc/socAdapter";

const W = 560, H = 200, PAD_L = 28, PAD_B = 22, PAD_T = 10;

function seriesPath(pts: SocTrendPoint[], key: "critical" | "high" | "medium" | "low"): string {
  const max = Math.max(4, ...pts.map((p) => Math.max(p.critical, p.high, p.medium, p.low)));
  const step = (W - PAD_L - 8) / Math.max(1, pts.length - 1);
  return pts.map((p, i) => {
    const x = PAD_L + i * step;
    const y = H - PAD_B - (p[key] / max) * (H - PAD_B - PAD_T);
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

export default function ThreatTrendChart({ points, source }: { points: SocTrendPoint[]; source: "live" | "unavailable" }) {
  if (points.length === 0) {
    return (
      <div className="soc-panel soc-trend">
        <div className="soc-panel__head">
          <h2>Threat Trend</h2>
          <span className="soc-panel__select">Last 24 hours ▾</span>
        </div>
        <div className="soc-empty">No threat trend data available.</div>
        {source === "unavailable" && <small className="soc-demo-note">demo data — threat-trend API pending</small>}
      </div>
    );
  }
  const max = Math.max(4, ...points.map((p) => Math.max(p.critical, p.high, p.medium, p.low)));
  const ticks = [0, 0.5, 1].map((f) => Math.round(max * f));
  const series: Array<{ key: "critical" | "high" | "medium" | "low"; label: string }> = [
    { key: "critical", label: "Critical" },
    { key: "high", label: "High" },
    { key: "medium", label: "Medium" },
    { key: "low", label: "Low" },
  ];

  return (
    <div className="soc-panel soc-trend">
      <div className="soc-panel__head">
        <h2>Threat Trend</h2>
        <span className="soc-panel__select">Last 24 hours ▾</span>
      </div>
      <div className="soc-trend__legend">
        {series.map((s) => (
          <span key={s.key}><i style={{ background: sevColor(s.key) }} /> {s.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="soc-trend__svg" role="img" aria-label="Threat trend over the last 24 hours">
        {ticks.map((t, i) => {
          const y = H - PAD_B - (t / max) * (H - PAD_B - PAD_T);
          return (
            <g key={i}>
              <line x1={PAD_L} x2={W - 8} y1={y} y2={y} className="soc-grid-line" />
              <text x={4} y={y + 3} className="soc-axis-text">{t}</text>
            </g>
          );
        })}
        {series.map((s) => (
          <path key={s.key} d={seriesPath(points, s.key)} fill="none" stroke={sevColor(s.key)} strokeWidth={2} />
        ))}
        {points.map((p, i) => {
          const step = (W - PAD_L - 8) / Math.max(1, points.length - 1);
          return (
            <text key={p.label} x={PAD_L + i * step} y={H - 6} className="soc-axis-text" textAnchor="middle">
              {p.label}
            </text>
          );
        })}
      </svg>
      <title>{source === "unavailable" ? "Demo data — threat-trend API not yet available" : "Live threat trend"}</title>
      {source === "unavailable" && <small className="soc-demo-note">demo data — threat-trend API pending</small>}
    </div>
  );
}
