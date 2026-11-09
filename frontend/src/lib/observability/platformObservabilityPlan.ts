/**
 * platformObservabilityPlan.ts - the Prometheus + Grafana platform-observability
 * audit and plan, as shown on /observability.
 *
 * Source of truth: docs/design/PROMETHEUS_GRAFANA_IMPLEMENTATION_AUDIT.md. This
 * module is documentation, not telemetry: it never reports a process as healthy
 * and carries no measured values. Keep the two in step when a phase changes.
 *
 * Scope: Prometheus and Grafana monitor the HSDEEP platform itself (API, worker
 * and scheduler processes, pipelines). Customer infrastructure stays on the
 * collectors -> PostgreSQL -> Command Center path.
 */

/** The five status labels the brief requires, nothing else. */
export type DeliveryStatus =
  | "implemented-tested"
  | "implemented-not-live"
  | "partial"
  | "not-implemented"
  | "blocked";

export const DELIVERY_STATUS_LABEL: Record<DeliveryStatus, string> = {
  "implemented-tested": "Implemented and tested",
  "implemented-not-live": "Implemented, not live-verified",
  partial: "Partially implemented",
  "not-implemented": "Not implemented",
  blocked: "Blocked",
};

export const DELIVERY_STATUS_TONE: Record<DeliveryStatus, "good" | "info" | "warn" | "neutral" | "bad"> = {
  "implemented-tested": "good",
  "implemented-not-live": "info",
  partial: "warn",
  "not-implemented": "neutral",
  blocked: "bad",
};

export interface PlanPhase {
  id: string;
  name: string;
  work: string;
  status: DeliveryStatus;
  /** Why this status, in one line (what is verified, what is not). */
  note: string;
}

export const PLAN_PHASES: PlanPhase[] = [
  { id: "P0", name: "Repository audit and plan", status: "implemented-tested",
    work: "Audit of metrics, /metrics, runtime roles, orchestrator, scheduler, deployment and frontend against origin/main",
    note: "Approved 2026-11-09, with the three recommended decisions" },
  { id: "P1", name: "HTTP instrumentation", status: "implemented-not-live",
    work: "hsdeep_http_* request, duration, in-progress and error metrics by route template; build info with runtime role; /metrics skips the rate limiter",
    note: "Covered by backend tests; not yet scraped by a running Prometheus" },
  { id: "P2", name: "Pipeline wiring", status: "partial",
    work: "Collection phases, outcomes and failure stages; scheduler tick depth, freshness, duration and failures; alerts opened by severity; DB pool and Redis gauges; ServiceNow relabel",
    note: "Collection, scheduler and alert wiring tested; DB pool / Redis gauges and the ServiceNow relabel remain" },
  { id: "P3", name: "Prometheus deployment", status: "not-implemented",
    work: "deployment/observability Compose with pinned images, volumes, retention, credentials_file auth; separate api / worker / scheduler jobs; K8s scrape NetworkPolicy",
    note: "Planned" },
  { id: "P4", name: "Grafana dashboards", status: "not-implemented",
    work: "Provisioned datasource; Executive Platform Health, Collector Operations, API Performance, Scheduler & Worker Health, Alert Pipeline",
    note: "Panels will query only metrics that exist" },
  { id: "P5", name: "Alert rules", status: "not-implemented",
    work: "Target down per role, collector failure ratio and latency, stale scheduler tick, API 5xx ratio and p95",
    note: "Notifications stay unconfigured until a receiver is set up and tested" },
  { id: "P6", name: "Next.js page", status: "implemented-not-live",
    work: "This page: live RED and pipeline figures for this process (superuser summary API), plan, catalogue, references, Grafana link",
    note: "Live section reads the in-process registry; cross-process history waits for Grafana (P3-P4)" },
  { id: "P7", name: "Security and regression tests", status: "not-implemented",
    work: "promtool config and rule checks, dashboard JSON lint, compose validation, bundle check for tokens, full suites",
    note: "Planned" },
  { id: "P8", name: "Documentation", status: "partial",
    work: "Metric catalogue, alert runbooks, production steps, final report",
    note: "Audit document written; runbooks wait for P5" },
];

export interface Finding {
  title: string;
  detail: string;
  severity: "high" | "medium" | "low";
}

export const AUDIT_FINDINGS: Finding[] = [
  { severity: "high", title: "No hsdeep_* metric was emitted",
    detail: "app/core/metrics.py had no importers and its observe_* helpers no callers, so /metrics only served Python process metrics. Fixed: Phase 1 loads the module and Phase 2 calls the helpers from the orchestrator, scheduler and alert engine." },
  { severity: "high", title: "No HTTP request metrics existed",
    detail: "Added in Phase 1, labelled by route template (e.g. /api/incidents/{incident_id}) and never by raw path, tenant, user or IP." },
  { severity: "medium", title: "ServiceNow metrics carry tenant identifiers",
    detail: "Most are labelled by connection_id, a per-tenant UUID. The module stays unloaded until it is relabelled in Phase 2." },
  { severity: "medium", title: "Each scrape hit the API-key lookup",
    detail: "The metrics token looked like a possible API key to the rate limiter. Fixed in Phase 1: /metrics skips the limiter and quota path." },
  { severity: "low", title: "Metrics token is not required at startup",
    detail: "Safe as is: /metrics answers 404 in production when METRICS_BEARER_TOKEN is unset." },
];

export interface CatalogueMetric {
  name: string;
  type: "counter" | "histogram" | "gauge";
  labels: string;
  /** "emitted": recorded today. "declared": registered but nothing records it yet. */
  state: "emitted" | "declared";
}

