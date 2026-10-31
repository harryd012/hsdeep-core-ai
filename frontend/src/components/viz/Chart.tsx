"use client";

import { useLayoutEffect, useRef, useState, memo } from "react";

/**
 * Chart — a zero-dependency SVG visualization primitive.
 *
 * Renders line, area, bar and donut charts from the simple
 * `{ label: string; value: number }[]` series model the dashboard already
 * uses (see dashboardModels.TrendChart), so it slots into existing data flows
 * without a competing contract.
 *
 * Design rules (from the platform's truth conventions):
 *  - Honest emptiness: an all-zero or empty series renders an explicit "No
 *    data" overlay, never a flat line at 0 that masquerades as a reading.
 *  - null/undefined values render as gaps in line/area series, not as 0.
 *  - Responsive width via ResizeObserver; the chart never overflows its tile.
 *  - Themeable through CSS custom properties (var(--cyan) etc.) consumed via
 *    getComputedStyle so SVG fills/ strokes match the active theme.
 *  - Accessible: each chart is role="img" with an aria-label summarizing it.
 */

export type ChartType = "line" | "area" | "bar" | "donut";

export interface ChartPoint {
  label: string;
  /** number, or null for "no reading" (renders as a gap in line/area). */
  value: number | null;
}

export interface ChartSeries {
  /** Optional series name for the legend/tooltip. */
  name?: string;
  color?: string; // CSS var or hex; defaults to --cyan
  data: ChartPoint[];
}

interface ChartProps {
  type: ChartType;
  series: ChartSeries[];
  height?: number;
  /** Y-axis label, e.g. "alerts". */
  yLabel?: string;
  /** Show the horizontal value gridlines. Default true. */
  grid?: boolean;
  /** For donut: center label/value. */
  centerLabel?: { title: string; value: string };
  /** Tooltip unit suffix appended to values, e.g. "ms". */
  unit?: string;
}

const PAD = { top: 12, right: 12, bottom: 24, left: 36 };

function useContainerWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        setWidth(Math.max(0, Math.floor(e.contentRect.width)));
      }
    });
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** Resolve a CSS var / hex to a concrete color usable in SVG attributes. */
function resolveColor(c: string): string {
  if (typeof document === "undefined" || !c.startsWith("var(")) return c;
  const name = c.slice(4, -1).trim();
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || c;
}

/** Resolve CSS-var colors to concrete values for SVG attributes, computed
 *  during render (client-only; document is guarded in resolveColor). No
 *  effect or state: avoids the set-state-in-effect rule entirely. */
function resolveSeriesColors(series: ChartSeries[]): ChartSeries[] {
  return series.map((s) => ({
    ...s,
    color: s.color ? resolveColor(s.color) : s.color,
  }));
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) {
    if (min === 0) return [0, 1];
    return [0, max];
  }
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= end + 1e-9; v += step) ticks.push(Math.round(v * 1000) / 1000);
  return ticks;
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(1);
}

