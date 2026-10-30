/**
 * observabilityState.ts - the single, honest data-lifecycle state machine
 * that every observability workspace renders through.
 *
 * WHY THIS EXISTS
 * ---------------
 * Across the existing dashboard each component hand-rolled its own
 * loading/error/empty handling, and several drifted into the two failure
 * modes this platform must never exhibit:
 *   - rendering a *failed* fetch as a successful zero, and
 *   - presenting absent evidence as if it were fresh/live.
 *
 * This module derives ONE state per view from the raw signals a React Query
 * call already exposes (loading / error / data / lastCollectedAt), so a view
 * cannot accidentally do either. Pure and deterministic: no fetching, no side
 * effects, no fabricated values.
 *
 * TRUTH RULES (why this is a state machine, not a set of booleans):
 *   - ERROR outranks every data-derived state. A failed request is NEVER
 *     rendered as data (and never as 0).
 *   - UNAVAILABLE = the source is known but currently unreachable. Distinct
 *     from EMPTY (reachable, legitimately nothing) and ERROR (bad request).
 *   - EMPTY = reachable source, genuinely no rows. Absence is not failure
 *     and is not freshness.
 *   - STALE = data exists but its last-collected timestamp is older than the
 *     freshness policy. Stale data is still shown (it is real), but is never
 *     labelled live/fresh.
 *   - LOADING is only the *initial* load with no prior data; a background
 *     refetch that already has data is NOT loading.
 *   - OK requires real data that is not stale.
 *
 * "Never map missing/failed data to zero" is structural: no state produces a
 * numeric value, because a view with no data renders the EMPTY/ERROR/
 * UNAVAILABLE block and not the metric body at all.
 */

/** Loading lifecycle collapsed to the states a view must be able to show. */
export type DataState =
  | "LOADING" // initial load, nothing to show yet
  | "ERROR" // request/response failed - never rendered as a value
  | "EMPTY" // reachable source, genuinely no rows
  | "STALE" // data exists but is older than the freshness policy
  | "UNAVAILABLE" // source known but not reachable right now
  | "OK"; // real, non-stale data present

/**
 * The four-token status vocabulary the whole platform already uses (Panel,
 * ReadinessCheckCard, topology visuals). Reused verbatim. UNKNOWN/absent is
 * deliberately "info" (neutral), never "healthy".
 */
export type StatusTone = "healthy" | "warning" | "critical" | "info";

export interface DataStateVisual {
  /** Short uppercase badge label. */
  label: string;
  /** One-line operator-facing explanation. Never a metric, never a claim. */
  description: string;
  /** Existing status token this state paints with. */
  tone: StatusTone;
}

export const DATA_STATE_VISUALS: Record<DataState, DataStateVisual> = {
  LOADING: { label: "LOADING", description: "Loading telemetry...", tone: "info" },
  ERROR: {
    label: "ERROR",
    description: "The request failed. No data is shown.",
    tone: "critical",
  },
  EMPTY: {
    label: "NO DATA",
    description: "The source responded but has no records for this view.",
    tone: "info",
  },
  STALE: {
    label: "STALE",
    description: "Data is older than the freshness policy and is not live.",
    tone: "warning",
  },
  UNAVAILABLE: {
    label: "UNAVAILABLE",
    description: "The data source is known but could not be reached.",
    tone: "warning",
  },
  OK: { label: "OK", description: "Current data is available.", tone: "healthy" },
};

/** Tone -> existing design token. Single source for every consumer. */
export const STATUS_TONE_COLOR: Record<StatusTone, string> = {
  healthy: "var(--green)",
  warning: "var(--amber)",
  critical: "var(--red)",
  info: "var(--information)",
};

/**
 * Coarse, reason-coded error so the UI can explain a failure without ever
 * leaking a raw message that might contain a secret. Mirrors the platform's
 * convention of bounded, non-sensitive error text.
 */
export type DataStateErrorKind =
  | "unauthorized" // 401
  | "forbidden" // 403
  | "not_found" // 404
  | "rate_limited" // 429
  | "timeout" // request/response timeout
  | "server" // 5xx
  | "network" // DNS/connection refused
  | "unknown"; // anything else

