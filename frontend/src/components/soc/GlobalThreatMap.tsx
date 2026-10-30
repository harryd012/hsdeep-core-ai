"use client";

/**
 * GlobalThreatMap — SVG equirectangular world with severity points and
 * curved attack arcs. Real SVG component (no map library installed);
 * coordinates come from the typed fallback adapter until a geo API exists.
 */
import type { SocThreatArc, SocThreatLocation } from "@/lib/soc/socModels";
import { sevColor } from "@/lib/soc/socAdapter";

const W = 640, H = 300;

export default function GlobalThreatMap({ points, arcs, source }: {
  points: SocThreatLocation[]; arcs: SocThreatArc[]; source: "live" | "unavailable";
}) {
  return (
    <div className="soc-panel soc-map">
      <div className="soc-panel__head">
        <h2>Global Threat Map</h2>
        <div className="soc-map__legend">
          {["critical", "high", "medium", "low"].map((s) => (
            <span key={s}><i style={{ background: sevColor(s) }} /> {s[0].toUpperCase() + s.slice(1)}</span>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="soc-map__svg" role="img" aria-label="Global threat map">
        {points.length === 0 && (
          <text x={W / 2} y={H / 2} textAnchor="middle" className="soc-axis-text">
            No live threat locations
          </text>
        )}
        {/* graticule */}
        {Array.from({ length: 7 }, (_, i) => (
          <line key={`h${i}`} x1={0} x2={W} y1={(i * H) / 6} y2={(i * H) / 6} className="soc-grid-line" />
        ))}
        {Array.from({ length: 9 }, (_, i) => (
          <line key={`v${i}`} y1={0} y2={H} x1={(i * W) / 8} x2={(i * W) / 8} className="soc-grid-line" />
        ))}
        {/* abstract continent blobs (stylized landmass) */}
        <g className="soc-map__land">
          <ellipse cx="150" cy="110" rx="80" ry="55" />
          <ellipse cx="185" cy="215" rx="45" ry="55" />
          <ellipse cx="310" cy="100" rx="60" ry="42" />
          <ellipse cx="330" cy="230" rx="85" ry="42" />
          <ellipse cx="455" cy="110" rx="65" ry="60" />
          <ellipse cx="560" cy="235" rx="45" ry="30" />
          <ellipse cx="520" cy="230" rx="30" ry="35" />
        </g>
        {/* curved attack arcs */}
        {arcs.map((a, i) => {
          const x1 = (a.from.x / 100) * W, y1 = (a.from.y / 100) * H;
          const x2 = (a.to.x / 100) * W, y2 = (a.to.y / 100) * H;
          const mx = (x1 + x2) / 2, my = Math.min(y1, y2) - 40;
          return (
            <path
              key={i}
              d={`M${x1},${y1} Q${mx},${my} ${x2},${y2}`}
              fill="none"
              stroke={sevColor(a.severity)}
              strokeOpacity={0.5}
              strokeDasharray="4 4"
            />
          );
        })}
        {/* threat points */}
        {points.map((p) => (
          <g key={p.city} transform={`translate(${(p.x / 100) * W},${(p.y / 100) * H})`}>
            <circle r="9" fill={sevColor(p.severity)} opacity="0.18" className="soc-map__pulse" />
            <circle r="4" fill={sevColor(p.severity)} />
            <text x="8" y="4" className="soc-map__label">{p.city}</text>
          </g>
        ))}
      </svg>
      {source === "unavailable" && <small className="soc-demo-note">demo data — geographic threat feed pending</small>}
    </div>
  );
}
