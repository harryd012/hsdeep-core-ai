/**
 * dashboardModels.ts — SEMANTIC dashboard data models.
 *
 * These types describe WHAT the dashboard shows, never WHERE the data comes
 * from. Every field is populated by `dashboardDataAdapter.ts` from an EXISTING
 * backend API response (see `lib/api.ts`). No field may ever be hardcoded or
 * fabricated by the UI; unavailable data is `null` and the UI renders an
 * honest empty state.
 */
import type { CommandKpi } from "./commandKpis";
import type { SystemStatus } from "@/lib/health";

/* ---------------------------------------------------------------------------
 * Shared primitives
 * ------------------------------------------------------------------------- */

export type KpiStatus = "healthy" | "warning" | "critical" | "neutral";

/** One operational KPI tile in the Operational Health strip. */
export interface OperationalHealthKpi {
  label: string;
  /** null = the backend has no data for this metric yet (honest empty state). */
  value: number | null;
  status: KpiStatus;
  /** Short contextual suffix, e.g. "3 critical" or "of 42 devices". */
  context?: string;
  /** Deep link to the page that OWNS this metric (existing routes only). */
  href: string;
}

/** Semantic model for the Zone 1 operational-health region. */
export interface OperationalHealthData {
  activeAlerts: OperationalHealthKpi;
  criticalIncidents: OperationalHealthKpi;
  devicesOnline: OperationalHealthKpi;
  /** Confirmed down: a recent probe reported a down-ish status. */
  devicesOffline: OperationalHealthKpi;
  /** Last seen older than the freshness threshold (not confirmed down). */
  devicesStale: OperationalHealthKpi;
  /** No usable evidence to classify (never labelled down). */
  devicesUnknown: OperationalHealthKpi;
  sensorsHealthy: OperationalHealthKpi;
  collectorSuccess: OperationalHealthKpi;
  sites: OperationalHealthKpi;
}

/** Semantic model for the Alert Summary panel (beside the KPI strip). */
export interface AlertSummaryData {
  total: number;
  critical: number;
  warning: number;
  info: number;
  rawEventCount: number | null;
  topAlerts: GroupedAlertSummary[];
}

export interface GroupedAlertSummary {
  fingerprint: string;
  title: string;
  severity: string;
  occurrenceCount: number;
  firstSeen: string;
  lastSeen: string;
}

/* ---------------------------------------------------------------------------
 * AI Operations
 * ------------------------------------------------------------------------- */

/** Semantic model for Zone 2 — answers "what does AI know and what should
 *  the operator do?" Sourced ONLY from CommandCenterOverview + AIInsights. */
export interface AiOperationsData {
  aiStatus: "online" | "degraded" | "offline";
  activeAgents: number | null;
  totalAgents: number | null;
  /** null when the AI metrics payload was unavailable (never a fabricated 0). */
  activeTasks: number | null;
  queuedTasks: number | null;
  failedTasks: number | null;
  /** Most recent real task titles currently in flight (max 3). */
  currentFocus: string[];
  /** Real AI correlation insights (getAIInsights). */
  recommendations: AiRecommendation[];
  lastUpdated: string | null;
}

export interface AiRecommendation {
  id: string;
  severity: string;
  message: string;
  kind: string;
  deviceScoped: boolean;
}

/* ---------------------------------------------------------------------------
 * Infrastructure
 * ------------------------------------------------------------------------- */

/** One health percentage row (NETWORK / CLOUD / COLLECTOR / …). */
export interface InfrastructureHealthRow {
  label: string;
  pct: number | null;
  sub: string;
  color: string;
}

/** Semantic model for Zone 3 — "what is the current state of my
 *  infrastructure?" */
export interface InfrastructureOverviewData {
  health: {
    rows: InfrastructureHealthRow[];
    /** Verdict derived ONLY from rows that actually have data. */
    status: "healthy" | "warning" | "critical";
  };
  /** Estate totals as reported by the API. A field the backend did not report
   *  stays null so the UI can say "N/A" instead of inventing a 0 that is
   *  indistinguishable from a measured zero. */
  summary: {
    sites: number | null;
    devices: number | null;
    monitoringSources: number | null;
    cloudResources: number | null;
    sensors: number | null;
    regions: number | null;
  } | null;
  devices: DeviceRow[];
  sensors: SensorRow[];
  collectors: CollectorRow[];
  recentEvents: RecentEvent[];
}

export interface DeviceRow {
  id: string;
  name: string;
  type: string;
  status: string;
  site: string | null;
  lastSeen: string | null;
}

export interface SensorRow {
  id: string;
  name: string;
  status: string;
  lastValueAt: string | null;
}

export interface CollectorRow {
  id: string;
  name: string;
  status: string;
  lastRunAt: string | null;
  lastRunStatus: string | null;
}

export interface RecentEvent {
  id: string;
  timestamp: string;
  severity: string;
  type: string;
  resource: string | null;
  message: string;
}

