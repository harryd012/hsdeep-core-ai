/**
 * Device and link state → presentation tokens (Phase 9.5).
 *
 * This module is a PROJECTION, not an engine. It reads the canonical tokens the
 * backend already produced (operational state, health bucket, telemetry
 * freshness, lifecycle state, relationship status) and decides which of the six
 * device states / five link states to show. It computes no health score, owns no
 * thresholds, and queries nothing — if it disagrees with the backend that is a
 * bug here, not a judgement call.
 *
 * Colour is never the only signal: every state also carries a shape marker, a
 * dash pattern and a text label.
 */

export type DeviceState =
  | "ONLINE"
  | "WARNING"
  | "OFFLINE"
  | "UNKNOWN"
  | "STALE"
  | "MAINTENANCE";

export type LinkState = "UP" | "DOWN" | "DEGRADED" | "UNKNOWN" | "STALE";

export interface StateVisual<S extends string> {
  state: S;
  label: string;
  /** CSS custom property from the app's existing palette — no new colours. */
  color: string;
  /** SVG stroke-dasharray. Empty string = solid. */
  dash: string;
  /** Shape token so state survives greyscale and colour-blind rendering. */
  marker: "dot" | "ring" | "triangle" | "square" | "hex";
  description: string;
}

export const DEVICE_STATE_VISUALS: Record<DeviceState, StateVisual<DeviceState>> = {
  ONLINE: {
    state: "ONLINE",
    label: "Online",
    color: "var(--green)",
    dash: "",
    marker: "dot",
    description: "Reachable and operating normally.",
  },
  WARNING: {
    state: "WARNING",
    label: "Warning",
    color: "var(--amber)",
    dash: "",
    marker: "triangle",
    description:
      "Reachable, but the platform reports degradation or a critical health bucket.",
  },
  OFFLINE: {
    state: "OFFLINE",
    label: "Offline",
    color: "var(--red)",
    dash: "",
    marker: "square",
    description: "The stored operational state is down.",
  },
  UNKNOWN: {
    state: "UNKNOWN",
    label: "Unknown",
    color: "var(--muted)",
    dash: "",
    marker: "ring",
    description: "No operational state has been established for this resource.",
  },
  STALE: {
    state: "STALE",
    label: "Data stale",
    color: "var(--muted)",
    dash: "2 3",
    marker: "hex",
    description:
      "Telemetry is older than the stale window, so the last known state cannot be asserted.",
  },
  MAINTENANCE: {
    state: "MAINTENANCE",
    label: "Maintenance",
    color: "var(--muted)",
    dash: "",
    marker: "hex",
    description: "Deliberately out of service (maintenance lifecycle or health).",
  },
};

export const LINK_STATE_VISUALS: Record<LinkState, StateVisual<LinkState>> = {
  UP: {
    state: "UP",
    label: "Up",
    color: "var(--green)",
    dash: "",
    marker: "dot",
    description: "The recorded relationship state is active.",
  },
  DOWN: {
    state: "DOWN",
    label: "Down",
    color: "var(--red)",
    dash: "6 4",
    marker: "square",
    description:
      "The recorded relationship state is failed, or an endpoint interface is down.",
  },
  DEGRADED: {
    state: "DEGRADED",
    label: "Degraded",
    color: "var(--amber)",
    dash: "10 4",
    marker: "triangle",
    description: "The recorded relationship state is degraded.",
  },
  UNKNOWN: {
    state: "UNKNOWN",
    label: "Unknown",
    color: "var(--muted)",
    dash: "1 4",
    marker: "ring",
    description: "No state has been recorded for this relationship.",
  },
  STALE: {
    state: "STALE",
    label: "Data stale",
    color: "var(--muted)",
    dash: "3 3",
    marker: "hex",
    description:
      "The recorded link state is older than the stale window, so it cannot be asserted as current.",
  },
};

export interface DeviceStateInput {
  /** Canonical operational state: up | down | degraded | unknown. */
  operationalState?: string | null;
  /** Canonical health bucket, when the graph carries one. */
  health?: string | null;
  /** Canonical freshness state: fresh | aging | stale | expired | never_collected. */
  freshnessState?: string | null;
  lifecycleState?: string | null;
}

