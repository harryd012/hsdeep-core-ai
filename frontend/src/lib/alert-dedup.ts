/**
 * Alert Fingerprinting & Deduplication Engine (Sprint 16)
 *
 * Defines a fingerprint/dedup key per alert based on:
 *   - source device/host (device_id, falling back to site_id)
 *   - port/service (extracted from the alert title or monitoring_source_id)
 *   - alert type (metric_type, falling back to a normalized title)
 *
 * NOT alert message text alone — minor wording differences (e.g. "SSL Security
 * Check (Port 993)" vs "SSL Security Check (Port 993) — Certificate Expired")
 * won't create separate incidents as long as the host + port + type match.
 *
 * When a new alert matches an existing open fingerprint, its occurrence_count
 * is folded into the existing group instead of creating a new row. First-seen
 * and last-seen timestamps are tracked per fingerprint.
 *
 * This is a client-side presentation-layer dedup. The backend already prevents
 * alert storms per-sensor (uq_alerts_active_per_sensor) and per-vendor-signal
 * (uq_alerts_active_per_signal), but different sensors/ports for the same
 * underlying issue (e.g. IMAPS on 993 + SMTPS on 465) create separate records.
 * This layer groups those for display without modifying the backend.
 */
import type { Alert, AlertSeverity, AlertStatus } from "./api";

// ---------------------------------------------------------------------------
// Fingerprint computation
// ---------------------------------------------------------------------------

/** Extract a port number from an alert title like "SSL Security Check (Port 993)". */
export function extractPortFromTitle(title: string): string | null {
  const match = title.match(/Port\s*(\d+)/i);
  return match ? match[1] : null;
}

/**
 * Normalize an alert title by stripping port annotations and collapsing
 * whitespace, so that "SSL Security Check (Port 993)" and
 * "SSL Security Check (Port 993) — Certificate Expired" map to the same
 * base type when combined with the metric_type.
 */
export function normalizeTitle(title: string): string {
  return title
    .replace(/Port\s*\d+/gi, "")
    .replace(/[—–-]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Compute a stable fingerprint string for an alert.
 *
 * Format: `<host>|<port>|<alertType>`
 *   - host: device_id (preferred) or site_id (fallback) or "unknown"
 *   - port: extracted port from title, or monitoring_source_id as fallback
 *   - alertType: metric_type (preferred) or normalized title (fallback)
 *
 * Two alerts with the same fingerprint represent the same underlying problem
 * on the same host/port/service, regardless of minor wording differences.
 */
export function alertFingerprint(alert: Alert): string {
  const host = alert.device_id || alert.site_id || "unknown";
  const port = extractPortFromTitle(alert.title) || alert.monitoring_source_id || "unknown";
  const alertType = alert.metric_type || normalizeTitle(alert.title) || "unknown";
  return `${host}|${port}|${alertType}`;
}

// ---------------------------------------------------------------------------
// Grouped alert representation
// ---------------------------------------------------------------------------

export interface GroupedAlert {
  /** The fingerprint key this group represents. */
  fingerprint: string;
  /** Display title (from the first/most-recent alert in the group). */
  title: string;
  /** Highest severity in the group (critical > warning > info). */
  severity: AlertSeverity;
  /** Status (open/acknowledged/resolved) — groups are typically open. */
  status: AlertStatus;
  /** Total occurrence count across all alerts in this group. */
  occurrenceCount: number;
  /** Earliest triggered_at across all alerts in the group. */
  firstSeen: string;
  /** Latest last_occurred_at across all alerts in the group. */
  lastSeen: string;
  /** Individual alert records for drill-down detail. */
  alerts: Alert[];
  /** Denormalized display fields from the first alert. */
  deviceName: string | null;
  siteName: string | null;
  monitoringSourceId: string | null;
  sensorName: string | null;
}

/** Severity ranking for comparison (higher = more severe). */
const SEVERITY_RANK: Record<string, number> = {
  critical: 2,
  warning: 1,
  info: 0,
};

function maxSeverity(a: AlertSeverity, b: AlertSeverity): AlertSeverity {
  return SEVERITY_RANK[b] > SEVERITY_RANK[a] ? b : a;
}

/**
 * Group a list of raw Alert records into deduplicated GroupedAlert entries.
 *
 * Alerts sharing the same fingerprint are merged:
 *   - occurrenceCount is summed
 *   - firstSeen is the earliest triggered_at
 *   - lastSeen is the latest last_occurred_at
 *   - severity is the highest across all members
 *
 * Results are sorted by lastSeen descending (most recent first).
 */
export function groupAlertsByFingerprint(alerts: Alert[]): GroupedAlert[] {
  const groups = new Map<string, GroupedAlert>();

  for (const alert of alerts) {
    const fp = alertFingerprint(alert);
    const existing = groups.get(fp);

    if (existing) {
      existing.occurrenceCount += alert.occurrence_count;
      existing.alerts.push(alert);
      existing.severity = maxSeverity(existing.severity, alert.severity);

      if (new Date(alert.last_occurred_at) > new Date(existing.lastSeen)) {
        existing.lastSeen = alert.last_occurred_at;
      }
      if (new Date(alert.triggered_at) < new Date(existing.firstSeen)) {
        existing.firstSeen = alert.triggered_at;
      }
    } else {
      groups.set(fp, {
        fingerprint: fp,
        title: alert.title,
        severity: alert.severity,
        status: alert.status,
        occurrenceCount: alert.occurrence_count,
        firstSeen: alert.triggered_at,
        lastSeen: alert.last_occurred_at,
        alerts: [alert],
        deviceName: alert.device_name,
        siteName: alert.site_name,
        monitoringSourceId: alert.monitoring_source_id,
        sensorName: alert.sensor_name,
      });
    }
  }

  return Array.from(groups.values()).sort(
    (a, b) => new Date(b.lastSeen).getTime() - new Date(a.lastSeen).getTime(),
  );
}

// ---------------------------------------------------------------------------
// Deduplicated summary
// ---------------------------------------------------------------------------

export interface DeduplicatedAlertSummary {
  /** Number of distinct fingerprints (deduplicated count). */
  total: number;
  /** Deduplicated counts by severity. */
  by_severity: { critical: number; warning: number; info: number };
  /** Raw alert-event count (for debugging — distinct from the user-facing number). */
  raw_event_count: number;
  /** Total occurrence count across all grouped alerts. */
  total_occurrences: number;
}

/**
 * Compute a deduplicated alert summary from a list of raw Alert records.
 *
 * The `total` and `by_severity` reflect distinct fingerprints (the
 * user-facing numbers), while `raw_event_count` preserves the raw count
 * for debugging.
 */
export function computeDeduplicatedSummary(alerts: Alert[]): DeduplicatedAlertSummary {
  const grouped = groupAlertsByFingerprint(alerts);

  const by_severity: { critical: number; warning: number; info: number } = {
    critical: 0,
    warning: 0,
    info: 0,
  };

  let totalOccurrences = 0;

  for (const g of grouped) {
    by_severity[g.severity] = (by_severity[g.severity] ?? 0) + 1;
    totalOccurrences += g.occurrenceCount;
  }

  return {
    total: grouped.length,
    by_severity,
    raw_event_count: alerts.length,
    total_occurrences: totalOccurrences,
  };
}

// ---------------------------------------------------------------------------
// Time formatting helper (shared with UI components)
// ---------------------------------------------------------------------------

export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  const minutes = Math.max(0, Math.round(diffMs / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours < 24) return mins > 0 ? `${hours}h ${mins}m ago` : `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
