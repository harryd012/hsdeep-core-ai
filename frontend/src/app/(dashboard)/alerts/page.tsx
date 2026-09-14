"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Bell } from "lucide-react";
import { Alert, AlertSummary, listAlerts, getAlertSummary } from "@/lib/api";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.round(diffMs / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [alertList, alertSum] = await Promise.all([
          listAlerts({ limit: 50 }),
          getAlertSummary(),
        ]);
        if (!cancelled) {
          setAlerts(alertList);
          setSummary(alertSum);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load alerts");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const critical = summary?.by_severity.critical ?? 0;
  const warning = summary?.by_severity.warning ?? 0;
  const info = summary?.by_severity.info ?? 0;
  const total = summary?.total ?? 0;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>ALERT SUMMARY</h2>
          {error && <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}><AlertTriangle size={14} /> {error}</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 16, marginBottom: 24 }}>
            {[
              [String(critical), "Critical", "var(--red)"],
              [String(warning), "Warning", "var(--amber)"],
              [String(info), "Info", "#119be1"],
              [String(total), "Total", "var(--cyan)"],
            ].map(([value, label, color]) => (
              <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                <div style={{ fontSize: "clamp(28px,2.2vw,42px)", color, fontWeight: 700 }}>{value}</div>
                <div style={{ fontSize: "clamp(11px,.7vw,14px)", color: "var(--muted)" }}>{label}</div>
              </div>
            ))}
          </div>

          <h2 style={{ marginTop: 16 }}>ACTIVE ALERTS ({alerts.length})</h2>
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading alerts…</p>}
          {loaded && alerts.length === 0 && (
            <div style={{ textAlign: "center", padding: 32 }}>
              <CheckCircle2 size={48} style={{ color: "var(--green)", margin: "0 auto 12px" }} />
              <p style={{ color: "var(--muted)" }}>No active alerts. All systems operational.</p>
            </div>
          )}
          <div style={{ overflowY: "auto", maxHeight: 500 }}>
            {alerts.map((a) => (
              <div key={a.id} className="alert-row">
                <i className={a.severity} />
                <div>
                  <span>{a.title}</span>
                  <small style={{ display: "block", color: "var(--muted)", fontSize: "clamp(10px,.65vw,12px)", marginTop: 2 }}>
                    {a.description ?? ""}
                  </small>
                </div>
                <time>{timeAgo(a.last_occurred_at)}</time>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}