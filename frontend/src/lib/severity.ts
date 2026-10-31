/**
 * Incident severity presentation — ONE source for colour, order and labels so
 * every chart, pill and legend on the platform agrees.
 *
 * Backend vocabulary (backend/app/schemas/incident.py IncidentSeverity) is
 * critical | warning | info. "high"/"medium"/"low" are kept only so older
 * payloads that carry them still render with a sensible colour; they are not
 * invented by any UI.
 */

export const SEVERITY_COLOR: Record<string, string> = {
  critical: "#ef4444",
  high: "#f97316",
  warning: "#eab308",
  medium: "#eab308",
  low: "#3b82f6",
  info: "#8b5cf6",
};
export const SEVERITY_FALLBACK = "#64748b";
export const SEVERITY_ORDER = ["critical", "high", "warning", "medium", "low", "info"];

/** The filterable incident severities, worst first (matches the backend enum). */
export const INCIDENT_SEVERITIES = ["critical", "warning", "info"] as const;
export type IncidentSeverityValue = (typeof INCIDENT_SEVERITIES)[number];

export function sevColor(severity: string): string {
  return SEVERITY_COLOR[severity.toLowerCase()] ?? SEVERITY_FALLBACK;
}

export function sevRank(severity: string): number {
  const i = SEVERITY_ORDER.indexOf(severity.toLowerCase());
  return i === -1 ? SEVERITY_ORDER.length : i;
}

export function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

/** null -> "No data" (never "0 min"); < 60 -> minutes; otherwise hours. */
export function formatDuration(minutes: number | null): string {
  if (minutes === null) return "No data";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hrs`;
}
