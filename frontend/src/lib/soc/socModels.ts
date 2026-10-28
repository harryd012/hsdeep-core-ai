/**
 * socModels.ts — typed models for the Security Operations Center dashboard.
 *
 * LIVE vs FALLBACK:
 *  - Models populated from existing backend APIs (lib/api.ts) are live data.
 *  - The backend has NO SOC-specific endpoints yet (threat-trend series,
 *    attack vectors, MITRE ATT&CK coverage, geo threat map, workflow stats,
 *    identity/malware/blocked counters). Those categories use the typed
 *    FALLBACK data at the bottom of socAdapter.ts. Every fallback record is
 *    marked `source: "fallback"` and the UI labels it "demo data" so a fake
 *    value can never masquerade as a live one. Replace each FALLBACK_* block
 *    with the real endpoint when the backend ships it.
 */

export type SocSeverity = "critical" | "high" | "medium" | "low" | "info";

export interface SocKpi {
  key: string;
  label: string;
  value: number | null;
  change: string | null;
  description: string;
  tone: "critical" | "high" | "good" | "warn" | "info" | "violet";
  /**
   * "live" = computed from backend data; "fallback" = demo data (labelled in the
   * UI); "unavailable" = the backend could not be read, so no value is shown.
   * A count that is unknown must never be reported as a measured 0.
   */
  source: "live" | "fallback" | "unavailable";
}

export interface SocTrendPoint {
  label: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface SocAttackVector {
  label: string;
  count: number;
}

export interface SocMitreCoverage {
  percent: number;
  identified: number;
  monitored: number;
  total: number;
}

export interface SocEvent {
  id: string;
  time: string;
  severity: SocSeverity;
  title: string;
  description: string;
  source: "live" | "fallback";
}

export interface SocIncidentRow {
  id: string;
  ref: string;
  title: string;
  severity: SocSeverity;
  status: string;
  time: string;
  source: "live" | "fallback";
}

export interface SocAssetHealth {
  name: string;
  /** "unknown" = no device matched this class, so no health verdict exists. */
  status: "healthy" | "warning" | "critical" | "unknown";
  /** "—" when there is nothing measured to report (never "0/0"). */
  availability: string;
  source: "live" | "unavailable";
}

export interface SocThreatLocation {
  city: string;
  /** Percent-based map coordinates (0-100) on the equirectangular map. */
  x: number;
  y: number;
  severity: SocSeverity;
}

export interface SocThreatArc {
  from: SocThreatLocation;
  to: SocThreatLocation;
  severity: SocSeverity;
}

export interface SocWorkflowStage {
  key: string;
  title: string;
  description: string;
  count: number | null;
}

export interface SocData {
  summary: {
    /**
     * Derived from the critical-alert and at-risk-device counts. null when
     * either input is unavailable - an unknown posture must never render as
     * "Good" (that inference is what a `?? 0` fallback used to produce).
     */
    posture: "Good" | "Fair" | "Critical" | null;
    monitoredAssets: number | null;
    /** Open (not closed/archived) incidents, or null when unavailable. */
    openIncidents: number | null;
    source: "live" | "unavailable";
  };
  kpis: SocKpi[];
  trend: { points: SocTrendPoint[]; source: "live" | "unavailable" };
  attackVectors: { rows: SocAttackVector[]; source: "live" | "unavailable" };
  mitre: SocMitreCoverage & { source: "live" | "unavailable" };
  events: SocEvent[];
  incidents: SocIncidentRow[];
  assets: SocAssetHealth[];
  threatMap: { points: SocThreatLocation[]; arcs: SocThreatArc[]; source: "live" | "unavailable" };
  workflow: SocWorkflowStage[];
  states: {
    alerts: "loading" | "success" | "empty" | "error";
    incidents: "loading" | "success" | "empty" | "error";
    events: "loading" | "success" | "empty" | "error";
    devices: "loading" | "success" | "empty" | "error";
  };
}
