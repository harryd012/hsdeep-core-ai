/**
 * platformMetrics.ts - the shape of GET /api/platform/observability/summary
 * (superuser only, enforced server-side) and the formatting the Platform
 * Observability page needs. Pure: the page does the fetch, so this stays
 * testable under node --test.
 *
 * The backend reads its own in-process Prometheus registry: values are
 * cumulative since that process started and cover that process only
 * (`scope.runtime_mode` says which role). Every unobserved value is null and
 * must render as "Not measured", never as 0 or as healthy.
 */
export interface RouteStat {
  method: string;
  route: string;
  requests: number;
  errors: number;
  mean_seconds: number | null;
  p95_seconds: number | null;
}

export interface SourceTypeStat {
  source_type: string;
  success: number;
  error: number;
  success_ratio: number | null;
  mean_seconds: number | null;
  p95_seconds: number | null;
}

export interface PlatformMetricsSummary {
  generated_at: string;
  scope: { kind: "process"; runtime_mode: string; version: string; since: string; uptime_seconds: number; note: string };
  http: {
    requests_total: number;
    errors_total: number;
    error_ratio: number | null;
    in_progress: number;
    p50_seconds: number | null;
    p95_seconds: number | null;
    p99_seconds: number | null;
    status_classes: Record<"2xx" | "3xx" | "4xx" | "5xx", number>;
    top_routes: RouteStat[];
    slowest_routes: RouteStat[];
  };
  collection: {
    runs_total: number;
    success_total: number;
    error_total: number;
    skipped_total: number;
    success_ratio: number | null;
    p95_seconds: number | null;
    by_source_type: SourceTypeStat[];
    failures_by_stage: Record<string, number>;
    phase_mean_seconds: Record<string, number | null>;
    items_upserted: Record<string, number>;
  };
  scheduler: {
    last_tick_at: string | null;
    last_tick_age_seconds: number | null;
    queue_depth: number | null;
    ticks_total: number;
    tick_failures_total: number;
    tick_mean_seconds: number | null;
  };
  alerts: { created_total: number; by_severity: Record<string, number>; by_source: Record<string, number> };
}

export const SUMMARY_PATH = "/api/platform/observability/summary";

/** Same marker as the page kit: KPI tiles render it muted with "Not measured". */
export const NOT_MEASURED = "—";

