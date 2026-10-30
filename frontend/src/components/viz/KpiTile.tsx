"use client";

import { memo } from "react";
import Chart, { type ChartPoint } from "@/components/viz/Chart";
import TileShell from "@/components/viz/TileShell";

/**
 * KpiTile — a large statistic with an optional sparkline trend.
 *
 * The value is shown verbatim; null/undefined renders "N/A" (the honest
 * "unknown", never a reassuring 0). status drives the accent color via the
 * platform's tone vocabulary.
 */
export type KpiStatus = "healthy" | "warning" | "critical" | "neutral";

const STATUS_COLOR: Record<KpiStatus, string> = {
  healthy: "var(--green)",
  warning: "var(--amber)",
  critical: "var(--red)",
  neutral: "var(--cyan)",
};

export interface KpiTileProps {
  title: string;
  /** The headline number, or null when unknown. */
  value: number | string | null;
  /** Small context line under the value, e.g. "of 42 devices". */
  context?: string;
  status?: KpiStatus;
  /** Optional sparkline points (recent trend). null/zero-only -> hidden. */
  spark?: ChartPoint[];
  sparkColor?: string;
  loading?: boolean;
  error?: unknown;
  lastCollectedAt?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
}

function hasRealSpark(spark: ChartPoint[] | undefined): boolean {
  return !!spark && spark.some((d) => d.value !== null && d.value !== 0);
}

export default memo(function KpiTile({
  title,
  value,
  context,
  status = "neutral",
  spark,
  sparkColor,
  loading,
  error,
  lastCollectedAt,
  onRefresh,
  refreshing,
}: KpiTileProps) {
  const color = STATUS_COLOR[status];
  const showSpark = hasRealSpark(spark);

  return (
    <TileShell
      title={title}
      onRefresh={onRefresh}
      refreshing={refreshing}
      lastCollectedAt={lastCollectedAt}
      loading={loading}
      error={error}
      data={value === null && !showSpark ? null : value}
      minHeight={130}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 30, fontWeight: 800, lineHeight: 1, color: value === null ? "var(--muted)" : color }}>
            {value === null ? "N/A" : value}
          </div>
          {context && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{context}</div>}
        </div>
        {showSpark && (
          <div style={{ flex: 1, minWidth: 0, height: 48 }}>
            <Chart
              type="line"
              series={[{ color: sparkColor ?? color, data: spark ?? [] }]}
              height={48}
            />
          </div>
        )}
      </div>
    </TileShell>
  );
});