/* ---------------------------------------------------------------------------
 * Trends + Activity
 * ------------------------------------------------------------------------- */

/** One tabbed time-series/aggregate chart in Zone 4. */
export interface TrendChart {
  key: string;
  title: string;
  color: string;
  data: { label: string; value: number }[];
}

/** One activity feed entry — real events/collector runs only. */
export interface ActivityItem {
  id: string;
  timestamp: string;
  type: "device" | "alert" | "incident" | "collector" | "system";
  severity?: string;
  title: string;
  description: string;
  resource: string | null;
}

/* ---------------------------------------------------------------------------
 * Header
 * ------------------------------------------------------------------------- */

/** Semantic model for Zone 0 header context. All fields optional because
 *  each one only renders when the backend actually provides it. */
export interface DashboardHeaderData {
  tenantId: string;
  user: {
    email: string;
    fullName: string | null;
    roles: string[];
  } | null;
  apiHost: string;
}

/* ---------------------------------------------------------------------------
 * Whole-dashboard view model (what useDashboardData returns)
 * ------------------------------------------------------------------------- */

export type SectionState = "loading" | "success" | "empty" | "error";

/* ---------------------------------------------------------------------------
 * Attention (Level 1 "what needs me?" / Level 2 "why?" / Level 3 "next?")
 * ------------------------------------------------------------------------- */

/**
 * One attention counter. `value === null` means the underlying source could not
 * be read - the UI renders "N/A" and never substitutes a 0 (a measured 0 is a
 * legitimate, different answer: "nothing needs attention").
 */
export interface AttentionMetric {
  key: "criticalIncidents" | "highPriorityIncidents" | "degradedSystems" | "unacknowledgedAlerts";
  label: string;
  value: number | null;
  /** One line explaining exactly what was counted, or why it is unavailable. */
  context: string;
  href: string;
  tone: "critical" | "warning" | "neutral";
}

export interface AttentionIncidentRow {
  id: string;
  title: string;
  severity: string;
  status: string;
  ageLabel: string;
  /** Device name when the incident is device-scoped; null otherwise. */
  asset: string | null;
  /* --- EVIDENCE / IMPACT / RCA, all from the same listIncidents payload ---- */
  /** Member alerts attached to the incident (measured impact scope). */
  memberAlerts: number;
  /** Total occurrences reported by the backend. */
  occurrences: number;
  /** Correlated incidents carry more than one member alert. */
  correlated: boolean;
  /** The recorded root-cause summary, or null when none has been recorded. */
  rca: string | null;
}

export interface AttentionDeviceRow {
  id: string;
  name: string;
  type: string;
  status: string;
}

/**
 * Authoritative aggregates the backend already computes:
 *   GET /api/incidents/intelligence      (alert correlation + noise reduction)
 *   GET /api/incidents/collector-health  (per-source collection pipeline health)
 * Every field is null when its endpoint was not readable.
 */
export interface AttentionEvidence {
  activeIncidents: number | null;
  correlatedIncidents: number | null;
  suppressedAlerts: number | null;
  noiseReductionPct: number | null;
  collectorsHealthy: number | null;
  collectorsDegraded: number | null;
  collectorsFailing: number | null;
  collectorsUnknown: number | null;
}

export interface AttentionRequiredData {
  metrics: AttentionMetric[];
  /** The open critical/high-priority incidents behind the two incident counts. */
  incidents: AttentionIncidentRow[];
  /** The devices behind DEGRADED SYSTEMS. */
  degradedDevices: AttentionDeviceRow[];
  evidence: AttentionEvidence;
  /** True when at least one source was unavailable (never a fabricated state). */
  partial: boolean;
}

export interface DashboardData {
  header: DashboardHeaderData;
  /** Authoritative platform state from GET /health ("ok" | "degraded" |
   *  "unavailable"); null while the probe has not answered. Drives the
   *  masthead status pill. */
  apiHealth: SystemStatus | null;
  /** True when the API answers; false when unreachable; null while the probe
   *  has not answered. Drives the masthead API row. */
  apiHealthy: boolean | null;
  /** Level 1-3: attention counters, the items behind them, and next actions. */
  attention: AttentionRequiredData;
  /** PHASE 1 KPI strip: normalized command-centre KPIs (one aggregation pass). */
  commandKpis: CommandKpi[];
  operationalHealth: OperationalHealthData;
  alertSummary: AlertSummaryData;
  aiOperations: AiOperationsData;
  infrastructure: InfrastructureOverviewData;
  trends: TrendChart[];
  activity: ActivityItem[];
  /** Per-section lifecycle so the UI can render honest states. */
  states: {
    attention: SectionState;
    commandKpis: SectionState;
    operationalHealth: SectionState;
    alertSummary: SectionState;
    aiOperations: SectionState;
    infrastructure: SectionState;
    trends: SectionState;
    activity: SectionState;
  };
}
