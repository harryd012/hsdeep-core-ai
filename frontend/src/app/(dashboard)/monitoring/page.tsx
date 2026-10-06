"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Radio, Activity, CheckCircle2 } from "lucide-react";
import { MonitoringSourceOut, SensorOut, listMonitoringSources, listSourceSensors, listSensors } from "@/lib/api";
import { ALERTING_STATES, classifySensorState } from "@/lib/sensorState";
import FreshnessIndicator from "@/components/shared/FreshnessIndicator";

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

/** A tenant-owned PRTG source paired with the canonical Sensor rows persisted
 *  for it by the Collector Framework. */
interface PrtgSensorGroup {
  source: MonitoringSourceOut;
  sensors: SensorOut[];
}

/** Render a canonical sensor reading, e.g. "12.4 ms", or an em dash when the
 *  collector has not recorded a numeric value. */
function formatSensorValue(sensor: SensorOut): string {
  if (sensor.current_value === null) return "—";
  return sensor.unit ? `${sensor.current_value} ${sensor.unit}` : String(sensor.current_value);
}

export default function MonitoringPage() {
  const [sensors, setSensors] = useState<SensorOut[]>([]);
  const [sources, setSources] = useState<MonitoringSourceOut[]>([]);
  const [prtgSensorGroups, setPrtgSensorGroups] = useState<PrtgSensorGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [srcList, senList] = await Promise.all([
          listMonitoringSources(),
          listSensors(),
        ]);
        // Provider sensor rows come from the canonical, tenant-scoped
        // per-source endpoint. Each source is its own request so a failure on
        // one PRTG source cannot masquerade as a different tenant's data.
        const prtgSources = srcList.filter((s) => s.source_type === "prtg");
        const groups: PrtgSensorGroup[] = await Promise.all(
          prtgSources.map(async (source) => ({
            source,
            sensors: await listSourceSensors(source.id),
          })),
        );
        if (!cancelled) {
          setSources(srcList);
          setSensors(senList);
          setPrtgSensorGroups(groups);
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

  // Canonical states, not raw status strings: a `up` sensor whose last value is
  // older than 2h classifies as STALE and must not be counted as healthy.
  const upCount = sensors.filter(
    (s) => classifySensorState(s.status, s.last_value_at).state === "HEALTHY",
  ).length;
  const downCount = sensors.filter((s) =>
    ALERTING_STATES.includes(classifySensorState(s.status, s.last_value_at).state),
  ).length;
  const prtgSensorRows = prtgSensorGroups.flatMap((group) =>
    group.sensors.map((sensor) => ({ source: group.source, sensor })),
  );

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
            <h2>PRTG SENSORS ({prtgSensorRows.length})</h2>
            <p style={{ color: "var(--muted)", fontSize: 11 }}>
              Collected sensor rows persisted for this tenant&apos;s PRTG sources, read through the
              tenant-scoped per-source monitoring API. No direct probe access, no cross-tenant rows.
            </p>
            {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading PRTG sensors…</p>}
            {loaded && prtgSensorGroups.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12 }}>No PRTG monitoring source is configured for this tenant.</p>}
            {loaded && prtgSensorGroups.length > 0 && prtgSensorRows.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12 }}>No sensor rows have been collected for this tenant&apos;s PRTG sources yet.</p>}
            <div style={{ maxHeight: 420, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--cyan)" }}>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Sensor</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Status</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Value</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Metric</th>
                    <th style={{ textAlign: "left", padding: "10px 12px" }}>Freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {prtgSensorRows.map(({ source, sensor }) => {
                    const si = classifySensorState(sensor.status, sensor.last_value_at);
                    return (
                    <tr key={sensor.id} style={{ borderBottom: "1px solid rgba(0,217,255,.08)" }}>
                      <td style={{ padding: "10px 12px" }}>
                        {sensor.name} <small style={{ color: "var(--muted)", fontSize: 10 }}>({source.name})</small>
                      </td>
                      <td style={{ padding: "10px 12px", color: si.color }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                          <i style={{ width: 6, height: 6, borderRadius: "50%", background: si.dot, display: "inline-block" }} />
                          {si.label}
                        </span>
                      </td>
                      <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{formatSensorValue(sensor)}</td>
                      <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{sensor.metric_type}</td>
                      <td style={{ padding: "10px 12px" }}>
                        <FreshnessIndicator lastCollectedAt={sensor.last_value_at} showAge />
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