export const DATA_STATE_ERROR_LABEL: Record<DataStateErrorKind, string> = {
  unauthorized: "Not authorized - session may have expired",
  forbidden: "Not permitted for this tenant",
  not_found: "Requested resource was not found",
  rate_limited: "Rate or quota limit reached",
  timeout: "Request timed out",
  server: "Upstream server error",
  network: "Could not reach the API",
  unknown: "Request failed",
};

/** Signals a view already has. All optional; absent means "unknown". */
export interface DataStateSignals {
  /** True only for the INITIAL load (no data yet). A background refetch that
   *  already has data must NOT set this. */
  loading?: boolean;
  /** A failed request/response. Any truthy value outranks data. */
  error?: unknown;
  /** Optional coarse error kind for a human, non-sensitive message. */
  errorKind?: DataStateErrorKind | null;
  /** The fetched payload. undefined/null => no data. An empty array or object
   *  counts as "no rows" (EMPTY), not as an error. */
  data?: unknown;
  /** UTC ISO timestamp of the last real collection, or null when never. */
  lastCollectedAt?: string | null;
  /** When true, the source is known but currently unreachable. */
  unavailable?: boolean;
  /** Freshness policy in minutes; data older than this is STALE. Defaults to
   *  the shared 30-minute AGING threshold FreshnessIndicator uses. */
  staleAfterMinutes?: number;
}

export const DEFAULT_STALE_AFTER_MINUTES = 30;

/** True when the payload has at least one real record. */
function hasRecords(data: unknown): boolean {
  if (data === null || data === undefined) return false;
  if (Array.isArray(data)) return data.length > 0;
  if (typeof data === "number") return Number.isFinite(data);
  if (typeof data === "string") return data.length > 0;
  if (typeof data === "object") return Object.keys(data as object).length > 0;
  return true;
}

/** Age of the last collection in minutes, or null when not determinable. */
function ageMinutes(lastCollectedAt: string | null | undefined): number | null {
  if (!lastCollectedAt) return null;
  const ms = Date.now() - new Date(lastCollectedAt).getTime();
  if (Number.isNaN(ms)) return null;
  return ms / 60_000;
}

/**
 * Derive the single honest state for a view from its raw signals.
 *
 * Precedence (first match wins) - this ordering is the truth guarantee:
 *   1. ERROR        (a failed request is never data, never zero)
 *   2. UNAVAILABLE  (known source, unreachable now)
 *   3. LOADING      (initial load, nothing yet)
 *   4. EMPTY        (reachable, genuinely no rows)
 *   5. STALE        (data exists but older than the policy)
 *   6. OK           (real, non-stale data)
 */
export function deriveDataState(signals: DataStateSignals): DataState {
  if (signals.error) return "ERROR";
  if (signals.unavailable) return "UNAVAILABLE";
  if (signals.loading) return "LOADING";
  if (!hasRecords(signals.data)) return "EMPTY";

  const minutes = ageMinutes(signals.lastCollectedAt);
  if (minutes === null) {
    // Real data but no collection timestamp: we cannot assert freshness, so
    // we never call it fresh. Shown, but flagged STALE rather than painted OK.
    return "STALE";
  }
  const limit = signals.staleAfterMinutes ?? DEFAULT_STALE_AFTER_MINUTES;
  if (minutes > limit) return "STALE";
  return "OK";
}

/** Convenience: the visual for a given set of signals. */
export function dataStateVisual(signals: DataStateSignals): DataStateVisual {
  return DATA_STATE_VISUALS[deriveDataState(signals)];
}

/** Map a derived state onto the existing 4-token Panel status vocabulary. */
export function dataStateToTone(state: DataState): StatusTone {
  return DATA_STATE_VISUALS[state].tone;
}

/**
 * Operator-facing message for an error, preferring the coarse reason kind.
 * Never includes the raw error, which could carry a secret.
 */
export function errorMessage(
  errorKind: DataStateErrorKind | null | undefined,
): string {
  return DATA_STATE_ERROR_LABEL[errorKind ?? "unknown"];
}