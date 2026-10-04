/**
 * Command-centre KPI layer (PHASE 1).
 *
 * One normalized view model for the primary dashboard KPI strip. Values arrive
 * from the EXISTING dashboard aggregation (a single `useDashboardData` pass) -
 * no component polls anything, and no number is derived here that the adapter or
 * the backend already derived.
 *
 * NAVIGATION (PHASE 2): every `href` points at an EXISTING route, and where the
 * target already understands a filter the card carries it - /incidents reads
 * `?severity=` via useSearchParams, so the critical and high-priority cards link
 * to `?severity=critical` / `?severity=warning`. No new filtering mechanism is
 * introduced and no duplicate incidents page is created.
 *
 * TRUTH RULES (non-negotiable):
 *   value === null  -> the source did not answer / is unknown -> the UI shows N/A
 *   value === 0     -> a MEASURED zero -> the UI shows 0
 * The two are never conflated, and a KPI is never invented so the strip looks
 * complete.
 *
 * State vocabulary (subset of the platform truth model):
 *   healthy      - measured, nothing needs attention
 *   degraded     - measured, worth watching
 *   critical     - measured, needs attention now
 *   unknown      - the source answered but carries no verdict (e.g. an empty
 *                  estate: zero devices is not "everything is down")
 *   unavailable  - the source could not be read
 *
 * Thresholds below are documented product policy, not universal truth.
 */
import type { Alert, Device, Incident } from "@/lib/api";
import type { OperationalHealthData } from "./dashboardModels";

export type CommandKpiState = "healthy" | "degraded" | "critical" | "unknown" | "unavailable";

export interface CommandKpi {
  key: string;
  label: string;
  /** null = unavailable/unknown (renders N/A); 0 = a measured zero. */
  value: number | null;
  /** "%" for rate KPIs, null for counts. */
  unit: string | null;
  state: CommandKpiState;
  /** What was counted, or exactly why no value is available. */
  context: string;
  href: string;
}

export interface CommandKpiInput {
  /** Authoritative device/sensor/collector/site derivations (existing adapter). */
  operationalHealth: OperationalHealthData;
  incidents?: Incident[] | undefined;
  alerts?: Alert[] | undefined;
  devices?: Device[] | undefined;
  /** AI task counters (null = the metrics payload was unavailable). */
  aiTasks?:
    | { active: number | null; queued: number | null; failed: number | null }
    | undefined;
  available: {
    incidents: boolean;
    alerts: boolean;
    devices: boolean;
    ai?: boolean;
  };
  /** Sensors currently registered (for the empty-estate distinction). */
  sensorTotal?: number | null;
  /** Collection runs in the last 24h: 0 means "no data", not "all failed". */
  collectorRunsLast24h?: number | null;
}

/** Incident statuses that no longer need an operator. */
const CLOSED_INCIDENT_STATUS = new Set(["resolved", "closed", "archived", "recovered"]);

/**
 * Device statuses that mean "not healthy" (collector vocabulary). Deliberately a
 * fixed list: a status we do not recognise - including the backend default
 * "unknown" - is NOT counted as a problem.
 */
const DEGRADED_DEVICE_STATUS = new Set([
  "degraded", "warning", "down", "offline", "critical", "error", "unreachable",
]);

/** Collector success thresholds (same convention as the existing health adapter). */
const COLLECTOR_SUCCESS_HEALTHY = 90;
const COLLECTOR_SUCCESS_DEGRADED = 70;

/** Severity vocabulary of the incident API: critical | warning | info. */
const SEVERITY_CRITICAL = "critical";
const SEVERITY_HIGH = "warning";

function severityOf(incident: Incident): string {
  return (incident.severity || "").toLowerCase();
}

function isOpenIncident(incident: Incident): boolean {
  return !CLOSED_INCIDENT_STATUS.has((incident.status || "").toLowerCase());
}

/** A "count of problems": a measured 0 means nothing needs attention. */
function problemState(value: number | null, critical = true): CommandKpiState {
  if (value === null) return "unavailable";
  if (value === 0) return "healthy";
  return critical ? "critical" : "degraded";
}

/** A rate against the documented thresholds. */
function rateState(value: number | null): CommandKpiState {
  if (value === null) return "unavailable";
  if (value >= COLLECTOR_SUCCESS_HEALTHY) return "healthy";
  return value >= COLLECTOR_SUCCESS_DEGRADED ? "degraded" : "critical";
}

/**
 * Estate KPIs: an empty estate is not an outage.
 *   null              -> unavailable
 *   0 with no members -> unknown (nothing could be online)
 *   0 with members    -> critical / degraded
 */
function positiveState(value: number | null, total: number | null): CommandKpiState {
  if (value === null) return "unavailable";
  if (total !== null && total === 0) return "unknown";
  return value > 0 ? "healthy" : "critical";
}

function negativeState(value: number | null, total: number | null): CommandKpiState {
  if (value === null) return "unavailable";
  if (total !== null && total === 0) return "unknown";
  return value > 0 ? "degraded" : "healthy";
}

