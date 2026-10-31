/**
 * metrics.ts — SINGLE SOURCE OF TRUTH for dashboard metric derivation.
 *
 * Every tile on the dashboard that shows "devices online / offline / stale /
 * unknown" or "collector success" MUST read from here, so the same number can
 * never appear twice with two different values. Pure and dependency-free (only
 * imports ./activitySort) so the definitions are unit-testable in isolation.
 *
 * Metric definitions (the contract every tile relies on):
 *
 *   device state (classifyDevice) — up / down / unknown / stale:
 *     * "up"     — recent evidence (last_seen_at within threshold) + an
 *                  up/online status. A measured reachable device.
 *     * "down"   — recent evidence + a down-ish status (down/offline/critical/
 *                  error/unreachable/degraded/warning). Only then is "down"
 *                  honest: a failed probe confirmed it.
 *     * "stale"  — last_seen_at is present but OLDER than the freshness
 *                  threshold. We cannot confirm current state, so it is NOT
 *                  labelled down (collectors may simply have stopped reporting).
 *     * "unknown"— no usable last_seen_at, or a status that carries no health
 *                  opinion. No evidence, no verdict.
 *
 *   collector success (collectorSuccessPct) — getAutomationStatus()'s
 *     success_rate_pct over the last 24h. Zero runs => null ("unknown"), never
 *     a fabricated 0% (a measured 0% would mean every run failed).
 */
import { toEpochMs } from "./activitySort.ts";

export type DeviceState = "up" | "down" | "unknown" | "stale";

/** A device not seen within this window is STALE, not up/down. */
export const DEVICE_FRESHNESS_THRESHOLD_MS = 24 * 60 * 60 * 1000;

export interface DeviceLike {
  status?: string | null;
  last_seen_at?: string | null;
}

/** Statuses that carry a positive health opinion. */
const UP_STATUSES = new Set(["up", "online"]);

/**
 * Statuses that carry a "not healthy" opinion. Deliberately a fixed list: a
 * status we do not recognise is NOT counted as down (that would invent an
 * outage). Includes the collector vocabulary's warning/degraded band.
 */
const DOWN_STATUSES = new Set([
  "down",
  "offline",
  "critical",
  "error",
  "unreachable",
  "degraded",
  "warning",
]);

/**
 * Classify one device from its status + last_seen_at. Freshness always wins:
 * a device with no recent evidence is never "down", because "down" requires a
 * confirming (recent) probe result.
 */
export function classifyDevice(
  device: DeviceLike,
  now: number = Date.now(),
): DeviceState {
  const status = (device.status ?? "").toLowerCase().trim();
  const lastSeenMs = toEpochMs(device.last_seen_at);

  if (lastSeenMs === null) {
    return "unknown";
  }
  if (now - lastSeenMs > DEVICE_FRESHNESS_THRESHOLD_MS) {
    return "stale";
  }
  if (UP_STATUSES.has(status)) return "up";
  if (DOWN_STATUSES.has(status)) return "down";
  return "unknown";
}

export interface DeviceStateCounts {
  up: number;
  down: number;
  unknown: number;
  stale: number;
  total: number;
}

/**
 * The canonical device-state rollup. Every "devices X" tile derives from this
 * one count, so online/offline/stale/unknown are always mutually consistent.
 */
export function countDeviceStates(
  devices: DeviceLike[] | undefined,
  now: number = Date.now(),
): DeviceStateCounts {
  const counts: DeviceStateCounts = { up: 0, down: 0, unknown: 0, stale: 0, total: 0 };
  if (!devices) return counts;
  counts.total = devices.length;
  for (const device of devices) {
    counts[classifyDevice(device, now)] += 1;
  }
  return counts;
}

/** A "needs attention" device is one CONFIRMED down — not merely stale/unknown. */
export function isDegradedDevice(state: DeviceState): boolean {
  return state === "down";
}

export interface CollectorStatusLike {
  runs_last_24h?: number | null;
  success_rate_pct?: number | null;
}

/**
 * Collector success rate over 24h, or null when the window has no runs (or the
 * source did not answer). null renders "N/A"; it is never a fabricated 0%.
 */
export function collectorSuccessPct(
  automation: CollectorStatusLike | undefined,
): number | null {
  if (!automation) return null;
  if (typeof automation.runs_last_24h !== "number" || automation.runs_last_24h <= 0) {
    return null;
  }
  if (typeof automation.success_rate_pct !== "number") return null;
  return Math.round(automation.success_rate_pct);
}

/** Collection runs in the last 24h, or null when the source did not answer. */
export function collectorRuns24h(
  automation: CollectorStatusLike | undefined,
): number | null {
  if (!automation || typeof automation.runs_last_24h !== "number") return null;
  return automation.runs_last_24h;
}
