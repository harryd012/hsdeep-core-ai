/**
 * Traffic formatting, direction and animation gating (Phase 9.5).
 *
 * Pure functions over telemetry the backend actually measured. Nothing here
 * invents a number: a missing reading returns `null` so the caller renders an
 * explicit "unavailable" instead of a fabricated 0.
 *
 * Direction is derived from per-endpoint transmit/receive counters, never from
 * an assumption about which way a link "usually" flows.
 */

export type TrafficDirection = "none" | "forward" | "reverse" | "bidirectional";

export type UtilizationBand = "normal" | "moderate" | "high" | "critical";

/** Descending scales, so the first match is the largest unit that fits. */
const SCALES: readonly { divisor: number; suffix: string }[] = [
  { divisor: 1e12, suffix: "Tbps" },
  { divisor: 1e9, suffix: "Gbps" },
  { divisor: 1e6, suffix: "Mbps" },
  { divisor: 1e3, suffix: "Kbps" },
  { divisor: 1, suffix: "bps" },
];

function formatValue(value: number): string {
  const decimals = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return value
    .toFixed(decimals)
    .replace(/(\.\d*?)0+$/, "$1")
    .replace(/\.$/, "");
}

/**
 * Human-readable throughput with an automatically chosen unit.
 *
 * Never hardcodes Mbps: 950 bps stays "950 bps" and 1.2 Tbps is not rendered as
 * "1200000 Mbps". Returns null for a missing/NaN reading — the caller must then
 * say "unavailable" rather than print 0.
 */
export function formatBps(bps: number | null | undefined): string | null {
  if (bps === null || bps === undefined || !Number.isFinite(bps)) return null;
  const value = Math.max(0, Math.abs(bps));
  for (const scale of SCALES) {
    if (value >= scale.divisor) {
      return `${formatValue(value / scale.divisor)} ${scale.suffix}`;
    }
  }
  return `${formatValue(value)} bps`;
}

/** Percentage text, or null when there is no percentage to show. */
export function formatPercent(pct: number | null | undefined): string | null {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  const clamped = Math.min(100, Math.max(0, pct));
  return `${formatValue(clamped)}%`;
}

/**
 * Utilisation band. Thresholds are presentation bands (0-50 / 50-80 / 80-90 /
 * 90-100), applied to whatever percentage the backend supplies; the backend
 * decides whether that percentage is a provider measurement or a derived one and
 * labels it via `utilization_source`.
 */
export function utilizationBand(pct: number | null | undefined): UtilizationBand | null {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  const clamped = Math.min(100, Math.max(0, pct));
  if (clamped >= 90) return "critical";
  if (clamped >= 80) return "high";
  if (clamped >= 50) return "moderate";
  return "normal";
}

export interface DirectionInput {
  localRxBps?: number | null;
  localTxBps?: number | null;
  remoteRxBps?: number | null;
  remoteTxBps?: number | null;
}

/**
 * Which way traffic is actually flowing across an edge.
 *
 * `forward` means source → target, evidenced by the LOCAL end transmitting or the
 * REMOTE end receiving. `none` means no direction is evidenced (no traffic, or no
 * telemetry at all) and therefore nothing may be animated.
 */
export function trafficDirection(input: DirectionInput): TrafficDirection {
  const forward =
    (input.localTxBps ?? 0) > 0 || (input.remoteRxBps ?? 0) > 0;
  const reverse =
    (input.remoteTxBps ?? 0) > 0 || (input.localRxBps ?? 0) > 0;
  if (forward && reverse) return "bidirectional";
  if (forward) return "forward";
  if (reverse) return "reverse";
  return "none";
}

export interface AnimationGate {
  /** Total measured throughput for the link; null/undefined = not measured. */
  bps?: number | null;
  /** Result of `isFreshnessCurrent(...)` for the link's telemetry. */
  freshnessCurrent?: boolean;
  /** `prefers-reduced-motion` — always wins. */
  reducedMotion?: boolean;
  /** User-facing toggle. */
  animateEnabled?: boolean;
  /** Remaining animation budget. Large graphs cap concurrent animations. */
  budget?: number;
}

/**
 * Whether a link may animate.
 *
 * Animation is evidence: it requires MEASURED throughput above zero AND current
 * telemetry. A missing reading, an unmeasured (0-bps-by-default) link, stale
 * data, reduced motion, a disabled toggle, or an exhausted budget all mean
 * "static link". Nothing here ever animates by default or at random.
 */
export function shouldAnimate(gate: AnimationGate): boolean {
  if (gate.animateEnabled !== true) return false;
  if (gate.reducedMotion === true) return false;
  if ((gate.budget ?? 0) <= 0) return false;
  if (gate.freshnessCurrent !== true) return false;
  const bps = gate.bps;
  if (bps === null || bps === undefined || !Number.isFinite(bps)) return false;
  return bps > 0;
}

/**
 * Concurrent-animation budget for a graph of this size.
 *
 * Bounded so a large enterprise site cannot degrade the canvas: only a fraction
 * of the links animate, and never more than a hard ceiling. Deterministic in
 * `linkCount`, so the same graph always animates the same number of links.
 */
export function animationBudget(linkCount: number, cap = 40): number {
  if (!Number.isFinite(linkCount) || linkCount <= 0) return 0;
  return Math.max(0, Math.min(cap, Math.ceil(linkCount / 4)));
}