export default memo(function Chart({
  type,
  series,
  height = 200,
  yLabel,
  grid = true,
  centerLabel,
  unit = "",
}: ChartProps) {
  const { ref, width } = useContainerWidth<HTMLDivElement>();
  const resolved = resolveSeriesColors(series);
  const [hover, setHover] = useState<number | null>(null);

  // Single-series convenience (the dashboard's TrendChart is single-series).
  const s = resolved[0];
  const data = s?.data ?? [];

  // Honest emptiness: no data, or every value null/zero (for non-donut), is
  // "no data", not a flat reading.
  const hasRealData =
    data.length > 0 &&
    data.some((d) => d.value !== null && d.value !== 0);

  const W = width || 320;
  const H = height;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  if (type === "donut") {
    return (
      <DonutChart
        containerRef={ref}
        series={resolved}
        height={H}
        centerLabel={centerLabel}
        unit={unit}
      />
    );
  }

  // --- cartesian (line / area / bar) ---
  const values = data.map((d) => d.value ?? 0);
  const allVals = values.filter((_, i) => data[i].value !== null);
  let min = 0;
  let max = allVals.length ? Math.max(...allVals) : 1;
  if (type === "line" || type === "area") {
    min = Math.min(0, ...(allVals.length ? allVals : [0]));
  }
  if (max === min) max = min + 1;
  const ticks = niceTicks(min, max, 4);
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const yRange = yMax - yMin || 1;

  const x = (i: number) => PAD.left + (data.length <= 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
  const y = (v: number) => PAD.top + plotH - ((v - yMin) / yRange) * plotH;

  const color = s?.color || "var(--cyan)";

  // Build the line/area path, breaking at null values (gaps).
  let path = "";
  let inSeg = false;
  data.forEach((d, i) => {
    if (d.value === null) {
      inSeg = false;
      return;
    }
    const px = x(i);
    const py = y(d.value);
    if (!inSeg) {
      path += `M ${px} ${py}`;
      inSeg = true;
    } else {
      path += ` L ${px} ${py}`;
    }
  });
  const areaPath =
    type === "area" && path
      ? `${path} L ${x(data.length - 1)} ${PAD.top + plotH} L ${x(0)} ${PAD.top + plotH} Z`
      : "";

  const tickLabels = ticks.map((t) => (
    <g key={t}>
      {grid && t !== 0 && (
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={y(t)}
          y2={y(t)}
          stroke="var(--line, #1f2d38)"
          strokeWidth={1}
          strokeDasharray="2 3"
          opacity={0.5}
        />
      )}
      <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" fontSize={9} fill="var(--muted, #6b7785)">
        {fmt(t)}
      </text>
    </g>
  ));

  return (
    <div ref={ref} style={{ width: "100%", position: "relative" }} role="img" aria-label={`${type} chart of ${yLabel ?? "values"}`}>
      <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
        {/* zero baseline */}
        <line x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} stroke="var(--line, #1f2d38)" strokeWidth={1} opacity={0.7} />

        {tickLabels}

        {/* x labels (sparse to avoid crowding) */}
        {data.map((d, i) => {
          const step = Math.max(1, Math.ceil(data.length / 6));
          if (i % step !== 0 && i !== data.length - 1) return null;
          return (
            <text key={i} x={x(i)} y={H - 6} textAnchor="middle" fontSize={9} fill="var(--muted, #6b7785)">
              {d.label}
            </text>
          );
        })}

        {/* area fill */}
        {type === "area" && areaPath && <path d={areaPath} fill={color} opacity={0.16} />}

        {/* bars */}
        {type === "bar" &&
          data.map((d, i) => {
            if (d.value === null) return null;
            const bw = Math.min(28, (plotW / data.length) * 0.7);
            const bx = x(i) - bw / 2;
            const by = y(d.value);
            const bh = y(0) - by;
            return (
              <rect
                key={i}
                x={bx}
                y={by}
                width={bw}
                height={Math.max(0, bh)}
                rx={2}
                fill={color}
                opacity={hover === null || hover === i ? 0.9 : 0.5}
              >
                <title>{`${d.label}: ${fmt(d.value ?? 0)}${unit}`}</title>
              </rect>
            );
          })}

        {/* line/area stroke */}
        {(type === "line" || type === "area") && path && (
          <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        )}

        {/* points + hover hit areas */}
        {(type === "line" || type === "area") &&
          data.map((d, i) =>
            d.value === null ? null : (
              <g key={i}>
                <circle cx={x(i)} cy={y(d.value)} r={hover === i ? 3.5 : 2} fill={color} stroke="var(--bg, #0b0f14)" strokeWidth={1}>
                  <title>{`${d.label}: ${fmt(d.value ?? 0)}${unit}`}</title>
                </circle>
                <rect
                  x={x(i) - (plotW / data.length) / 2}
                  y={PAD.top}
                  width={plotW / data.length}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                />
              </g>
            ),
          )}
        {type === "bar" &&
          data.map((d, i) => (
            <rect
              key={`hit-${i}`}
              x={x(i) - (plotW / data.length) / 2}
              y={PAD.top}
              width={plotW / data.length}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
      </svg>

      {!hasRealData && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--muted)",
            fontSize: 12,
            pointerEvents: "none",
          }}
        >
          No data in this window
        </div>
      )}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Donut
// ---------------------------------------------------------------------------

function DonutChart({
  containerRef,
  series,
  height,
  centerLabel,
  unit,
}: {
  containerRef: React.RefObject<HTMLDivElement | null>;
  series: ChartSeries[];
  height: number;
  centerLabel?: { title: string; value: string };
  unit?: string;
}) {
  // Flatten all series' real points into slices, each colored by its series
  // color. A single-series input paints every slice the same color; a
  // multi-series input (e.g. one series per severity bucket) paints each slice
  // distinctly — both are honest over the same primitive.
  const slices = series.flatMap((s) =>
    (s.data ?? [])
      .filter((d) => d.value !== null && d.value > 0)
      .map((d) => ({ label: d.label, value: d.value ?? 0, color: s.color || "var(--cyan)" })),
  );
  const total = slices.reduce((sum, d) => sum + d.value, 0);
  const hasRealData = total > 0 && slices.length > 0;

  const cx = height / 2;
  const cy = height / 2;
  const r = height / 2 - 4;
  const ir = r * 0.62;

  let acc = 0;
  const arcs = slices.map((d) => {
    const frac = (d.value ?? 0) / total;
    const start = acc;
    acc += frac;
    const a0 = start * 2 * Math.PI - Math.PI / 2;
    const a1 = acc * 2 * Math.PI - Math.PI / 2;
    const large = acc - start > 0.5 ? 1 : 0;
    const p = (a: number, r2: number) => [cx + r2 * Math.cos(a), cy + r2 * Math.sin(a)];
    const [x0, y0] = p(a0, r);
    const [x1, y1] = p(a1, r);
    const [x2, y2] = p(a1, ir);
    const [x3, y3] = p(a0, ir);
    const d2 = `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${ir} ${ir} 0 ${large} 0 ${x3} ${y3} Z`;
    const color = d.color;
    return { d: d2, color, label: d.label, value: d.value ?? 0, pct: frac };
  });

  return (
    <div ref={containerRef} style={{ width: "100%", display: "flex", gap: 12, alignItems: "center" }}>
      <svg width={height} height={height} style={{ flexShrink: 0 }} role="img" aria-label="donut chart">
        {hasRealData ? (
          arcs.map((a, i) => (
            <path key={i} d={a.d} fill={a.color} stroke="var(--bg, #0b0f14)" strokeWidth={1.5}>
              <title>{`${a.label}: ${fmt(a.value)}${unit} (${Math.round(a.pct * 100)}%)`}</title>
            </path>
          ))
        ) : (
          <circle cx={cx} cy={cy} r={(r + ir) / 2} fill="none" stroke="var(--line, #1f2d38)" strokeWidth={r - ir} opacity={0.4} />
        )}
        {centerLabel && (
          <text x={cx} y={cy - 4} textAnchor="middle" fontSize={18} fontWeight={700} fill="var(--fg, #e6edf3)">
            {centerLabel.value}
          </text>
        )}
        {centerLabel && (
          <text x={cx} y={cy + 12} textAnchor="middle" fontSize={9} fill="var(--muted, #6b7785)">
            {centerLabel.title}
          </text>
        )}
      </svg>
      {/* legend */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 11, minWidth: 0 }}>
        {hasRealData ? (
          arcs.map((a, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--fg)" }}>
              <i style={{ width: 9, height: 9, borderRadius: 2, background: a.color, flexShrink: 0 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.label}</span>
              <span style={{ color: "var(--muted)", marginLeft: "auto" }}>
                {fmt(a.value)} · {Math.round(a.pct * 100)}%
              </span>
            </div>
          ))
        ) : (
          <span style={{ color: "var(--muted)" }}>No data</span>
        )}
      </div>
    </div>
  );
}
