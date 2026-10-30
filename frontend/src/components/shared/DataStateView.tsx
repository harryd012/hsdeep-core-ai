"use client";

/**
 * DataStateView - the shared renderer for the loading / error / empty /
 * stale / unavailable states, plus the OK pass-through.
 *
 * It consumes the honest state from observabilityState.ts and renders the
 * correct block. On OK it renders its children (the real data). On any
 * other state it renders an explanatory panel instead - it never renders
 * children as if they were data when the request failed or returned
 * nothing. Accessible (role="status", aria-live) and responsive.
 *
 * Presentational: all signals arrive as props; no fetching here.
 */

import { memo, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Inbox,
  Loader2,
  Clock,
  Unplug,
} from "lucide-react";
import FreshnessIndicator from "@/components/shared/FreshnessIndicator";
import {
  dataStateToTone,
  deriveDataState,
  errorMessage,
  type DataState,
  type DataStateErrorKind,
  type DataStateSignals,
} from "@/lib/observability/observabilityState";

const STATE_ICON: Record<DataState, typeof AlertTriangle> = {
  LOADING: Loader2,
  ERROR: AlertTriangle,
  EMPTY: Inbox,
  STALE: Clock,
  UNAVAILABLE: Unplug,
  OK: CheckCircle2,
};

function stateDescription(
  state: DataState,
  errorKind: DataStateErrorKind | null | undefined,
): string {
  switch (state) {
    case "ERROR":
      return errorMessage(errorKind);
    case "LOADING":
      return "Loading telemetry...";
    case "EMPTY":
      return "The source responded but has no records for this view.";
    case "UNAVAILABLE":
      return "The data source is known but could not be reached right now.";
    case "STALE":
      return "Data is older than the freshness policy and is not a live reading.";
    default:
      return "Current data is available.";
  }
}

export interface DataStateViewProps extends DataStateSignals {
  /** Rendered only when the derived state is OK. */
  children: ReactNode;
  /** Optional heading shown above the state/children. */
  title?: string;
  /** When true, render nothing extra on OK besides children (no OK banner). */
  hideOkBanner?: boolean;
}

export default memo(function DataStateView({
  children,
  title,
  hideOkBanner = false,
  ...signals
}: DataStateViewProps) {
  const state = deriveDataState(signals);
  const tone = dataStateToTone(state);
  const Icon = STATE_ICON[state];

  return (
    <div className="obs-stateview" aria-label={title ?? "Data"}>
      {title ? <div className="obs-stateview__title">{title}</div> : null}

      {state === "OK" ? (
        <>{children}</>
      ) : (
        <div
          className="obs-state"
          role="status"
          aria-live="polite"
          data-state={state}
        >
          <span className={`obs-state__badge obs-state__badge--${tone}`}>
            {state === "LOADING" ? (
              <span className="obs-state__pulse" aria-hidden="true" />
            ) : (
              <Icon size={13} aria-hidden="true" />
            )}
            {state}
          </span>
          <p className="obs-state__desc">
            {stateDescription(state, signals.errorKind)}
          </p>
          {state !== "LOADING" && signals.lastCollectedAt ? (
            <FreshnessIndicator
              lastCollectedAt={signals.lastCollectedAt}
              showAge
            />
          ) : null}
        </div>
      )}

      {state === "OK" && !hideOkBanner ? null : null}
    </div>
  );
});