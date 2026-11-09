/**
 * Traces API (observability plan P8 slice 1): OpenTelemetry spans received
 * over OTLP/HTTP, queried per service, per trace and as a service map, within
 * a window of at most 7 days.
 */
import { apiFetch } from "@/lib/api";

export interface ServiceStats {
  service: string;
  spans: number;
  error_spans: number;
  traces: number;
  entry_spans: number;
  error_rate_pct: number;
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
  last_seen: string;
}

export interface TraceSummary {
  trace_id: string;
  start: string;
  duration_ms: number;
  spans: number;
  errors: number;
  services: string[];
  root_service: string | null;
  root_name: string | null;
  root_complete: boolean;
}

export interface ServiceEdge {
  source: string;
  target: string;
  calls: number;
  errors: number;
  avg_ms: number;
  p95_ms: number | null;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  service_name: string;
  name: string;
  kind: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status_code: string;
  status_message: string | null;
  attributes: Record<string, unknown>;
  resource_attributes: Record<string, unknown>;
  events: { name: string; time: string; attributes: Record<string, unknown> }[];
}

export interface TraceDetail {
  trace_id: string;
  spans: Span[];
  orphans: number;
  truncated: boolean;
}

export interface Window {
  since: string;
  until: string;
}

function qs(w: Window, extra: Record<string, string | number | boolean | null | undefined> = {}): string {
  const p = new URLSearchParams({ since: w.since, until: w.until });
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== "" && v !== false) p.set(k, String(v));
  return p.toString();
}

export function getTraceServices(w: Window): Promise<ServiceStats[]> {
  return apiFetch<ServiceStats[]>(`/api/traces/services?${qs(w)}`);
}

export function getServiceMap(w: Window): Promise<Window & { services: ServiceStats[]; edges: ServiceEdge[] }> {
  return apiFetch(`/api/traces/service-map?${qs(w)}`);
}

export function listTraces(
  w: Window,
  f: { service?: string | null; errorsOnly?: boolean; minDurationMs?: number | null; q?: string; limit?: number; offset?: number },
): Promise<Window & { items: TraceSummary[]; total: number }> {
  return apiFetch(`/api/traces?${qs(w, {
    service: f.service, errors_only: f.errorsOnly, min_duration_ms: f.minDurationMs, q: f.q?.trim(),
    limit: f.limit ?? 50, offset: f.offset ?? 0,
  })}`);
}

export function getTrace(traceId: string): Promise<TraceDetail> {
  return apiFetch<TraceDetail>(`/api/traces/${encodeURIComponent(traceId)}`);
}

export function formatMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1000) return `${(v / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 })} s`;
  return `${v.toLocaleString("en-US", { maximumFractionDigits: v < 10 ? 2 : 1 })} ms`;
}
