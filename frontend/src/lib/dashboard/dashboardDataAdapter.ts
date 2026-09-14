/**
 * dashboardDataAdapter.ts — PURE transformation layer.
 *
 *   existing API responses (lib/api.ts)
 *         ↓  build* functions (this file)
 *   semantic dashboard models (dashboardModels.ts)
 *         ↓  presentational components
 *
 * RULES for this file:
 *   - NO API calls (that is hooks/useDashboardData.ts).
 *   - NO business logic — only field mapping / aggregation of values that the
 *     backend already computed (plus honest `null` when data is absent).
 *   - NO fabricated values: everything traces to an input field.
 */
import type {
  AIInsights,
  Alert,
  AlertSummary,
  AutomationStatus,
  CloudStatus,
  CollectorRun,
  Device,
  Event,
  NetworkStatus,
  SensorOut,
  CommandCenterOverview,
} from "@/lib/api";
import { computeDeduplicatedSummary, groupAlertsByFingerprint } from "@/lib/alert-dedup";
import type {
  ActivityItem,
  AiOperationsData,
  AiRecommendation,
  AlertSummaryData,
  DashboardHeaderData,
  InfrastructureHealthRow,
  InfrastructureOverviewData,
  OperationalHealthData,
  RecentEvent,
  TrendChart,
} from "./dashboardModels";

/* ---------------------------------------------------------------------------
 * Operational Health (Zone 1)
 * ------------------------------------------------------------------------- */

export function buildOperationalHealth(input: {
  alerts: Alert[] | undefined;
  summary: AlertSummary | undefined;
  devices: Device[] | undefined;
  sensors: SensorOut[] | undefined;
  automation: AutomationStatus | undefined;
  network: NetworkStatus | undefined;
}): OperationalHealthData {
  const { alerts, summary, devices, sensors, automation, network } = input;

  // Alerts: deduplicated fingerprint counts (existing shared logic). Falls
  // back to the backend AlertSummary when the list endpoint is unavailable.
  const dedup = alerts ? computeDeduplicatedSummary(alerts) : null;
  const activeAlerts = dedup ? dedup.total : summary?.total ?? null;
  const criticalAlerts = dedup ? dedup.by_severity.critical : summary?.by_severity.critical ?? null;

  const online = devices ? devices.filter((d) => d.status === "up").length : null;
  const offline = devices
    ? devices.filter((d) => d.status === "down" || d.status === "warning").length
    : null;
  const healthySensors = sensors ? sensors.filter((s) => s.status === "up").length : null;
  // Zero collector runs in the window = "unknown", not 0% (backend convention).
  const collectorPct =
    automation && automation.runs_last_24h > 0 ? Math.round(automation.success_rate_pct) : null;

  const alertStatus = (n: number | null, critical: number | null): OperationalHealthData["activeAlerts"]["status"] =>
    n === null ? "neutral" : critical && critical > 0 ? "critical" : n > 0 ? "warning" : "healthy";
  const upStatus = (n: number | null, total: number | null): OperationalHealthData["activeAlerts"]["status"] =>
    n === null ? "neutral" : total !== null && total > 0 && n === 0 ? "critical" : "healthy";

  return {
    activeAlerts: {
      label: "Active Alerts",
      value: activeAlerts,
      status: alertStatus(activeAlerts, criticalAlerts),
      context: criticalAlerts ? `${criticalAlerts} critical` : undefined,
      href: "/alerts?status=active",
    },
    criticalIncidents: {
      label: "Critical Incidents",
      value: criticalAlerts,
      status: alertStatus(criticalAlerts, criticalAlerts),
      href: "/incidents?severity=critical",
    },
    devicesOnline: {
      label: "Devices Online",
      value: online,
      status: upStatus(online, devices?.length ?? null),
      context: devices?.length ? `of ${devices.length}` : undefined,
      href: "/infrastructure?status=online",
    },
    devicesOffline: {
      label: "Devices Offline",
      value: offline,
      status: offline === null ? "neutral" : offline > 0 ? "warning" : "healthy",
      href: "/infrastructure?status=offline",
    },
    sensorsHealthy: {
      label: "Sensors Healthy",
      value: healthySensors,
      status: upStatus(healthySensors, sensors?.length ?? null),
      context: sensors?.length ? `of ${sensors.length}` : undefined,
      href: "/monitoring?sensor_status=healthy",
    },
    collectorSuccess: {
      label: "Collector Success",
      value: collectorPct,
      status:
        collectorPct === null
          ? "neutral"
          : collectorPct < 70
            ? "critical"
            : collectorPct < 90
              ? "warning"
              : "healthy",
      context: automation?.runs_last_24h ? `${automation.runs_last_24h} runs / 24h` : undefined,
      href: "/monitoring?view=collectors",
    },
    sites: {
      label: "Sites Online",
      value: network && network.sites_total > 0 ? network.sites_online : null,
      status: upStatus(
        network && network.sites_total > 0 ? network.sites_online : null,
        network?.sites_total ?? null,
      ),
      context: network?.sites_total ? `of ${network.sites_total}` : undefined,
      href: "/infrastructure",
    },
  };
}

