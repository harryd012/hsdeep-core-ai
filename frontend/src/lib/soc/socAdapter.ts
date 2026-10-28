/**
 * socAdapter.ts — transforms existing backend payloads (lib/api.ts types)
 * into the SOC dashboard models.
 *
 * The backend has NO SOC-specific endpoints yet (threat-trend series,
 * attack vectors, MITRE ATT&CK coverage, geo threat map, workflow stats,
 * identity/malware/blocked counters). Those categories return `source:
 * "unavailable"` with empty/null data — never fabricated values.
 * Live data comes only from existing backend APIs: alerts, incidents,
 * events, and devices.
 */
import type { Alert, Device, Event, Incident } from "@/lib/api";
import { stripHtml } from "@/lib/sanitize";
import type {
  SocAssetHealth, SocAttackVector, SocData, SocEvent, SocIncidentRow,
  SocKpi, SocMitreCoverage, SocThreatArc, SocThreatLocation, SocTrendPoint,
  SocWorkflowStage,
} from "./socModels";

function alertSev(s: string): "critical" | "high" | "medium" | "low" | "info" {
  const v = (s || "").toLowerCase();
  if (v.includes("critical") || v === "down") return "critical";
  if (v.includes("high") || v === "warning") return "high";
  if (v.includes("medium") || v.includes("warn")) return "medium";
  if (v.includes("low") || v.includes("info")) return "low";
  return "info";
}

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return "—";
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/* ------------------------------------------------------------------ */
/* Live builders                                                       */
/* ------------------------------------------------------------------ */

function buildKpis(
  alerts: Alert[],
  devices: Device[],
  available: { alerts: boolean; devices: boolean },
): SocKpi[] {
  const open = alerts.filter((a) => (a.status || "").toLowerCase() === "open");
  // A count is only "measured" once its query has settled without error: the
  // hooks pass `?? []`, so while loading or after a failure an empty array would
  // otherwise be reported as a real 0 (and "0 critical alerts" reads as health).
  const critical = available.alerts
    ? open.filter((a) => alertSev(a.severity) === "critical").length
    : null;
  const high = available.alerts
    ? open.filter((a) => alertSev(a.severity) === "high").length
    : null;
  const atRisk = available.devices
    ? devices.filter((d) => {
        const s = (d.status || "").toLowerCase();
        return s !== "up" && s !== "online" && s !== "healthy";
      }).length
    : null;
  const sourceOf = (value: number | null): SocKpi["source"] =>
    value === null ? "unavailable" : "live";
  return [
    { key: "critical", label: "Critical Threats", value: critical, change: null, description: "Require immediate attention", tone: "critical", source: sourceOf(critical) },
    { key: "high", label: "High Severity", value: high, change: null, description: "Investigating", tone: "high", source: sourceOf(high) },
    { key: "blocked", label: "Blocked Attacks", value: null, change: null, description: "No blocking telemetry integrated", tone: "info", source: "unavailable" },
    { key: "endpoints", label: "Endpoints at Risk", value: atRisk, change: null, description: "Require investigation", tone: "warn", source: sourceOf(atRisk) },
    { key: "identity", label: "Identity Alerts", value: null, change: null, description: "No identity provider integrated", tone: "info", source: "unavailable" },
    { key: "malware", label: "Malware Detected", value: null, change: null, description: "No malware detection integrated", tone: "violet", source: "unavailable" },
  ];
}

function buildEvents(events: Event[], alerts: Alert[]): SocEvent[] {
  const fromEvents: SocEvent[] = events.slice(0, 8).map((e) => ({
    id: e.id,
    time: timeAgo(e.occurred_at),
    severity: alertSev(e.severity),
    title: e.event_type.replace(/[_-]+/g, " "),
    description: stripHtml(e.message),
    source: "live",
  }));
  if (fromEvents.length > 0) return fromEvents;
  // Honest fallback only when the backend returned zero events.
  return alerts.slice(0, 6).map((a) => ({
    id: a.id,
    time: timeAgo(a.triggered_at),
    severity: alertSev(a.severity),
    title: a.title,
    description: stripHtml(a.description ?? a.source ?? ""),
    source: "live",
  }));
}

function buildIncidentRows(incidents: Incident[]): SocIncidentRow[] {
  return incidents.slice(0, 6).map((i) => ({
    id: i.id,
    ref: `INC-${i.id.slice(0, 4).toUpperCase()}`,
    title: i.title,
    severity: alertSev(i.severity),
    status: (i.status || "").replace(/_/g, " "),
    time: timeAgo(i.opened_at),
    source: "live",
  }));
}

