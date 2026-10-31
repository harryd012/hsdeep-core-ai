"use client";

/**
 * MetricTile - the reusable stat/metric card for observability workspaces.
 *
 * It is deliberately NOT a chart: it shows a single real value with a unit,
 * a freshness indicator and an optional sparkline, OR an honest
 * loading/error/empty/stale/unavailable block. It NEVER renders a
 * fabricated number for missing or failed data - when the underlying query
 * has no value, the tile shows the derived state instead (see
 * observabilityState.ts). This is the structural "no false zero" guarantee.
 *
 * Presentational: all data arrives as props. It reuses the existing
 * FreshnessIndicator and the shared 4-token status vocabulary.
 */

import { memo, type ReactNode } from "react";
import FreshnessIndicator, { type FreshnessBucket } from "@/components/shared/FreshnessIndicator";
import Sparkline from "./Sparkline";
import {
  dataStateToTone,
  deriveDataState,
  errorMessage,
  type DataState,
  type DataStateErrorKind,
  type StatusTone,
} from "@/lib/observability/observabilityState";

export interface MetricTileProps {
  /** Uppercase metric label, e.g. "CPU UTILIZATION". */
  label: string;
  /** The measured value, formatted by the caller (string keeps units precise).
   *  Pass null when there is genuinely no value - the tile shows a state, not 0. */
  value: string | number | null;
  /** Optional unit suffix rendered smaller, e.g. "%", "ms", "req/s". */
  unit?: string;
  /** UTC ISO timestamp of last collection, or null when never. Drives the
   *  freshness badge and the STALE derivation. */
  lastCollectedAt?: string | null;
  /** Optional trend series for the sparkline (nulls are gaps). */
  spark?: ReadonlyArray<number | null | undefined>;
  /** Colour for the sparkline (a design token). */
  sparkColor?: string;
  /** Small explanatory note under the value (never a claim of health). */
  note?: ReactNode;
  /** Override the freshness bucket if a backend computes it. */
  freshnessBucket?: FreshnessBucket;
  /** Set true when the source is known but currently unreachable. */
  unavailable?: boolean;
  /** A failed request. Any truthy value forces the ERROR state. */
  error?: unknown;
  /** Coarse error kind for a non-sensitive message. */
  errorKind?: DataStateErrorKind | null;
  /** Freshness policy in minutes (default 30, shared with FreshnessIndicator). */
  staleAfterMinutes?: number;
}

/** The concise copy for the non-OK states. */
function stateCopy(state: DataState, errorKind: DataStateErrorKind | null | undefined): string {
  switch (state) {
    case "ERROR":
      return errorMessage(errorKind);
    case "LOADING":
      return "Loading metric...";
    case "EMPTY":
      return "No data reported for this metric.";
    case "UNAVAILABLE":
      return "Source unavailable - value could not be read.";
    case "STALE":
      return "Value is stale; not a live reading.";
    default:
      return "";
  }
}

export default memo(function MetricTile({
  label,
  value,
  unit,
  lastCollectedAt,
  spark,
  sparkColor,
  note,
  freshnessBucket,
  unavailable,
  error,
  errorKind,
  staleAfterMinutes,
}: MetricTileProps) {
  const loading = value === null && !error && !unavailable;
  const state: DataState = deriveDataState({
    loading,
    error,
    errorKind,
    data: value,
    lastCollectedAt,
    unavailable,
    staleAfterMinutes,
  });
  const tone: StatusTone = dataStateToTone(state);

  return (
    <div
      className={`obs-metric obs-metric--${tone}`}
      role="group"
      aria-label={label}
      data-state={state}
    >
      <div className="obs-metric__head">
        <span className="obs-metric__label">{label}</span>
        <FreshnessIndicator
          lastCollectedAt={lastCollectedAt ?? null}
          bucket={freshnessBucket}
          compact
        />
      </div>

      {state === "OK" ? (
        <div className="obs-metric__value">
          {value}
          {unit ? <span className="obs-metric__unit">{unit}</span> : null}
        </div>
      ) : (
        <div className="obs-metric__value" aria-live="polite">
          {state === "ERROR" ? "--" : state === "UNAVAILABLE" ? "N/A" : "--"}
        </div>
      )}

      <div className="obs-metric__foot">
        <span className="obs-metric__note">
          {note != null ? note : state === "OK" ? "" : stateCopy(state, errorKind)}
        </span>
        {spark && spark.length > 1 ? (
          <span className="obs-metric__spark">
            <Sparkline
              points={spark}
              color={sparkColor ?? "var(--cyan)"}
              label={`${label} trend`}
            />
          </span>
        ) : null}
      </div>
    </div>
  );
});