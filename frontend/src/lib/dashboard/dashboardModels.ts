/**
 * dashboardModels.ts — SEMANTIC dashboard data models.
 *
 * These types describe WHAT the dashboard shows, never WHERE the data comes
 * from. Every field is populated by `dashboardDataAdapter.ts` from an EXISTING
 * backend API response (see `lib/api.ts`). No field may ever be hardcoded or
 * fabricated by the UI; unavailable data is `null` and the UI renders an
 * honest empty state.
 */

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
  devicesOffline: OperationalHealthKpi;
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
  activeTasks: number;
  queuedTasks: number;
  failedTasks: number;
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
  summary: {
    sites: number;
    devices: number;
    monitoringSources: number;
    cloudResources: number;
    sensors: number;
    regions: number;
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

export interface DashboardData {
  header: DashboardHeaderData;
  operationalHealth: OperationalHealthData;
  alertSummary: AlertSummaryData;
  aiOperations: AiOperationsData;
  infrastructure: InfrastructureOverviewData;
  trends: TrendChart[];
  activity: ActivityItem[];
  /** Per-section lifecycle so the UI can render honest states. */
  states: {
    operationalHealth: SectionState;
    alertSummary: SectionState;
    aiOperations: SectionState;
    infrastructure: SectionState;
    trends: SectionState;
    activity: SectionState;
  };
}
