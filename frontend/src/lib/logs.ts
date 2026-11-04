/**
 * Logs Explorer API (observability plan P7 slice 1): log lines stored as
 * events (syslog through the receiver, or any collector), searched within a
 * window of at most 7 days.
 */
import { apiFetch } from "@/lib/api";

export type LogSeverity = "critical" | "warning" | "info";

export interface LogLine {
  id: string;
  timestamp: string;
  severity: LogSeverity;
  event_type: string;
  message: string;
  host: string | null;
  app: string | null;
  facility: string | null;
  syslog_severity: string | null;
  device_id: string | null;
  device_name: string | null;
  attributes: Record<string, unknown>;
}

export interface LogFilters {
  since: string;
  until: string;
  q?: string;
  severity?: LogSeverity[];
  host?: string | null;
  app?: string | null;
  eventType?: string | null;
  deviceId?: string | null;
}

export interface LogStats {
  since: string;
  until: string;
  bucket_seconds: number;
  histogram: { t: string; critical: number; warning: number; info: number }[];
  facets: Record<"severity" | "host" | "app" | "event_type", { value: string; count: number }[]>;
}

function params(f: LogFilters): URLSearchParams {
  const qs = new URLSearchParams({ since: f.since, until: f.until });
  if (f.q?.trim()) qs.set("q", f.q.trim());
  for (const s of f.severity ?? []) qs.append("severity", s);
  if (f.host) qs.set("host", f.host);
  if (f.app) qs.set("app", f.app);
  if (f.eventType) qs.set("event_type", f.eventType);
  if (f.deviceId) qs.set("device_id", f.deviceId);
  return qs;
}

export function searchLogs(f: LogFilters, limit = 100, offset = 0): Promise<{ since: string; until: string; items: LogLine[]; total: number }> {
  const qs = params(f);
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  return apiFetch(`/api/logs?${qs.toString()}`);
}

export function getLogStats(f: LogFilters): Promise<LogStats> {
  return apiFetch<LogStats>(`/api/logs/stats?${params(f).toString()}`);
}
