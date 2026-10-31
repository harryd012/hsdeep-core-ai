/**
 * Cloud Operations KPI formatting - pure, deterministic, testable helpers.
 *
 * No React, no fetch: maps a CloudOperationsKPI payload onto the
 * string/number/null that MetricTile renders, preserving the
 * "genuine zero vs unavailable vs no-data" distinction the dashboard must
 * never flatten. Kept as a plain module (like dashboard/metrics.ts) so the
 * node:test suite can import it without a DOM or React renderer.
 */

export interface CloudOpsKpiValue {
  value: number | string | Record<string, unknown> | null;
  unavailable: boolean;
  unavailable_reason: string | null;
  detail: Record<string, unknown> | null;
}

export interface HealthBuckets {
  online?: number | null;
  offline?: number | null;
  stale?: number | null;
  unknown?: number | null;
  total?: number | null;
}

/** "N/M online" - a real zero total stays "0"; no object is "N/A". */
export function formatHealthValue(v: HealthBuckets | null | undefined): string {
  if (!v) return "N/A";
  const online = v.online ?? 0;
  const total = v.total ?? 0;
  if (total === 0) return "0";
  return `${online}/${total} online`;
}

/** One-line breakdown, omitting zero-value buckets so a zero never reads as a fault. */
export function healthBreakdown(v: HealthBuckets | null | undefined): string {
  if (!v) return "";
  const parts: string[] = [];
  if ((v.online ?? 0) > 0) parts.push(`${v.online} online`);
  if ((v.offline ?? 0) > 0) parts.push(`${v.offline} offline`);
  if ((v.stale ?? 0) > 0) parts.push(`${v.stale} stale`);
  if ((v.unknown ?? 0) > 0) parts.push(`${v.unknown} unknown`);
  return parts.join(" · ");
}

export interface KpiRender {
  value: string | number | null;
  note?: string;
  unavailable: boolean;
}

/**
 * Map one KPI onto MetricTile props.
 *
 *   - unavailable  -> null value + reason + unavailable flag
 *   - null value   -> null value + "No data reported." (EMPTY, not an error)
 *   - object value -> formatted health string + bucket breakdown
 *   - number/str   -> the value itself (a measured zero is a real zero)
 */
export function resolveKpi(kpi: CloudOpsKpiValue): KpiRender {
  if (kpi.unavailable) {
    return {
      value: null,
      unavailable: true,
      note: kpi.unavailable_reason ?? "Source not configured.",
    };
  }
  if (kpi.value === null || kpi.value === undefined) {
    return { value: null, unavailable: false, note: "No data reported." };
  }
  if (typeof kpi.value === "object") {
    const h = kpi.value as unknown as HealthBuckets;
    const breakdown = healthBreakdown(h);
    return {
      value: formatHealthValue(h),
      unavailable: false,
      note: breakdown || undefined,
    };
  }
  const n = kpi.value;
  if (
    kpi.detail &&
    typeof kpi.detail.total === "number" &&
    typeof kpi.detail.critical === "number"
  ) {
    return {
      value: n,
      unavailable: false,
      note: `${kpi.detail.total} total · ${kpi.detail.critical} critical`,
    };
  }
  return { value: n, unavailable: false };
}