function assetBucket(name: string, devices: Device[]): SocAssetHealth {
  const token = name.toLowerCase().split(/[\s/]/)[0].replace(/[^a-z]/g, "");
  const match = devices.filter((d) =>
    `${d.device_type} ${d.name} ${d.vendor ?? ""}`.toLowerCase().includes(token));
  const total = match.length;
  const down = match.filter((d) => {
    const s = (d.status || "").toLowerCase();
    return s !== "up" && s !== "online" && s !== "healthy";
  }).length;
  // No device matched this class: there is no verdict to report. The old
  // `status: "warning"` + "0/0" asserted a health state (and a measured zero)
  // for an asset class the tenant may simply not own.
  if (total === 0) return { name, status: "unknown", availability: "—", source: "unavailable" };
  return {
    name,
    status: down === 0 ? "healthy" : down < Math.max(1, Math.ceil(total / 3)) ? "warning" : "critical",
    availability: `${total - down}/${total}`,
    source: "live",
  };
}

function buildAssets(devices: Device[]): SocAssetHealth[] {
  // Asset buckets are derived from actual device types/reporting site names,
  // never from hardcoded category labels. A tenant with no devices in a class
  // simply does not see that class — no "0/0" availability assertion.
  const byType: Record<string, Device[]> = {};
  for (const d of devices) {
    const key = (d.device_type || "unknown").toLowerCase();
    byType[key] = [...(byType[key] ?? []), d];
  }
  return Object.entries(byType).map(([name, group]) => assetBucket(name, group));
}

/* ------------------------------------------------------------------ */
/* Composition                                                         */
/* ------------------------------------------------------------------ */

export function buildSocData(
  input: {
    alerts: Alert[]; incidents: Incident[]; events: Event[]; devices: Device[];
  },
  /**
   * Which of the underlying queries actually settled without error. The hooks
   * pass `?? []`, so without this signal an unavailable payload is
   * indistinguishable from a measured empty estate - which is how "0 critical
   * alerts" and a "Good" posture could be asserted from a failed fetch.
   * Defaults to all-available so existing callers keep their behaviour.
   */
  available: { alerts: boolean; devices: boolean; incidents: boolean } = {
    alerts: true, devices: true, incidents: true,
  },
): SocData {
  const { alerts, incidents, events, devices } = input;
  const kpis = buildKpis(alerts, devices, available);
  const openIncidents = incidents.filter((i) => {
    const s = (i.status || "").toLowerCase();
    return s !== "closed" && s !== "archived";
  });
  const criticalCount = kpis[0].value;
  const atRisk = kpis[3].value;
  // The verdict is only derived when BOTH counters exist; a null input yields a
  // null posture, never "Good". (The thresholds themselves are the documented
  // product policy and are unchanged.)
  const posture: SocData["summary"]["posture"] =
    criticalCount === null || atRisk === null ? null
      : criticalCount > 5 ? "Critical" : criticalCount > 0 || atRisk > 5 ? "Fair" : "Good";

  return {
    summary: {
      posture,
      monitoredAssets: available.devices && devices.length > 0 ? devices.length : null,
      openIncidents: available.incidents ? openIncidents.length : null,
      source: available.devices && devices.length > 0 ? "live" : "unavailable",
    },
    kpis,
    trend: { points: [], source: "unavailable" },
    attackVectors: { rows: [], source: "unavailable" },
    mitre: { percent: 0, identified: 0, monitored: 0, total: 0, source: "unavailable" },
    events: buildEvents(events, alerts),
    incidents: buildIncidentRows(openIncidents),
    assets: buildAssets(devices),
    threatMap: { points: [], arcs: [], source: "unavailable" },
    workflow: [],
    states: {
      alerts: available.alerts ? (alerts.length > 0 ? "success" : "empty") : "loading",
      incidents: available.incidents ? (openIncidents.length > 0 ? "success" : "empty") : "loading",
      events: available.alerts ? (events.length > 0 ? "success" : "empty") : "loading",
      devices: available.devices ? (devices.length > 0 ? "success" : "empty") : "loading",
    },
  };
}

/** Severity → CSS color token used by every SOC widget. */
export function sevColor(s: string): string {
  switch ((s || "").toLowerCase()) {
    case "critical": return "#ff5470";
    case "high": return "#f5a623";
    case "medium": return "#ffd166";
    case "low": return "#38bdf8";
    default: return "#6b8494";
  }
}

