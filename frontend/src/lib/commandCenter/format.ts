/**
 * Executive Command Center formatting rules (ECC-1).
 *
 * Pure and dependency-free so the truth rules are unit-tested directly
 * (frontend/tests/command-center-format.test.mjs):
 *   - null / undefined is "not measured" and renders "—", never 0;
 *   - a real 0 renders "0";
 *   - a delta exists only when BOTH windows were measured; with a zero
 *     baseline it is an absolute change ("+3"), never an infinite percentage;
 *   - delta colour encodes good / bad, not direction (incidents up is bad,
 *     MTTR down is good).
 */

export const NOT_MEASURED = "—";

export const RANGES = ["7d", "14d", "30d", "90d"] as const;
export type CommandCenterRange = (typeof RANGES)[number];
export const DEFAULT_RANGE: CommandCenterRange = "7d";

const SEVERITIES = ["critical", "warning", "info"] as const;
type Severity = (typeof SEVERITIES)[number];

export function parseRange(value: string | null | undefined): CommandCenterRange {
  return (RANGES as readonly string[]).includes(value ?? "") ? (value as CommandCenterRange) : DEFAULT_RANGE;
}

export function rangeDays(range: CommandCenterRange): number {
  return Number.parseInt(range, 10);
}

export function parseSeverity(value: string | null | undefined): Severity | null {
  return (SEVERITIES as readonly string[]).includes(value ?? "") ? (value as Severity) : null;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_MEASURED;
  return value.toLocaleString("en-US");
}

/** KPI-tile count: exact below 100,000, compact above ("2.67M") so it fits. */
export function formatKpiCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_MEASURED;
  if (Math.abs(value) < 100_000) return formatCount(value);
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 }).format(value);
}

/** Minutes -> "14 min" / "2.8 hrs" / "3.1 days"; null -> "—". */
export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_MEASURED;
  if (value < 60) return `${Math.round(value)} min`;
  const hours = value / 60;
  if (hours < 48) return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hrs`;
  const days = hours / 24;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)} days`;
}

/** Percentage 0..100 with one decimal, or null when there is no denominator. */
export function pct(part: number, total: number): number | null {
  if (!total) return null;
  return Math.round((part / total) * 1000) / 10;
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_MEASURED;
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

export type DeltaPolarity = "higher-is-better" | "lower-is-better";
export type DeltaTone = "good" | "bad" | "neutral";

export interface Delta {
  /** "33%" or "+3" (absolute when the baseline is 0). */
  label: string;
  direction: "up" | "down" | "flat";
  tone: DeltaTone;
}

export function computeDelta(
  current: number | null | undefined,
  previous: number | null | undefined,
  polarity: DeltaPolarity,
): Delta | null {
  if (current === null || current === undefined || previous === null || previous === undefined) return null;
  if (Number.isNaN(current) || Number.isNaN(previous)) return null;
  const diff = current - previous;
  if (diff === 0) return { label: "0%", direction: "flat", tone: "neutral" };
  const direction = diff > 0 ? "up" : "down";
  const improved = polarity === "higher-is-better" ? diff > 0 : diff < 0;
  const tone: DeltaTone = improved ? "good" : "bad";
  if (previous === 0) {
    const abs = Math.abs(diff);
    const rounded = Number.isInteger(abs) ? abs : Math.round(abs * 10) / 10;
    return { label: `${diff > 0 ? "+" : "-"}${rounded}`, direction, tone };
  }
  const change = Math.abs(diff / previous) * 100;
  return { label: `${change >= 10 ? Math.round(change) : change.toFixed(1)}%`, direction, tone };
}

/** "14:32:18" in the viewer's zone; "—" for a missing/invalid timestamp. */
export function formatClock(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined) return NOT_MEASURED;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NOT_MEASURED;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** Elapsed time since `iso` as "12 min" / "3 h 16 min" / "2 d 4 h". */
export function formatAge(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return NOT_MEASURED;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return NOT_MEASURED;
  const minutes = Math.max(0, Math.floor((now - t) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

/** Data is stale when it is older than twice the poll interval. */
export function isStale(updatedAt: number | null | undefined, now: number, pollMs: number): boolean {
  if (!updatedAt) return false;
  return now - updatedAt > pollMs * 2;
}
