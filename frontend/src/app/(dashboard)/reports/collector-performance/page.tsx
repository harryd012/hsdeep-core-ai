"use client";

import { useEffect, useState } from "react";
import ReportsBackButton from "@/components/reports/ReportsBackButton";
import { apiFetch, DEFAULT_TENANT_ID } from "@/lib/api";

interface CollectorRun {
  id: string;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  devices_upserted: number;
  sensors_upserted: number;
}

export default function CollectorPerformancePage() {
  const [runs, setRuns] = useState<CollectorRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch<CollectorRun[]>(`/api/collector-runs?tenant_id=${DEFAULT_TENANT_ID}&limit=100`);
        if (!cancelled) {
          setRuns(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load collector runs");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const successCount = runs.filter((r) => r.status === "success").length;
  const errorCount = runs.filter((r) => r.status === "error").length;
  const successRate = runs.length ? Math.round((successCount / runs.length) * 100) : 0;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <ReportsBackButton />
            <h2>COLLECTOR PERFORMANCE</h2>
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</p>}
          {loaded && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16, marginBottom: 16 }}>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{runs.length}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Total Runs</div>
                </div>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--green)", fontWeight: 700 }}>{successCount}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Success</div>
                </div>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--red)", fontWeight: 700 }}>{errorCount}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Errors</div>
                </div>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: successRate >= 90 ? "var(--green)" : "var(--amber)", fontWeight: 700 }}>{successRate}%</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Success Rate</div>
                </div>
              </div>
              {runs.length > 0 && (
                <div>
                  <h3 style={{ color: "#c5f3ff", fontSize: 13, marginBottom: 8 }}>RECENT RUNS</h3>
                  <div style={{ maxHeight: 400, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
                    {runs.map((r) => (
                      <div key={r.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
                        <span style={{ color: "#c5f3ff", fontSize: 12 }}>{r.id.slice(0, 8)}</span>
                        <span style={{ color: r.status === "success" ? "var(--green)" : r.status === "error" ? "var(--red)" : "var(--amber)", fontSize: 11 }}>{r.status}</span>
                        <span style={{ color: "var(--muted)", fontSize: 11 }}>{new Date(r.started_at).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}