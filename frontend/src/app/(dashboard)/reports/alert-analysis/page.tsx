"use client";

import { useEffect, useState } from "react";
import ReportsBackButton from "@/components/reports/ReportsBackButton";
import { apiFetch, DEFAULT_TENANT_ID } from "@/lib/api";

interface AlertStats {
  total: number;
  by_severity: { critical: number; warning: number; info: number };
  by_status: { open: number; acknowledged: number; resolved: number };
}

export default function AlertAnalysisPage() {
  const [stats, setStats] = useState<AlertStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch<AlertStats>(`/api/alerts/summary?tenant_id=${DEFAULT_TENANT_ID}`);
        if (!cancelled) {
          setStats(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load alert analysis");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <ReportsBackButton />
            <h2>ALERT ANALYSIS</h2>
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</p>}
          {loaded && stats && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16 }}>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{stats.total}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Total Alerts</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--red)", fontWeight: 700 }}>{stats.by_severity.critical}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Critical</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--amber)", fontWeight: 700 }}>{stats.by_severity.warning}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Warning</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "#119be1", fontWeight: 700 }}>{stats.by_severity.info}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Info</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{stats.by_status.open}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Open</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{stats.by_status.acknowledged}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Acknowledged</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--green)", fontWeight: 700 }}>{stats.by_status.resolved}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Resolved</div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}