"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { downsample, fmt } from "@/lib/hostHealth";

/**
 * MetricChart — one sensor's history as an area line with its warning and
 * critical limits drawn across, plus a hover readout. Plain SVG (no chart
 * library), measured to its container so text never stretches. Points must
 * be oldest first.
 */
const pad = { l: 48, r: 12, t: 12, b: 26 };

export default function MetricChart({
  points,
  unit,
  warning,
  critical,
  height = 220,
}: {
  points: { t: number; v: number }[];
  unit: string | null;
  warning: number | null;
  critical: number | null;
  height?: number;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => downsample(points, Math.max(40, Math.floor((width - pad.l - pad.r) / 3))), [points, width]);

  const geo = useMemo(() => {
    if (!data.length) return null;
    const t0 = data[0].t;
    const t1 = data[data.length - 1].t || t0 + 1;
    let lo = Math.min(...data.map((p) => p.v));
    let hi = Math.max(...data.map((p) => p.v));
    for (const lim of [warning, critical]) if (lim !== null && lim <= hi * 1.5 + 1) { lo = Math.min(lo, lim); hi = Math.max(hi, lim); }
    if (lo > 0 && lo < hi * 0.4) lo = 0;
    if (hi === lo) { hi += 1; lo = Math.max(0, lo - 1); }
    const span = hi - lo;
    hi += span * 0.08;
    const iw = width - pad.l - pad.r;
    const ih = height - pad.t - pad.b;
    const x = (t: number) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * iw;
    const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * ih;
    const line = data.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const area = `${line}L${x(data[data.length - 1].t).toFixed(1)},${pad.t + ih}L${x(data[0].t).toFixed(1)},${pad.t + ih}Z`;
    const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3);
    const spanMs = t1 - t0;
    const tfmt = (t: number) =>
      new Date(t).toLocaleString("en-US", spanMs > 2 * 86_400_000 ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" });
    const xticks = Array.from({ length: 5 }, (_, i) => t0 + (spanMs * i) / 4);
    return { x, y, line, area, ticks, xticks, tfmt, ih };
  }, [data, width, height, warning, critical]);

  if (!geo) return <div className="hd-chart__empty" style={{ height }}>No readings in this window.</div>;

  const h = hover !== null ? data[hover] : null;
  const u = unit ? ` ${unit}` : "";

  return (
    <div ref={wrap} className="hd-chart" style={{ height }}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={`Metric history, ${data.length} points`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = e.clientX - r.left;
          let best = 0;
          let dist = Infinity;
          data.forEach((p, i) => {
            const d = Math.abs(geo.x(p.t) - px);
            if (d < dist) { dist = d; best = i; }
          });
          setHover(best);
        }}
      >
        <defs>
          <linearGradient id="hd-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--ecc-accent)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--ecc-accent)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {geo.ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(v)} y2={geo.y(v)} stroke="var(--ecc-border)" strokeDasharray="2 4" />
            <text x={pad.l - 6} y={geo.y(v) + 3} textAnchor="end" fontSize={10} fill="var(--ecc-text-muted)">{fmt(v)}</text>
          </g>
        ))}
        {geo.xticks.map((t, i) => (
          <text key={t} x={geo.x(t)} y={height - 8} textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"} fontSize={10} fill="var(--ecc-text-muted)">
            {geo.tfmt(t)}
          </text>
        ))}
        <path d={geo.area} fill="url(#hd-area)" />
        <path d={geo.line} fill="none" stroke="var(--ecc-accent)" strokeWidth={1.6} strokeLinejoin="round" />
        {warning !== null && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(warning)} y2={geo.y(warning)} stroke="var(--ecc-warn)" strokeDasharray="5 4" />
            <text x={width - pad.r - 2} y={geo.y(warning) - 4} textAnchor="end" fontSize={10} fill="var(--ecc-warn)">warning {fmt(warning)}</text>
          </g>
        )}
        {critical !== null && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(critical)} y2={geo.y(critical)} stroke="var(--ecc-bad)" strokeDasharray="5 4" />
            <text x={width - pad.r - 2} y={geo.y(critical) - 4} textAnchor="end" fontSize={10} fill="var(--ecc-bad)">critical {fmt(critical)}</text>
          </g>
        )}
        {h && (
          <g>
            <line x1={geo.x(h.t)} x2={geo.x(h.t)} y1={pad.t} y2={pad.t + geo.ih} stroke="var(--ecc-text-muted)" strokeWidth={1} />
            <circle cx={geo.x(h.t)} cy={geo.y(h.v)} r={4} fill="var(--ecc-surface)" stroke="var(--ecc-accent)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {h && (
        <div className="hd-chart__tip" style={{ left: Math.min(width - 150, Math.max(0, geo.x(h.t) + 10)), top: 8 }}>
          <strong>{fmt(h.v)}{u}</strong>
          <span>{new Date(h.t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      )}
    </div>
  );
}
