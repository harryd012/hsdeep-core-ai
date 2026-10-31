"use client";

import { memo } from "react";
import Chart, { type ChartSeries, type ChartType } from "@/components/viz/Chart";
import TileShell from "@/components/viz/TileShell";

/**
 * ChartTile — a TileShell wrapping a Chart. The owner supplies the series and
 * the honest data-lifecycle signals it already has (loading/error/data/
 * lastCollectedAt). data presence is derived from the series so DataStateView
 * shows EMPTY when there are no real points.
 */
export interface ChartTileProps {
  title: string;
  subtitle?: string;
  help?: string;
  type: ChartType;
  series: ChartSeries[];
  height?: number;
  yLabel?: string;
  unit?: string;
  centerLabel?: { title: string; value: string };
  loading?: boolean;
  error?: unknown;
  lastCollectedAt?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
}

function hasRealPoints(series: ChartSeries[]): boolean {
  return series.some((s) => s.data.some((d) => d.value !== null && d.value !== 0));
}

export default memo(function ChartTile({
  title,
  subtitle,
  help,
  type,
  series,
  height = 200,
  yLabel,
  unit,
  centerLabel,
  loading,
  error,
  lastCollectedAt,
  onRefresh,
  refreshing,
}: ChartTileProps) {
  return (
    <TileShell
      title={title}
      subtitle={subtitle}
      help={help}
      onRefresh={onRefresh}
      refreshing={refreshing}
      lastCollectedAt={lastCollectedAt}
      loading={loading}
      error={error}
      data={hasRealPoints(series) ? series : null}
      minHeight={height + 60}
    >
      <Chart type={type} series={series} height={height} yLabel={yLabel} unit={unit} centerLabel={centerLabel} />
    </TileShell>
  );
});
