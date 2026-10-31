"use client";

import { type ReactNode } from "react";
import { RefreshCw, HelpCircle } from "lucide-react";
import DataStateView, { type DataStateViewProps } from "@/components/shared/DataStateView";

/**
 * TileShell — the consistent chrome every dashboard tile renders through.
 *
 * A tile header (title, optional subtitle, optional help tooltip, refresh
 * timestamp + refresh control) above a body that delegates loading/empty/stale/
 * error/unavailable/OK to the shared DataStateView state machine, so every tile
 * is honest about its data lifecycle in the same way.
 *
 * Presentational: no fetching. The owner passes the DataStateSignals it already
 * has (loading / error / data / lastCollectedAt) plus the OK-body as children.
 */
export interface TileShellProps extends DataStateViewProps {
  title: string;
  subtitle?: string;
  /** Short help text shown as a hover tooltip (the "?" icon). */
  help?: string;
  /** Called by the refresh button; omit to hide the control. */
  onRefresh?: () => void;
  /** Refreshing state for the button spinner. */
  refreshing?: boolean;
  /** Extra nodes rendered in the header right (e.g. a legend toggle). */
  headerActions?: ReactNode;
  /** Minimum height so empty/loading states don't collapse the tile. */
  minHeight?: number;
  children: ReactNode;
}

export default function TileShell({
  title,
  subtitle,
  help,
  onRefresh,
  refreshing,
  headerActions,
  minHeight = 160,
  children,
  ...signals
}: TileShellProps) {
  return (
    <section
      style={{
        border: "1px solid var(--line, #1f2d38)",
        borderRadius: 10,
        background: "var(--surface, rgba(7,19,27,0.4))",
        display: "flex",
        flexDirection: "column",
        minHeight,
        overflow: "hidden",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 14px",
          borderBottom: "1px solid var(--line, #1f2d38)",
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: 0.4,
              textTransform: "uppercase",
              color: "var(--fg, #e6edf3)",
            }}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
            {help && (
              <span title={help} style={{ color: "var(--muted)", cursor: "help", display: "inline-flex" }}>
                <HelpCircle size={12} />
              </span>
            )}
          </div>
          {subtitle && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{subtitle}</div>}
        </div>
        {headerActions}
        {signals.lastCollectedAt && (
          <span style={{ fontSize: 9, color: "var(--muted)", whiteSpace: "nowrap" }}>
            {new Date(signals.lastCollectedAt).toLocaleTimeString()}
          </span>
        )}
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label={`Refresh ${title}`}
            title="Refresh"
            style={{
              display: "inline-flex",
              alignItems: "center",
              background: "transparent",
              border: "1px solid var(--line, #1f2d38)",
              borderRadius: 6,
              color: "var(--muted)",
              padding: "2px 6px",
              cursor: refreshing ? "wait" : "pointer",
            }}
          >
            <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
          </button>
        )}
      </header>
      <div style={{ padding: "12px 14px", flex: 1 }}>
        <DataStateView {...signals} hideOkBanner>
          {children}
        </DataStateView>
      </div>
    </section>
  );
}
