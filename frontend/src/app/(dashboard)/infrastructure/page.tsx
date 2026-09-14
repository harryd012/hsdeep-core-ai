"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Server, Globe2, Radio, Cloud } from "lucide-react";
import {
  DashboardSummary, Device,
  getDashboardSummary, listDevices,
  getTopology, TopologyOut,
} from "@/lib/api";

export default function InfrastructurePage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [topology, setTopology] = useState<TopologyOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [sum, devs, topo] = await Promise.all([
          getDashboardSummary(),
          listDevices(),
          getTopology(),
        ]);
        if (!cancelled) {
          setSummary(sum);
          setDevices(devs);
          setTopology(topo);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load infrastructure");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>INFRASTRUCTURE OVERVIEW</h2>
          {error && <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}><AlertTriangle size={14} /> {error}</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16 }}>
            {([
              [loaded ? String(summary?.sites ?? 0) : "…", "SITES", Globe2],
              [loaded ? String(summary?.devices ?? 0) : "…", "DEVICES", Server],
              [loaded ? String(summary?.sensors ?? 0) : "…", "SENSORS", Radio],
              [loaded ? String(summary?.cloud_resources ?? 0) : "…", "CLOUD", Cloud],
            ] as const).map(([value, label, Icon]) => (
              <div key={label} style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                <Icon size={32} style={{ color: "var(--cyan)", margin: "0 auto 8px" }} />
                <div style={{ fontSize: "clamp(24px,1.8vw,36px)", color: "var(--cyan)", fontWeight: 700 }}>{value}</div>
                <div style={{ fontSize: "clamp(11px,.7vw,14px)", color: "var(--muted)" }}>{label}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="hud-panel">
          <h2>DEVICE INVENTORY ({devices.length})</h2>
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading devices…</p>}
          {loaded && devices.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12 }}>No devices discovered yet.</p>}
          {loaded && topology && topology.sites.length === 0 && (
            <p style={{ color: "var(--muted)", fontSize: 12 }}>No sites configured for this tenant.</p>
          )}
          <div style={{ overflowY: "auto", maxHeight: 400 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "clamp(11px,.75vw,14px)" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--cyan)" }}>
                  <th style={{ textAlign: "left", padding: "8px 4px" }}>Name</th>
                  <th style={{ textAlign: "left", padding: "8px 4px" }}>Type</th>
                  <th style={{ textAlign: "left", padding: "8px 4px" }}>Vendor</th>
                  <th style={{ textAlign: "left", padding: "8px 4px" }}>Status</th>
                  <th style={{ textAlign: "left", padding: "8px 4px" }}>IP</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id} style={{ borderBottom: "1px solid rgba(0,217,255,.08)" }}>
                    <td style={{ padding: "8px 4px" }}>{d.name}</td>
                    <td style={{ padding: "8px 4px", color: "var(--muted)" }}>{d.device_type}</td>
                    <td style={{ padding: "8px 4px", color: "var(--muted)" }}>{d.vendor ?? "—"}</td>
                    <td style={{ padding: "8px 4px" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <i className={d.status} style={{ width: 6, height: 6, borderRadius: "50%", display: "inline-block" }} />
                        {d.status}
                      </span>
                    </td>
                    <td style={{ padding: "8px 4px", color: "var(--muted)" }}>{d.ip_address ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Topology summary */}
          <div style={{ marginTop: 16 }}>
            <h3>TOPOLOGY</h3>
            {!topology && <p style={{ color: "var(--muted)" }}>Topology not loaded.</p>}
            {topology && (
              <div>
                <div style={{ fontSize: 12, color: "var(--muted)" }}>Generated: {new Date(topology.generated_at).toLocaleString()}</div>
                <div style={{ marginTop: 8 }}>
                  {topology.sites.map((site) => (
                    <div key={site.id} style={{ padding: 8, border: "1px solid var(--line)", borderRadius: 6, marginBottom: 8 }}>
                      <div style={{ fontWeight: 700 }}>{site.name}</div>
                      <div style={{ fontSize: 12, color: "var(--muted)" }}>
                        Devices: {site.devices.length} · Monitoring Sources: {site.monitoring_sources.length}
                      </div>
                      {site.monitoring_sources.map((src) => (
                        <div key={src.id} style={{ marginTop: 6, paddingLeft: 8 }}>
                          <div style={{ fontSize: 13 }}>{src.name} <small style={{ color: "var(--muted)", fontSize: 11 }}>({src.source_type})</small></div>
                          <div style={{ fontSize: 12, color: "var(--muted)", paddingLeft: 8 }}>Sensors: {src.sensors.length}</div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}