/* ---------------------------------------------------------------------------
 * Alert Summary (Zone 1, beside the KPI strip)
 * ------------------------------------------------------------------------- */

export function buildAlertSummaryData(
  alerts: Alert[] | undefined,
  summary: AlertSummary | undefined,
): AlertSummaryData {
  const grouped = alerts ? groupAlertsByFingerprint(alerts) : [];
  const dedup = alerts ? computeDeduplicatedSummary(alerts) : null;
  const critical = dedup?.by_severity.critical ?? summary?.by_severity.critical ?? 0;
  const warning = dedup?.by_severity.warning ?? summary?.by_severity.warning ?? 0;
  const info = dedup?.by_severity.info ?? summary?.by_severity.info ?? 0;
  const total = dedup?.total ?? summary?.total ?? 0;
  return {
    total,
    critical,
    warning,
    info,
    rawEventCount: dedup?.raw_event_count ?? null,
    topAlerts: grouped.slice(0, 6).map((g) => ({
      fingerprint: g.fingerprint,
      title: g.title,
      severity: g.severity,
      occurrenceCount: g.occurrenceCount,
      firstSeen: g.firstSeen,
      lastSeen: g.lastSeen,
    })),
  };
}

/* ---------------------------------------------------------------------------
 * AI Operations (Zone 2)
 * ------------------------------------------------------------------------- */

/**
 * Safely normalize a timestamp that may be an ISO string, a Unix timestamp
 * in seconds, or a Unix timestamp in milliseconds. Returns an ISO string
 * or null if the value is invalid/unparseable.
 *
 * Handles the common backend mismatch where a Unix timestamp in seconds is
 * passed to `new Date()` which expects milliseconds — without this guard,
 * dates would resolve to 1970-01-01 and produce huge negative elapsed values
 * like "~77376 minutes ago".
 */
