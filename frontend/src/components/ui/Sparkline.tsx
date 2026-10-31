/**
 * Sparkline - a tiny dependency-free SVG sparkline for MetricTile and other
 * stat surfaces. Hand-rolled to match the existing SVG chart convention in
 * the codebase (e.g. ThreatTrendChart); the project intentionally ships no
 * charting library.
 *
 * Presentational and honest: it draws exactly the points it is given. It
 * never invents a point to fill a gap - a missing value simply breaks the
 * line, and an empty series renders an honest flat baseline with an
 * accessible "no data" label rather than a fabricated trend.
 */

import { memo } from "react";

const W = 96;
const H = 28;
const PAD = 2;

export interface SparklineProps {
  /** Numeric series in chronological order. null/undefined entries are gaps. */
  points: ReadonlyArray<number | null | undefined>;
  /** CSS colour (a design token, e.g. var(--cyan)). */
  color?: string;
  /** Accessible description; defaults to a neutral label. */
  label?: string;
  width?: number;
  height?: number;
}

function finite(points: ReadonlyArray<number | null | undefined>): number[] {
  return points.filter((p): p is number => typeof p === "number" && Number.isFinite(p));
}

export default memo(function Sparkline({
  points,
  color = "var(--cyan)",
  label = "Trend",
  width = W,
  height = H,
}: SparklineProps) {
  const values = finite(points);

  // Honest empty state: fewer than two real points cannot form a trend.
  if (values.length < 2) {
    return (
      <svg
        className="obs-spark"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${label}: no data`}
      >
        <line
          x1={PAD}
          y1={height - PAD}
          x2={width - PAD}
          y2={height - PAD}
          className="obs-spark__line"
          stroke="var(--muted)"
          strokeDasharray="3 3"
        />
      </svg>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (width - PAD * 2) / (points.length - 1);

  const xy = (i: number, v: number): [number, number] => {
    const x = PAD + i * stepX;
    const y = height - PAD - ((v - min) / span) * (height - PAD * 2);
    return [x, y];
  };

  // Build segments, breaking the line across gaps (null values) so a hole is
  // shown as a hole rather than an invented interpolation.
  const segments: string[] = [];
  let current: string[] = [];
  points.forEach((p, i) => {
    if (typeof p === "number" && Number.isFinite(p)) {
      const [x, y] = xy(i, p);
      current.push(`${current.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
    } else if (current.length) {
      segments.push(current.join(" "));
      current = [];
    }
  });
  if (current.length) segments.push(current.join(" "));

  // Area fill follows the last continuous segment only (best effort), so the
  // fill never fabricates data across a gap.
  const lastSeg = segments[segments.length - 1] ?? "";
  const areaPath = lastSeg
    ? `${lastSeg} L${(PAD + (points.length - 1) * stepX).toFixed(1)},${height - PAD} L${PAD},${height - PAD} Z`
    : "";

  return (
    <svg
      className="obs-spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      {areaPath && <path className="obs-spark__area" d={areaPath} fill={color} />}
      {segments.map((d, idx) => (
        <path key={idx} className="obs-spark__line" d={d} stroke={color} />
      ))}
    </svg>
  );
});