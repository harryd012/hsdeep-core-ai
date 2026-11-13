"use client";

/**
 * Multi-line time-series chart in the page-kit style (same axes, grid, theme
 * tokens and empty state as PageKit's LineChart, which draws one series).
 * Points Prometheus did not return are gaps, never zeros.
 */

export interface ChartLine {
  name: string;
  points: [number, number][]; // [unix seconds, value]
}

const PALETTE = ["var(--ecc-accent)", "#e0803a", "#2f9e77", "#8f5bd6", "#d0476d", "#3a9be0", "#a0892c", "#5b6b7f"];
const SEMANTIC: Record<string, string> = {
  "5xx": "var(--ecc-bad)", error: "var(--ecc-bad)", critical: "var(--ecc-bad)",
  "4xx": "var(--ecc-warn)", warning: "var(--ecc-warn)",
  "2xx": "var(--ecc-good)", success: "var(--ecc-good)",
};

export function lineColor(name: string, i: number): string {
  return SEMANTIC[name] ?? PALETTE[i % PALETTE.length];
}

export default function SeriesChart({
  lines,
  label,
  format,
  height = 170,
  stepSeconds,
  thresholds = [],
}: {
  lines: ChartLine[];
  label: string;
  format: (v: number) => string;
  height?: number;
  /** Break a line where consecutive points are more than two steps apart. */
  stepSeconds: number;
  thresholds?: { value: number; label: string; color: string }[];
}) {
  const drawn = lines.filter((l) => l.points.length > 0);
  if (drawn.length === 0) return <p className="pk-muted pk-empty-note">No measurements in this window.</p>;

  const W = 600, H = height, padL = 56, padR = 8, padT = 8, padB = 22;
  const all = drawn.flatMap((l) => l.points);
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
  const values = all.map((p) => p[1]).concat(thresholds.map((t) => t.value));
  const lo = Math.min(0, ...values);
  let hi = Math.max(...values);
  if (lo === hi) hi = lo + 1;
  hi += (hi - lo) * 0.08;
  const x = (t: number) => padL + (t1 === t0 ? (W - padL - padR) / 2 : ((t - t0) / (t1 - t0)) * (W - padL - padR));
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const fmtTime = (t: number) => new Date(t * 1000).toLocaleString("en-US",
    t1 - t0 > 2 * 86_400 ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" });

  const paths = drawn.map((l) => {
    const segs: [number, number][][] = [];
    for (const p of l.points) {
      const seg = segs[segs.length - 1];
      if (seg && p[0] - seg[seg.length - 1][0] <= stepSeconds * 2) seg.push(p);
      else segs.push([p]);
    }
    return segs;
  });

  return (
    <figure style={{ margin: 0 }} aria-label={label}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label={label}>
        {[hi, (hi + lo) / 2, lo].map((v) => (
          <g key={v}>
            <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} stroke="var(--ecc-border)" strokeDasharray="2 3" />
            <text x={padL - 6} y={y(v) + 4} textAnchor="end" fontSize="10" fill="var(--ecc-text-muted)">{format(v)}</text>
          </g>
        ))}
        {thresholds.map((th) => (
          <g key={th.label}>
            <line x1={padL} x2={W - padR} y1={y(th.value)} y2={y(th.value)} stroke={th.color} strokeDasharray="6 4" />
            <text x={W - padR} y={y(th.value) - 3} textAnchor="end" fontSize="10" fill={th.color}>{th.label}</text>
          </g>
        ))}
        {paths.map((segs, i) => segs.map((seg, j) => (
          <g key={`${i}-${j}`}>
            <path d={`M ${seg.map((p) => `${x(p[0])} ${y(p[1])}`).join(" L ")}`} fill="none"
              stroke={lineColor(drawn[i].name, i)} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
            {seg.length === 1 && <circle cx={x(seg[0][0])} cy={y(seg[0][1])} r="2.5" fill={lineColor(drawn[i].name, i)} />}
          </g>
        )))}
        <text x={padL} y={H - 6} fontSize="10" fill="var(--ecc-text-muted)">{fmtTime(t0)}</text>
        <text x={W - padR} y={H - 6} fontSize="10" textAnchor="end" fill="var(--ecc-text-muted)">{fmtTime(t1)}</text>
      </svg>
      <figcaption style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 12, marginTop: 4 }}>
        {drawn.map((l, i) => (
          <span key={l.name} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span aria-hidden="true" style={{ width: 10, height: 3, borderRadius: 2, background: lineColor(l.name, i) }} />
            <span>{l.name}</span>
            <span className="pk-muted">{format(l.points[l.points.length - 1][1])}</span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