export const METRIC_CATALOGUE: CatalogueMetric[] = [
  { name: "hsdeep_http_requests_total", type: "counter", labels: "method, route, status", state: "emitted" },
  { name: "hsdeep_http_request_duration_seconds", type: "histogram", labels: "method, route", state: "emitted" },
  { name: "hsdeep_http_requests_in_progress", type: "gauge", labels: "method", state: "emitted" },
  { name: "hsdeep_http_errors_total", type: "counter", labels: "method, route, status", state: "emitted" },
  { name: "hsdeep_build_info", type: "gauge", labels: "version, runtime_mode", state: "emitted" },
  { name: "hsdeep_collection_run_total", type: "counter", labels: "source_type, status", state: "emitted" },
  { name: "hsdeep_collection_run_duration_seconds", type: "histogram", labels: "source_type, status", state: "emitted" },
  { name: "hsdeep_collection_items_upserted_total", type: "counter", labels: "entity_type, source_type", state: "emitted" },
  { name: "hsdeep_collector_execution_total", type: "counter", labels: "source_type, phase", state: "emitted" },
  { name: "hsdeep_collector_phase_duration_seconds", type: "histogram", labels: "source_type, phase", state: "emitted" },
  { name: "hsdeep_collection_skipped_total", type: "counter", labels: "source_type", state: "emitted" },
  { name: "hsdeep_scheduler_queue_depth", type: "gauge", labels: "-", state: "emitted" },
  { name: "hsdeep_scheduler_last_tick_timestamp_seconds", type: "gauge", labels: "-", state: "emitted" },
  { name: "hsdeep_alert_created_total", type: "counter", labels: "source, severity", state: "emitted" },
  { name: "hsdeep_collection_failures_total", type: "counter", labels: "source_type, stage", state: "emitted" },
  { name: "hsdeep_scheduler_tick_duration_seconds", type: "histogram", labels: "-", state: "emitted" },
  { name: "hsdeep_scheduler_tick_failures_total", type: "counter", labels: "-", state: "emitted" },
];

export const APPROVED_DECISIONS: string[] = [
  "Paths that match no route share one __unmatched__ label, so scanners cannot create new series.",
  "This page is for platform superusers only: it describes platform internals, not tenant data.",
  "Docker Compose and the scrape NetworkPolicy now; Kubernetes manifests for Prometheus and Grafana later.",
];

export interface Reference {
  title: string;
  url: string;
  why: string;
  group: "Prometheus" | "Grafana" | "Method";
}

/** Official documentation only (prometheus.io, its client library, grafana.com, sre.google). */
export const REFERENCES: Reference[] = [
  { group: "Prometheus", title: "Metric and label naming", url: "https://prometheus.io/docs/practices/naming/",
    why: "Base units, _total suffix, labels not baked into names" },
  { group: "Prometheus", title: "Instrumentation best practices", url: "https://prometheus.io/docs/practices/instrumentation/",
    why: "Keep label cardinality low; never user IDs or emails as labels" },
  { group: "Prometheus", title: "Configuration: scrape authorization", url: "https://prometheus.io/docs/prometheus/latest/configuration/configuration/",
    why: "authorization.credentials_file reads the scrape token from a mounted secret" },
  { group: "Prometheus", title: "Alerting rules", url: "https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/",
    why: "for, keep_firing_for, labels and annotations" },
  { group: "Prometheus", title: "promtool", url: "https://prometheus.io/docs/prometheus/latest/command-line/promtool/",
    why: "check config and check rules in validation" },
  { group: "Prometheus", title: "Unit testing for rules", url: "https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/",
    why: "promtool test rules for the alert thresholds" },
  { group: "Prometheus", title: "Python client: multiprocess mode", url: "https://prometheus.github.io/client_python/multiprocess/",
    why: "Why one uvicorn process per pod needs no multiprocess mode" },
  { group: "Grafana", title: "Provision Grafana", url: "https://grafana.com/docs/grafana/latest/administration/provisioning/",
    why: "Datasources and dashboards from files" },
  { group: "Grafana", title: "Configure a Grafana Docker image", url: "https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/",
    why: "GF_SECURITY_ADMIN_PASSWORD__FILE for the admin secret" },
  { group: "Grafana", title: "Configure authentication", url: "https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/",
    why: "Anonymous access stays off" },
  { group: "Grafana", title: "Dashboard best practices", url: "https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/",
    why: "RED for services, USE for resources, drill-down hierarchy" },
  { group: "Method", title: "The RED Method", url: "https://grafana.com/blog/the-red-method-how-to-instrument-your-services/",
    why: "Rate, errors, duration per service: the API dashboard layout" },
  { group: "Method", title: "Google SRE: Monitoring Distributed Systems", url: "https://sre.google/sre-book/monitoring-distributed-systems/",
    why: "The four golden signals: latency, traffic, errors, saturation" },
];

/**
 * The Grafana link from NEXT_PUBLIC_GRAFANA_URL, or null when unset or not an
 * http(s) URL. Only a link is ever public: no token or password belongs here.
 */
export function grafanaUrl(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function phaseCounts(phases: PlanPhase[] = PLAN_PHASES): Record<DeliveryStatus, number> {
  const counts: Record<DeliveryStatus, number> = {
    "implemented-tested": 0, "implemented-not-live": 0, partial: 0, "not-implemented": 0, blocked: 0,
  };
  for (const p of phases) counts[p.status] += 1;
  return counts;
}