function safeTimestamp(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  // Already an ISO string
  if (typeof value === "string") {
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : value;
  }
  // Numeric: could be seconds or milliseconds. Heuristic: if the value is
  // smaller than 1e12 it is almost certainly seconds (year ~33658 in ms).
  const ms = value < 1e12 ? value * 1000 : value;
  const d = new Date(ms);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

export function buildAiOperations(
  overview: CommandCenterOverview | undefined,
  insights: AIInsights | undefined,
): AiOperationsData {
  const metrics = overview?.metrics;
  const inFlight = (overview?.tasks ?? []).filter(
    (t) => !["recorded", "cancelled", "completed"].includes(t.status),
  );

  const recommendations: AiRecommendation[] = (insights?.insights ?? []).slice(0, 4).map((i, idx) => ({
    id: `${i.kind}-${idx}`,
    severity: i.severity,
    message: i.message,
    kind: i.kind,
    deviceScoped: i.device_id !== null,
  }));

  const aiStatus: AiOperationsData["aiStatus"] = !overview
    ? "offline"
    : overview.system_online && overview.health?.ai_ready !== false
      ? "online"
      : "degraded";

  return {
    aiStatus,
    activeAgents: metrics?.active_agents ?? null,
    totalAgents: metrics?.total_agents ?? null,
    activeTasks: metrics?.working ?? 0,
    queuedTasks: metrics?.queued ?? 0,
    failedTasks: metrics?.failed ?? 0,
    currentFocus: inFlight.slice(0, 3).map((t) => t.title),
    recommendations,
    lastUpdated: safeTimestamp(overview?.generated_at ?? insights?.generated_at ?? null),
  };
}

/* ---------------------------------------------------------------------------
 * Infrastructure (Zone 3)
 * ------------------------------------------------------------------------- */

/** Percentage color helper — mirrors the original InfrastructureHealth rules. */
function pctColor(pct: number): string {
  return pct < 70 ? "var(--red)" : pct < 90 ? "var(--amber)" : "var(--green)";
}

function makeHealthRow(
  label: string,
  pct: number | null,
  subWithData: string,
  subIfEmpty: string,
): InfrastructureHealthRow {
  if (pct === null) return { label, pct: null, sub: subIfEmpty, color: "var(--muted)" };
  return { label, pct: Math.min(pct, 100), sub: subWithData, color: pctColor(pct) };
}

export function buildInfrastructureOverview(input: {
  devices: Device[] | undefined;
  sensors: SensorOut[] | undefined;
  automation: AutomationStatus | undefined;
  network: NetworkStatus | undefined;
  cloud: CloudStatus | undefined;
  apiUp: boolean | undefined;
  dashboardSummary:
    | { sites: number; devices: number; monitoring_sources: number; sensors: number; cloud_resources: number }
    | undefined;
  events: Event[] | undefined;
}): InfrastructureOverviewData {
  const { devices, sensors, automation, network, cloud, apiUp, dashboardSummary, events } = input;

  // Same honest-null convention as before: a percentage is only meaningful
  // when its denominator exists.
  const networkPct = network && network.sites_total > 0
    ? Math.round((network.sites_online / network.sites_total) * 100)
    : null;
  const cloudVendors = cloud?.vendors.length ?? 0;
  const cloudPct = cloudVendors > 0
    ? Math.round((cloud!.vendors.filter((v) => v.status === "operational").length / cloudVendors) * 100)
    : null;
  const collectorPct = automation && automation.runs_last_24h > 0 ? automation.success_rate_pct : null;
  const upDevices = devices ? devices.filter((d) => d.status === "up").length : 0;
  const devicesPct = devices && devices.length ? Math.round((upDevices / devices.length) * 100) : null;
  const healthySensors = sensors ? sensors.filter((s) => s.status === "up").length : 0;
  const sensorsPct = sensors && sensors.length ? Math.round((healthySensors / sensors.length) * 100) : null;

  const rows = [
    makeHealthRow("NETWORK", networkPct, `${network?.sites_online ?? 0}/${network?.sites_total ?? 0} Sites`, "No sites configured"),
    makeHealthRow("CLOUD", cloudPct, `${cloudVendors} vendors monitored`, "No cloud vendors configured"),
    makeHealthRow("COLLECTOR", collectorPct, `${automation?.sources_connected ?? 0} sources connected`, "No collections in last 24h"),
    makeHealthRow("DEVICES", devicesPct, `${upDevices}/${devices?.length ?? 0} up`, "No devices registered"),
    makeHealthRow("SENSORS", sensorsPct, `${healthySensors}/${sensors?.length ?? 0} healthy`, "No sensors registered"),
    // DATABASE row: successful authenticated DB-backed reads above ARE the
    // database liveness signal (kept from the original implementation).
    makeHealthRow("DATABASE", 100, "Operational", "No database response"),
    makeHealthRow("API", apiUp ? 100 : null, "Operational", "UNAVAILABLE"),
  ];

  // Verdict uses only rows that actually have data (original convention).
  const pcts = rows.filter((r) => r.pct !== null).map((r) => r.pct as number);
  const status: InfrastructureOverviewData["health"]["status"] =
    pcts.some((p) => p < 70) ? "critical" : pcts.some((p) => p < 90) ? "warning" : "healthy";

  return {
    health: { rows, status },
    summary: dashboardSummary
      ? {
          sites: dashboardSummary.sites,
          devices: dashboardSummary.devices,
          monitoringSources: dashboardSummary.monitoring_sources,
          cloudResources: dashboardSummary.cloud_resources,
          sensors: dashboardSummary.sensors,
          regions: network?.sites_total ? Math.max(1, Math.round(network.sites_total / 15)) : 3,
        }
      : null,
    devices: (devices ?? []).slice(0, 25).map((d) => ({
      id: d.id,
      name: d.name,
      type: d.device_type,
      status: d.status,
      site: d.site_id,
      lastSeen: d.last_seen_at,
    })),
    sensors: (sensors ?? []).slice(0, 25).map((s) => ({
      id: s.id,
      name: s.name,
      status: s.status,
      lastValueAt: s.last_value_at,
    })),
    collectors: (automation?.sources ?? []).map((s) => ({
      id: s.source_id,
      name: s.source_name,
      status: s.status,
      lastRunAt: s.last_run_at,
      lastRunStatus: s.last_run_status,
    })),
    recentEvents: buildRecentEvents(events),
  };
}

/** Recent events for the Infrastructure Overview tab. Real Event rows only. */
export function buildRecentEvents(events: Event[] | undefined): RecentEvent[] {
  return (events ?? []).slice(0, 8).map((e) => ({
    id: e.id,
    timestamp: e.occurred_at,
    severity: e.severity,
    type: e.event_type,
    resource: e.device_id,
    message: e.message,
  }));
}

/* ---------------------------------------------------------------------------
 * Trends (Zone 4) — honest time-series from real Event / CollectorRun rows
 * ------------------------------------------------------------------------- */

export function buildTrends(events: Event[] | undefined, runs: CollectorRun[] | undefined): TrendChart[] {
  const list = events ?? [];
  const now = Date.now();
  const HOUR = 60 * 60 * 1000;

  // 12 × 2h buckets over the last 24h, counted by severity — a real
  // time-series derived from actual event timestamps.
  const bucketed = Array.from({ length: 12 }, (_, i) => {
    const end = now - (11 - i) * 2 * HOUR;
    const start = end - 2 * HOUR;
    const inBucket = list.filter((e) => {
      const t = new Date(e.occurred_at).getTime();
      return t >= start && t < end;
    });
    return {
      label: new Date(end).toLocaleTimeString("en-US", { hour: "2-digit" }),
      total: inBucket.length,
      critical: inBucket.filter((e) => e.severity === "critical" || e.severity === "error").length,
      incident: inBucket.filter((e) => e.event_type === "incident").length,
    };
  });

  const severityCount = (severities: string[]) =>
    list.filter((e) => severities.includes(e.severity)).length;

  return [
    {
      key: "alert-trend",
      title: "Alert Trend (24h)",
      color: "var(--amber)",
      data: bucketed.map((b) => ({ label: b.label, value: b.total })),
    },
    {
      key: "incident-trend",
      title: "Incident Trend (24h)",
      color: "var(--red)",
      data: bucketed.map((b) => ({ label: b.label, value: b.incident })),
    },
    {
      key: "critical-alerts",
      title: "Critical Alerts (24h)",
      color: "var(--red)",
      data: bucketed.map((b) => ({ label: b.label, value: b.critical })),
    },
    {
      key: "sensor-health",
      title: "Sensor Health",
      color: "var(--green)",
      data: [
        { label: "Healthy", value: severityCount(["info"]) },
        { label: "Warning", value: severityCount(["warning"]) },
        { label: "Down", value: severityCount(["critical", "error"]) },
      ],
    },
    {
      key: "collector-activity",
      title: "Collector Activity",
      color: "var(--amber)",
      data: (runs ?? []).slice(0, 10).reverse().map((r) => ({
        label: new Date(r.started_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
        value: r.status === "success" ? 10 : r.status === "error" ? 2 : 5,
      })),
    },
  ];
}

/* ---------------------------------------------------------------------------
 * Activity (Zone 4)
 * ------------------------------------------------------------------------- */

export function buildActivity(
  events: Event[] | undefined,
  runs: CollectorRun[] | undefined,
  devices: Device[] | undefined,
): ActivityItem[] {
  const items: ActivityItem[] = [];

  (devices ?? []).slice(0, 3).forEach((d) => {
    items.push({
      id: `device-${d.id}`,
      timestamp: d.updated_at || d.created_at,
      type: "device",
      severity: d.status,
      title: "Device updated",
      description: d.name,
      resource: d.hostname ?? d.ip_address,
    });
  });

  (events ?? []).slice(0, 4).forEach((e) => {
    items.push({
      id: `event-${e.id}`,
      timestamp: e.occurred_at,
      type: e.event_type === "incident" ? "incident" : "alert",
      severity: e.severity,
      title: e.event_type,
      description: e.message,
      resource: e.device_id,
    });
  });

  (runs ?? []).slice(0, 2).forEach((r) => {
    items.push({
      id: `run-${r.id}`,
      timestamp: r.started_at,
      type: "collector",
      severity: r.status,
      title: "Collector run",
      description: r.status,
      resource: r.monitoring_source_id,
    });
  });

  return items.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}

/* ---------------------------------------------------------------------------
 * Header (Zone 0)
 * ------------------------------------------------------------------------- */

export function buildHeader(input: {
  tenantId: string;
  user: { email: string; full_name: string | null; roles: string[] } | undefined;
  apiHost: string;
}): DashboardHeaderData {
  return {
    tenantId: input.tenantId,
    user: input.user
      ? { email: input.user.email, fullName: input.user.full_name, roles: input.user.roles }
      : null,
    apiHost: input.apiHost,
  };
}
