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
