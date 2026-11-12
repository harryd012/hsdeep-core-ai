/**
 * Integration Center API (observability plan P12 slice 1): every monitoring
 * source and integration with one derived state, and a source's sync history.
 * Read-only; syncing stays on syncMonitoringSource (monitoring.update).
 */
import { apiFetch } from "@/lib/api";
import type { Tone } from "@/components/ui/PageKit";

export type IntegrationState = "failing" | "degraded" | "stale" | "never_synced" | "disabled" | "healthy";

export interface IntegrationRow {
  /** source: monitoring source · integration: generic integration · itsm: ServiceNow / ITSM connection (slice 2). */
  kind: "source" | "integration" | "itsm";
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
  /** ITSM only: sync jobs pending, processing or waiting to be retried. */
  open_jobs?: number;
  /** ITSM only: sync jobs of the last 24 hours that went to the dead-letter queue. */
  dead_letter_24h?: number;
}

export interface IntegrationCenterOverview {
  generated_at: string;
  summary: Record<IntegrationState, number> & {
    total: number;
    sources: number;
    integrations: number;
    itsm: number;
    runs_24h: number;
    failed_runs_24h: number;
    itsm_jobs_24h: number;
    itsm_failed_jobs_24h: number;
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
  /** Whether run-level events (a run that changed no health state) are included. */
  include_run_events: boolean;
  /** Run-level events left out of health_events (0 when they are included). */
  hidden_run_events: number;
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

export function getIntegrationSource(sourceId: string, includeRunEvents = false): Promise<IntegrationSourceDetail> {
  const qs = includeRunEvents ? "?include_run_events=true" : "";
  return apiFetch<IntegrationSourceDetail>(`/api/integration-center/sources/${encodeURIComponent(sourceId)}${qs}`);
}

export interface ITSMSyncJobRow {
  id: string;
  job_type: string;
  status: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  next_attempt_at: string | null;
  retry_count: number;
  max_retries: number;
  duration_ms: number | null;
  error: string | null;
}

export interface ITSMConnectionDetail {
  connection: IntegrationRow;
  jobs: ITSMSyncJobRow[];
}

export function getIntegrationITSM(connectionId: string): Promise<ITSMConnectionDetail> {
  return apiFetch<ITSMConnectionDetail>(`/api/integration-center/itsm/${encodeURIComponent(connectionId)}`);
}

/** Detail page of a row, or null when it has none (generic integrations). */
export function integrationHref(r: Pick<IntegrationRow, "kind" | "id">): string | null {
  if (r.kind === "source") return `/monitoring/integrations/${r.id}`;
  if (r.kind === "itsm") return `/monitoring/integrations/itsm/${r.id}`;
  return null;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 60_000)} min`;
}
