"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Radio, Activity, CheckCircle2 } from "lucide-react";
import { MonitoringSourceOut, PrtgSensorOut, SensorOut, listMonitoringSources, listPrtgRootSensors, listSensors } from "@/lib/api";

interface StatCard {
  value: string;
  label: string;
  color: string;
}

const iconMap: Record<string, typeof Activity> = {
  Sources: Activity,
  Sensors: Radio,
  Healthy: CheckCircle2,
  Alerting: AlertTriangle,
};

export default function MonitoringPage() {
  const [sensors, setSensors] = useState<SensorOut[]>([]);
  const [sources, setSources] = useState<MonitoringSourceOut[]>([]);
  const [prtgSensors, setPrtgSensors] = useState<PrtgSensorOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [srcList, senList, prtgList] = await Promise.all([
          listMonitoringSources(),
          listSensors(),
          listPrtgRootSensors(),
        ]);
        if (!cancelled) {
          setSources(srcList);
          setSensors(senList);
          setPrtgSensors(prtgList);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load monitoring data");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const upCount = sensors.filter((s) => s.status === "up").length;
  const downCount = sensors.filter((s) => s.status === "down" || s.status === "warning").length;

  const stats: StatCard[] = [
    { value: String(sources.length), label: "Sources", color: "var(--cyan)" },
    { value: String(sensors.length), label: "Sensors", color: "var(--cyan)" },
    { value: String(upCount), label: "Healthy", color: "var(--green)" },
    { value: String(downCount), label: "Alerting", color: "var(--red)" },
  ];

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>MONITORING OVERVIEW</h2>
          {error && <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}><AlertTriangle size={14} /> {error}</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginBottom: 24 }}>
            {stats.map(({ value, label, color }) => {
              const Icon = iconMap[label] ?? Activity;
              return (
                <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                  <Icon size={28} style={{ color, margin: "0 auto 8px" }} />
                  <div style={{ fontSize: "clamp(24px,1.8vw,36px)", color, fontWeight: 700 }}>{value}</div>
                  <div style={{ fontSize: "clamp(11px,.7vw,14px)", color: "var(--muted)" }}>{label}</div>
                </div>
              );
            })}
          </div>
          <h2>MONITORING SOURCES ({sources.length})</h2>
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading sources…</p>}
          {loaded && sources.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12 }}>No monitoring sources configured.</p>}
          {sources.map((s) => (
            <div key={s.id} className="alert-row">
              <i className={s.status === "connected" ? "info" : "critical"} />
              <span>{s.name} <small style={{ color: "var(--muted)", fontSize: 10 }}>({s.source_type})</small></span>
              <time>{s.status}</time>
            </div>
          ))}

          <div style={{ marginTop: 24 }}>
            <h2>PRTG ROOT SENSORS (LIVE)</h2>
            {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading live PRTG sensors…</p>}
            {loaded && prtgSensors.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12 }}>No live sensor rows returned from the PRTG probe.</p>}
            <div style={{ maxHeight: 420, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--cyan)" }}>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Sensor</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Status</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Value</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {prtgSensors.map((sensor) => (
                    <tr key={`${sensor.objid ?? sensor.name}-${sensor.name}`} style={{ borderBottom: "1px solid rgba(0,217,255,.08)" }}>
                      <td style={{ padding: "10px 12px" }}>{sensor.name}</td>
                      <td style={{ padding: "10px 12px", color: sensor.status === "up" ? "var(--green)" : sensor.status === "warning" ? "var(--amber)" : "var(--red)" }}>
                        {sensor.status}
                      </td>
                      <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{sensor.lastvalue ?? "—"}</td>
                      <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{sensor.message ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
