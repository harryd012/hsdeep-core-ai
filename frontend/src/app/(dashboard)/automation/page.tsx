"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Workflow, Cpu, CheckCircle2, type LucideIcon } from "lucide-react";
import { AutomationStatus, getAutomationStatus } from "@/lib/api";

interface StatCard {
  value: string;
  label: string;
  Icon: LucideIcon;
  color: string;
}

export default function AutomationPage() {
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await getAutomationStatus();
        if (!cancelled) { setStatus(data); setError(null); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load automation status");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const stats: StatCard[] = [
    { value: String(status?.sources_connected ?? (loaded ? 0 : "…")), label: "Active Sources", Icon: Workflow, color: "var(--cyan)" },
    { value: String(status?.runs_last_24h ?? (loaded ? 0 : "…")), label: "Executed Today", Icon: Cpu, color: "var(--cyan)" },
    { value: `${status?.success_rate_pct ?? (loaded ? 0 : "…")}%`, label: "Success Rate", Icon: CheckCircle2, color: "var(--green)" },
  ];

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>AUTOMATION CENTER</h2>
          {error && <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}><AlertTriangle size={14} /> {error}</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16, marginBottom: 24 }}>
            {stats.map(({ value, label, Icon, color }) => (
              <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                <Icon size={32} style={{ color, margin: "0 auto 8px" }} />
                <div style={{ fontSize: "clamp(24px,1.8vw,36px)", color, fontWeight: 700 }}>{value}</div>
                <div style={{ fontSize: "clamp(11px,.7vw,14px)", color: "var(--muted)" }}>{label}</div>
              </div>
            ))}
          </div>
          <h2>COLLECTOR RUNS</h2>
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading runs…</p>}
          {loaded && (!status?.sources || status.sources.length === 0) && <p style={{ color: "var(--muted)", fontSize: 12 }}>No collector activity yet.</p>}
          {status?.sources?.map((s) => (
            <div key={s.source_id} className="alert-row">
              <i className={s.last_run_status === "success" ? "info" : "critical"} />
              <span>{s.source_name}</span>
              <time>{s.last_run_status ?? "never"}</time>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
