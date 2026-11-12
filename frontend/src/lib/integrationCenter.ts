/**
 * Integration Center API (observability plan P12 slice 1): every monitoring
 * source and integration with one derived state, and a source's sync history.
 * Read-only; syncing stays on syncMonitoringSource (monitoring.update).
 */
import { apiFetch } from "@/lib/api";
import type { Tone } from "@/components/ui/PageKit";

export type IntegrationState = "failing" | "degraded" | "stale" | "never_synced" | "disabled" | "healthy";

export interface IntegrationRow {
  kind: "source" | "integration";
  id: string;
  name: string;
  type: string;
  vendor: string | null;
  endpoint: string | null;
  state: IntegrationState;
  status: string;
  health_status: string | null;
  enabled: boolean;
  credential_configured: boolean;
  poll_interval_seconds: number | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_attempt_at: string | null;
  last_run_status: string | null;
  next_run_at: string | null;
  consecutive_failures: number | null;
  last_error: string | null;
  sensors: number | null;
  devices: number | null;
  runs_24h: number | null;
  failed_runs_24h: number | null;
  success_rate_24h: number | null;
  avg_run_ms: number | null;
  health_score?: number;
}

export interface IntegrationCenterOverview {
  generated_at: string;
  summary: Record<IntegrationState, number> & {
    total: number;
    sources: number;
    integrations: number;
    runs_24h: number;
    failed_runs_24h: number;
  };
  items: IntegrationRow[];
}

export interface CollectorRunRow {
  id: string;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  devices: number;
  interfaces: number;
  sensors: number;
  metrics: number;
  events: number;
  alerts: number;
  error: string | null;
}

export interface HealthEventRow {
  id: string;
  event_type: string;
  created_at: string;
  from_health_status: string | null;
  to_health_status: string | null;
  from_status: string | null;
  to_status: string | null;
  consecutive_failures: number | null;
  reason: string | null;
}

export interface IntegrationSourceDetail {
  source: IntegrationRow;
  runs: CollectorRunRow[];
  health_events: HealthEventRow[];
}

export const STATE_LABEL: Record<IntegrationState, string> = {
  failing: "Failing",
  degraded: "Degraded",
  stale: "Stale",
  never_synced: "Never synced",
  disabled: "Disabled",
  healthy: "Healthy",
};

export const STATE_TONE: Record<IntegrationState, Tone> = {
  failing: "bad",
  degraded: "warn",
  stale: "warn",
  never_synced: "neutral",
  disabled: "neutral",
  healthy: "good",
};

export function getIntegrationCenter(): Promise<IntegrationCenterOverview> {
  return apiFetch<IntegrationCenterOverview>("/api/integration-center");
}

export function getIntegrationSource(sourceId: string): Promise<IntegrationSourceDetail> {
  return apiFetch<IntegrationSourceDetail>(`/api/integration-center/sources/${encodeURIComponent(sourceId)}`);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 60_000)} min`;
}