/** 0.0123 s -> "12 ms"; 2.5 s -> "2.50 s"; null -> NOT_MEASURED. */
export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NOT_MEASURED;
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(2)} s`;
  return `${(value / 60).toFixed(1)} min`;
}

/** 0.0042 -> "0.42%"; null -> NOT_MEASURED. */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NOT_MEASURED;
  return `${(value * 100).toFixed(digits)}%`;
}

/** 3723 -> "1 h 2 min". */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return NOT_MEASURED;
  const m = Math.floor(seconds / 60);
  if (m < 1) return `${Math.floor(seconds)} s`;
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export type HealthTone = "good" | "warn" | "bad" | "neutral";

/** Thresholds shared by the KPI tiles; they mirror the Phase 5 alert rules. */
export const THRESHOLDS = {
  errorRatioWarn: 0.01,
  errorRatioBad: 0.05,
  p95WarnSeconds: 1,
  p95BadSeconds: 2.5,
  collectionSuccessWarn: 0.95,
  collectionSuccessBad: 0.8,
  /** Scheduler ticks every minute; two missed ticks is stale. */
  tickStaleSeconds: 180,
} as const;

/** Tone for a value where higher is worse; neutral when not measured. */
export function toneHigherWorse(value: number | null, warn: number, bad: number): HealthTone {
  if (value === null || !Number.isFinite(value)) return "neutral";
  if (value >= bad) return "bad";
  if (value >= warn) return "warn";
  return "good";
}

/** Tone for a value where lower is worse; neutral when not measured. */
export function toneLowerWorse(value: number | null, warn: number, bad: number): HealthTone {
  if (value === null || !Number.isFinite(value)) return "neutral";
  if (value < bad) return "bad";
  if (value < warn) return "warn";
  return "good";
}

/** Requests per minute averaged over the process lifetime, or null. */
export function averagePerMinute(total: number, uptimeSeconds: number): number | null {
  if (!Number.isFinite(uptimeSeconds) || uptimeSeconds < 60 || total <= 0) return null;
  return total / (uptimeSeconds / 60);
}

// -- Prometheus scrape targets (GET /api/platform/observability/prometheus) ----

export const PROMETHEUS_TARGETS_PATH = "/api/platform/observability/prometheus";

export interface PrometheusTarget {
  job: string;
  component: string | null;
  instance: string;
  health: "up" | "down" | "unknown";
  last_scrape: string | null;
  last_scrape_seconds: number | null;
  last_error: string | null;
}

export interface PrometheusTargets {
  /** False when the backend has no PROMETHEUS_URL. */
  configured: boolean;
  reachable: boolean;
  /** A category, never a raw message: "invalid_url" | "timeout" | "unreachable". */
  error: "invalid_url" | "timeout" | "unreachable" | null;
  checked_at: string;
  targets: PrometheusTarget[];
  jobs: { job: string; up: number; total: number }[];
}

/** Prometheus reports a never-scraped target's lastScrape as the zero time. */
export function scrapedAt(iso: string | null): string | null {
  if (!iso || iso.startsWith("0001-")) return null;
  return iso;
}

export function prometheusProblem(t: PrometheusTargets): string | null {
  if (!t.configured) return "Not configured: set PROMETHEUS_URL on the backend (for example http://127.0.0.1:9090).";
  if (t.error === "invalid_url") return "PROMETHEUS_URL is not a valid http(s) URL.";
  if (t.error === "timeout") return "Prometheus did not answer within 3 seconds.";
  if (!t.reachable) return "Prometheus is configured but could not be reached.";
  return null;
}

// -- Platform health (GET /api/platform/observability/health) -----------------

export const PLATFORM_HEALTH_PATH = "/api/platform/observability/health";

export type HealthStatus = "HEALTHY" | "DEGRADED" | "WARNING" | "CRITICAL" | "UNKNOWN";

export interface ServiceHealth {
  key: string;
  name: string;
  status: HealthStatus;
  reason: string;
  evidence: Record<string, unknown>;
  rules: string[];
}

export interface PlatformHealth {
  checked_at: string;
  overall: HealthStatus;
  services: ServiceHealth[];
  not_available: { key: string; name: string; needs: string }[];
}

export const HEALTH_TONE: Record<HealthStatus, "good" | "info" | "warn" | "bad" | "neutral"> = {
  HEALTHY: "good",
  DEGRADED: "info",
  WARNING: "warn",
  CRITICAL: "bad",
  UNKNOWN: "neutral",
};

/** Counts per status, worst first, for the summary strip. */
export function healthCounts(services: ServiceHealth[]): { status: HealthStatus; count: number }[] {
  const order: HealthStatus[] = ["CRITICAL", "WARNING", "DEGRADED", "HEALTHY", "UNKNOWN"];
  return order.map((status) => ({ status, count: services.filter((s) => s.status === status).length }));
}

// -- Alert rules (GET /api/platform/observability/alerts) ---------------------

export const ALERT_RULES_PATH = "/api/platform/observability/alerts";

export interface ActiveAlert {
  state: "pending" | "firing";
  active_at: string | null;
  value: string | null;
  labels: Record<string, string>;
  summary: string | null;
}

export interface AlertRule {
  group: string;
  name: string;
  state: "inactive" | "pending" | "firing";
  severity: string | null;
  summary: string | null;
  description: string | null;
  for_seconds: number | null;
  health: string | null;
  last_error: string | null;
  alerts: ActiveAlert[];
}

export interface AlertRules {
  configured: boolean;
  reachable: boolean;
  error: "invalid_url" | "timeout" | "unreachable" | null;
  checked_at: string;
  rules: AlertRule[];
  firing: number;
  pending: number;
}

// -- Grafana dashboards -----------------------------------------------------

/** The provisioned dashboards (deployment/observability/grafana/build_dashboards.py). */
export const GRAFANA_DASHBOARDS = [
  { uid: "hsdeep-executive", title: "Executive Platform Health", about: "Availability, RED, collection success, scheduler freshness, dependencies" },
  { uid: "hsdeep-api", title: "API Performance", about: "Requests, 4xx / 5xx, p50 / p95 / p99, slowest and busiest routes" },
  { uid: "hsdeep-collectors", title: "Collector Operations", about: "Runs by source type, failures by stage, durations, items saved" },
  { uid: "hsdeep-scheduler", title: "Scheduler & Worker Health", about: "Tick age and duration, process availability, DB pool, memory and CPU" },
  { uid: "hsdeep-alerts", title: "Alert Pipeline", about: "Alerts opened by severity and source, persist-phase timing and failures" },
] as const;

/** Grafana dashboard URL with the same time range, or null without a Grafana base. */
export function grafanaDashboardUrl(base: string | null, uid: string, from = "now-6h", to = "now"): string | null {
  if (!base) return null;
  const url = new URL(`d/${encodeURIComponent(uid)}`, base.endsWith("/") ? base : `${base}/`);
  url.searchParams.set("from", from);
  url.searchParams.set("to", to);
  return url.toString();
}

// -- Time series (GET /api/platform/observability/timeseries) -----------------

export const TIMESERIES_PATH = "/api/platform/observability/timeseries";
export const TIMESERIES_RANGES = ["1h", "6h", "24h", "7d"] as const;
export type TimeseriesRange = (typeof TIMESERIES_RANGES)[number];

export interface TimeseriesSeries {
  key: string;
  label: string;
  unit: "reqps" | "ratio" | "seconds" | "count";
  group: "http" | "collection" | "scheduler" | "alerts";
  error: string | null;
  lines: { name: string; points: [number, number][] }[];
}

export interface Timeseries {
  configured: boolean;
  reachable: boolean;
  error: string | null;
  range: TimeseriesRange;
  step_seconds: number;
  checked_at: string;
  series: TimeseriesSeries[];
  /** "prometheus" = every scraped process; "process" = this backend's own in-memory history. */
  source?: "prometheus" | "process";
  /** Seconds of history the in-memory fallback holds (source "process" only). */
  history_seconds?: number | null;
  prometheus_configured?: boolean;
  prometheus_error?: string | null;
}

/** One line saying where the charts' data came from, and its limits. */
export function timeseriesSourceNote(t: Pick<Timeseries, "source" | "history_seconds" | "prometheus_configured">): string {
  if (t.source !== "process") return "From Prometheus, every scraped process";
  const why = t.prometheus_configured ? "Prometheus is not answering" : "Prometheus is not configured";
  const held = t.history_seconds == null ? "no samples yet" : `${formatUptime(t.history_seconds)} of history`;
  return `${why}: showing this backend process's own in-memory history (${held}, at most 24 h, reset on restart)`;
}