export function buildCommandKpis(input: CommandKpiInput): CommandKpi[] {
  const { operationalHealth, incidents, alerts, devices, aiTasks, available } = input;

  const openIncidents = (incidents ?? []).filter(isOpenIncident);
  const criticalIncidents = available.incidents
    ? openIncidents.filter((i) => severityOf(i) === SEVERITY_CRITICAL).length
    : null;
  const highPriority = available.incidents
    ? openIncidents.filter((i) => severityOf(i) === SEVERITY_HIGH).length
    : null;
  const openCount = available.incidents ? openIncidents.length : null;

  const unacknowledged = available.alerts
    ? (alerts ?? []).filter(
        (a) => (a.status || "").toLowerCase() === "open" && a.acknowledged_at === null,
      ).length
    : null;

  const deviceList = devices ?? [];
  const deviceTotal = available.devices ? deviceList.length : null;
  const degradedSystems = available.devices
    ? deviceList.filter((d) => DEGRADED_DEVICE_STATUS.has((d.status || "").toLowerCase())).length
    : null;
  const unclassifiedDevices = available.devices
    ? deviceList.filter((d) => ["", "unknown"].includes((d.status || "").toLowerCase())).length
    : null;

  const devicesOnline = operationalHealth.devicesOnline.value;
  const devicesOffline = operationalHealth.devicesOffline.value;
  const sensorsHealthy = operationalHealth.sensorsHealthy.value;
  const collectorSuccess = operationalHealth.collectorSuccess.value;
  const sensorTotal = input.sensorTotal ?? null;
  const collectorRuns = input.collectorRunsLast24h ?? null;

  const aiActive = aiTasks?.active ?? null;
  const aiQueued = aiTasks?.queued ?? null;
  const aiFailed = aiTasks?.failed ?? null;

  const notAnswered = "source did not answer";

  const attentionKpis: CommandKpi[] = [
    {
      key: "criticalIncidents",
      label: "CRITICAL INCIDENTS",
      value: criticalIncidents,
      unit: null,
      state: problemState(criticalIncidents),
      context: available.incidents
        ? `${openCount ?? 0} open incident(s) in total - opens the queue filtered to critical`
        : `incident API — ${notAnswered}`,
      // PHASE 2: /incidents reads `?severity=` (useSearchParams), so the card
      // carries the operator's context into the queue instead of dropping it.
      href: "/incidents?severity=critical",
    },
    {
      key: "highPriority",
      label: "HIGH PRIORITY",
      value: highPriority,
      unit: null,
      state: problemState(highPriority, false),
      context: available.incidents
        ? "open incidents at warning severity - opens the queue filtered to warning"
        : `incident API — ${notAnswered}`,
      // "High priority" is the warning-severity band of the incident API, so the
      // filter uses that same vocabulary rather than inventing a new one.
      href: "/incidents?severity=warning",
    },
    {
      key: "degradedSystems",
      label: "DEGRADED SYSTEMS",
      value: degradedSystems,
      unit: null,
      state: problemState(degradedSystems),
      context: available.devices
        ? `${deviceTotal ?? 0} device(s) monitored` +
          (unclassifiedDevices ? ` · ${unclassifiedDevices} with no reported status` : "")
        : `device API — ${notAnswered}`,
      href: "/infrastructure",
    },
    {
      key: "unacknowledgedAlerts",
      label: "UNACKNOWLEDGED ALERTS",
      value: unacknowledged,
      unit: null,
      state: problemState(unacknowledged, false),
      context: available.alerts ? "raised, not acknowledged yet" : `alert API — ${notAnswered}`,
      href: "/alerts",
    },
    {
      key: "openIncidents",
      label: "OPEN INCIDENTS",
      value: openCount,
      unit: null,
      state: problemState(openCount, false),
      context: available.incidents
        ? "not resolved, closed or archived"
        : `incident API — ${notAnswered}`,
      href: "/incidents",
    },
  ];

  const estateKpis: CommandKpi[] = [
    {
      key: "devicesOnline",
      label: "DEVICES ONLINE",
      value: devicesOnline,
      unit: null,
      state: positiveState(devicesOnline, deviceTotal),
      context:
        operationalHealth.devicesOnline.context ??
        (available.devices ? `of ${deviceTotal ?? 0} monitored` : `device API — ${notAnswered}`),
      href: operationalHealth.devicesOnline.href,
    },
    {
      key: "devicesOffline",
      label: "DEVICES OFFLINE",
      value: devicesOffline,
      unit: null,
      state: negativeState(devicesOffline, deviceTotal),
      context:
        operationalHealth.devicesOffline.context ??
        (available.devices ? `of ${deviceTotal ?? 0} monitored` : `device API — ${notAnswered}`),
      href: operationalHealth.devicesOffline.href,
    },
    {
      key: "sensorsHealthy",
      label: "SENSORS HEALTHY",
      value: sensorsHealthy,
      unit: null,
      state: positiveState(sensorsHealthy, sensorTotal),
      context:
        operationalHealth.sensorsHealthy.context ??
        (sensorTotal === null ? "sensor total not reported" : `of ${sensorTotal} monitored`),
      href: operationalHealth.sensorsHealthy.href,
    },
    {
      key: "collectorSuccess",
      label: "COLLECTOR SUCCESS",
      value: collectorSuccess,
      unit: "%",
      state:
        collectorSuccess === null
          ? "unavailable"
          : collectorRuns === 0
            ? "unknown" // no collection activity in the window: nothing to rate
            : rateState(collectorSuccess),
      context:
        collectorRuns === 0
          ? "no collection runs in the last 24h"
          : (operationalHealth.collectorSuccess.context ??
            `24h success rate (healthy ${COLLECTOR_SUCCESS_HEALTHY}%+, degraded ${COLLECTOR_SUCCESS_DEGRADED}%+)`),
      href: operationalHealth.collectorSuccess.href,
    },
    {
      key: "aiTasks",
      label: "AI TASKS",
      value: aiActive,
      unit: null,
      state:
        aiActive === null
          ? "unavailable"
          : aiFailed !== null && aiFailed > 0
            ? "degraded"
            : "healthy",
      context:
        aiActive === null
          ? `AI metrics — ${notAnswered}`
          : `${aiQueued === null ? "N/A" : aiQueued} queued · ${aiFailed === null ? "N/A" : aiFailed} failed`,
      href: "/ai-copilot",
    },
  ];

  return [...attentionKpis, ...estateKpis];
}
