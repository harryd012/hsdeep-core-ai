"use client";

/**
 * First real live-data panels in the frontend. Replaces the hardcoded
 * `alerts` array and inline "ALERT SUMMARY" / "TOP ALERTS" panel bodies that
 * previously lived in CoreHUD.tsx with actual fetches to the Alert Engine
 * API (/api/alerts, /api/alerts/summary).
 *
 * Reuses the exact `.hud-panel` / `.alert-row` / `.donut` markup and CSS
 * classes already defined in globals.css, so no styling changes were
 * needed - only the data source changed, from hardcoded to live.
 */

import { AlertTriangle, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, AlertSummary, getAlertSummary, listAlerts } from "@/lib/api";

const REFRESH_MS = 15000;

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.round(diffMs / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

export function TopAlertsPanel() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await listAlerts({ status: "open", limit: 6 });
        if (!cancelled) {
          setAlerts(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load alerts");
        }
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <section className="hud-panel alerts">
      <h2>TOP ALERTS</h2>
      {error && (
        <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}>
          <AlertTriangle size={14} /> {error}
        </p>
      )}
      {!error && loaded && alerts.length === 0 && (
        <p style={{ color: "#75909b", fontSize: 12 }}>No open alerts.</p>
      )}
      {alerts.map((alert) => (
        <div className="alert-row" key={alert.id}>
          <i className={alert.severity} />
          <span>{alert.title}</span>
          <time>{timeAgo(alert.last_occurred_at)}</time>
        </div>
      ))}
      <button type="button">
        View All Alerts <ChevronRight />
      </button>
    </section>
  );
}

export function AlertSummaryPanel() {
  const [summary, setSummary] = useState<AlertSummary | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getAlertSummary();
        if (!cancelled) setSummary(data);
      } catch {
        // Decorative context for the donut - a failed fetch just leaves the
        // zero-state rather than surfacing a second error banner here (the
        // TopAlertsPanel above already reports connectivity problems).
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const critical = summary?.by_severity.critical ?? 0;
  const warning = summary?.by_severity.warning ?? 0;
  const info = summary?.by_severity.info ?? 0;
  const total = summary?.total ?? 0;

  return (
    <section className="hud-panel summary">
      <h2>ALERT SUMMARY</h2>
      <div className="donut">
        <span>
          <b>{total}</b>
          <small>TOTAL</small>
        </span>
      </div>
      <ul>
        <li>
          <i className="red" />
          {critical} Critical
        </li>
        <li>
          <i className="amber" />
          {warning} Warning
        </li>
        <li>
          <i className="blue" />
          {info} Info
        </li>
      </ul>
    </section>
  );
}