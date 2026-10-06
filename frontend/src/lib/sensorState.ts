/**
 * Canonical sensor state vocabulary for the Monitoring page.
 *
 * The backend reports a small raw status string per sensor (`up`, `down`,
 * `warning`, `unknown`, and collector-level failures). Rendering every non-`up`
 * value as red hides the difference between "this sensor is down", "collection
 * is broken" and "nothing is configured" — operators need those to be
 * visually distinct, so the mapping lives here, once, instead of as inline
 * ternaries in the page.
 *
 * STALE is a freshness-derived state, not a backend status: a sensor that is
 * still reported `up` but whose last value is older than 2 hours must never be
 * presented as live.
 */

export type SensorState =
  | "HEALTHY"
  | "WARNING"
  | "CRITICAL"
  | "DOWN"
  | "STALE"
  | "UNKNOWN"
  | "COLLECTION_FAILED"
  | "NOT_CONFIGURED";

export interface SensorStateInfo {
  state: SensorState;
  color: string;
  label: string;
  dot: string; // CSS variable
}

const STATE_MAP: Record<SensorState, SensorStateInfo> = {
  HEALTHY: { state: "HEALTHY", color: "var(--green)", dot: "var(--green)", label: "Healthy" },
  WARNING: { state: "WARNING", color: "var(--amber)", dot: "var(--amber)", label: "Warning" },
  CRITICAL: { state: "CRITICAL", color: "var(--red)", dot: "var(--red)", label: "Critical" },
  DOWN: { state: "DOWN", color: "var(--red)", dot: "var(--red)", label: "Down" },
  STALE: { state: "STALE", color: "var(--amber)", dot: "var(--amber)", label: "Stale" },
  UNKNOWN: { state: "UNKNOWN", color: "var(--muted)", dot: "var(--muted)", label: "Unknown" },
  COLLECTION_FAILED: {
    state: "COLLECTION_FAILED",
    color: "#ff6b35",
    dot: "#ff6b35",
    label: "Collection failed",
  },
  NOT_CONFIGURED: {
    state: "NOT_CONFIGURED",
    color: "var(--muted)",
    dot: "var(--muted)",
    label: "Not configured",
  },
};

/** States that justify the "Alerting" stat card (anything not healthy/unknown). */
export const ALERTING_STATES: readonly SensorState[] = [
  "WARNING",
  "CRITICAL",
  "DOWN",
  "STALE",
  "COLLECTION_FAILED",
];

/** 2 hours: older than this and a still-`up` sensor is presented as STALE. */
const STALE_AFTER_MS = 2 * 60 * 60 * 1000;

/**
 * Map a raw backend sensor status string to a canonical SensorStateInfo.
 *
 * Backend emits: "up", "down", "warning", "unknown", and potentially
 * "collection_failed", "not_configured". Pass `lastValueAt` to enable
 * freshness-based STALE detection — never render old data as live.
 */
export function classifySensorState(
  status: string | null | undefined,
  lastValueAt?: string | null,
): SensorStateInfo {
  // Stale: still reported `up`, but the value itself is older than 2 hours.
  if (status === "up" && lastValueAt) {
    const ageMs = Date.now() - new Date(lastValueAt).getTime();
    if (!Number.isNaN(ageMs) && ageMs > STALE_AFTER_MS) return STATE_MAP.STALE;
  }

  switch ((status ?? "").toLowerCase()) {
    case "up":
      return STATE_MAP.HEALTHY;
    case "warning":
      return STATE_MAP.WARNING;
    case "critical":
      return STATE_MAP.CRITICAL;
    case "down":
      return STATE_MAP.DOWN;
    case "unknown":
      return STATE_MAP.UNKNOWN;
    case "collection_failed":
      return STATE_MAP.COLLECTION_FAILED;
    case "not_configured":
      return STATE_MAP.NOT_CONFIGURED;
    default:
      return STATE_MAP.UNKNOWN;
  }
}