/**
 * The six device states, in precedence order:
 *
 *   1. MAINTENANCE  lifecycle or health says maintenance (deliberate, not a fault)
 *   2. OFFLINE      the stored operational state is down
 *   3. WARNING      degraded, or a warning/critical health bucket
 *   4. STALE        the telemetry we hold has aged out (stale | expired)
 *   5. ONLINE       the stored operational state is up, with usable telemetry
 *   6. UNKNOWN      nothing established
 *
 * OFFLINE outranks STALE because a recorded outage is actionable; STALE outranks
 * ONLINE because an aged reading cannot support a claim of current health - the
 * platform must not report a device as live on data it knows is old.
 * `never_collected` is deliberately NOT stale: "no telemetry has ever arrived" is
 * a different fact from "the telemetry is old", and it is shown as such.
 */
export function deviceState(input: DeviceStateInput): DeviceState {
  const lifecycle = (input.lifecycleState ?? "").toLowerCase();
  const health = (input.health ?? "").toLowerCase();
  if (lifecycle === "maintenance" || health === "maintenance") return "MAINTENANCE";

  const operational = (input.operationalState ?? "").toLowerCase();
  if (operational === "down") return "OFFLINE";
  if (operational === "degraded") return "WARNING";
  if (health === "warning" || health === "critical") return "WARNING";

  const freshness = (input.freshnessState ?? "").toLowerCase();
  if (freshness === "stale" || freshness === "expired") return "STALE";

  if (operational === "up") return "ONLINE";
  return "UNKNOWN";
}

export interface LinkStateInput {
  /** Relationship status exactly as stored: active | degraded | failed | unknown. */
  statusRaw?: string | null;
  /** The backend's freshness for the link's own interface telemetry. */
  freshnessState?: string | null;
  localOperStatus?: string | null;
  remoteOperStatus?: string | null;
}

/**
 * The five link states, in precedence order:
 *
 *   1. DOWN      recorded state failed, or an endpoint interface is down
 *   2. STALE     the link's telemetry is stale/expired, so no current state is asserted
 *   3. DEGRADED  recorded state degraded
 *   4. UP        recorded state active
 *   5. UNKNOWN   nothing recorded
 *
 * DOWN outranks STALE for the same reason it does on a device: a recorded failure
 * is actionable. A link with no interface telemetry at all stays UP when the
 * RELATIONSHIP says active — the relationship is the evidence, the interfaces are
 * detail — which is why `never_collected` is not treated as stale here.
 */
export function linkState(input: LinkStateInput): LinkState {
  const status = (input.statusRaw ?? "").toLowerCase();
  const local = (input.localOperStatus ?? "").toLowerCase();
  const remote = (input.remoteOperStatus ?? "").toLowerCase();
  if (status === "failed" || local === "down" || remote === "down") return "DOWN";

  const freshness = (input.freshnessState ?? "").toLowerCase();
  if (freshness === "stale" || freshness === "expired") return "STALE";

  if (status === "degraded") return "DEGRADED";
  if (status === "active") return "UP";
  return "UNKNOWN";
}

/** True when a freshness state is current enough to assert (fresh | aging). */
export function isFreshnessCurrent(state: string | null | undefined): boolean {
  const token = (state ?? "").toLowerCase();
  return token === "fresh" || token === "aging";
}

/** True when nothing has ever been collected for this resource. */
export function isNeverCollected(state: string | null | undefined): boolean {
  const token = (state ?? "").toLowerCase();
  return token === "never_collected" || token === "unavailable";
}

/** Human label for a freshness state; an unrecognised token is shown verbatim. */
export function freshnessLabel(state: string | null | undefined): string {
  switch ((state ?? "").toLowerCase()) {
    case "fresh":
      return "Fresh";
    case "aging":
      return "Aging";
    case "stale":
      return "Stale";
    case "expired":
      return "Expired";
    case "never_collected":
      return "No telemetry collected";
    case "unavailable":
      return "Telemetry unavailable";
    default:
      return state ? String(state) : "Unknown";
  }
}