/** Latest value of a series line, or null when it has no points. */
export function latestPoint(series: TimeseriesSeries | undefined, line = "total"): number | null {
  const points = series?.lines.find((l) => l.name === line)?.points;
  return points && points.length ? points[points.length - 1][1] : null;
}

/** Axis / legend formatter for a series unit. */
export function formatUnit(unit: TimeseriesSeries["unit"], v: number): string {
  if (!Number.isFinite(v)) return NOT_MEASURED;
  if (unit === "seconds") return formatSeconds(v);
  if (unit === "ratio") return formatRatio(v, v !== 0 && Math.abs(v) < 0.01 ? 2 : 1);
  if (unit === "reqps") return `${v < 10 ? v.toFixed(2) : v.toFixed(0)}/s`;
  return v < 10 && !Number.isInteger(v) ? v.toFixed(2) : Math.round(v).toLocaleString("en-US");
}

/** Dashed reference lines, matching the alert rules. */
export const SERIES_THRESHOLDS: Record<string, { value: number; label: string; color: string }[]> = {
  http_error_ratio: [{ value: 0.05, label: "alert 5%", color: "var(--ecc-bad)" }],
  http_latency: [{ value: 2.5, label: "alert p95 2.5 s", color: "var(--ecc-bad)" }],
  collection_success_ratio: [{ value: THRESHOLDS.collectionSuccessWarn, label: "95%", color: "var(--ecc-warn)" }],
  scheduler_tick_age: [{ value: 300, label: "alert 5 min", color: "var(--ecc-bad)" }],
  db_pool_saturation: [{ value: 0.9, label: "alert 90%", color: "var(--ecc-bad)" }],
};
