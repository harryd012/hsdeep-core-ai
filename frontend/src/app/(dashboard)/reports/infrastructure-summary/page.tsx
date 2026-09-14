"use client";

import { useEffect, useState } from "react";
import ReportsBackButton from "@/components/reports/ReportsBackButton";
import { apiFetch, DEFAULT_TENANT_ID } from "@/lib/api";

interface Site { id: string; name: string; }
interface Device { id: string; name: string; device_type: string; status: string; }

export default function InfrastructureSummaryPage() {
  const [sites, setSites] = useState<Site[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [sitesData, devicesData] = await Promise.all([
          apiFetch<Site[]>(`/api/infrastructure/sites?tenant_id=${DEFAULT_TENANT_ID}`),
          apiFetch<Device[]>(`/api/devices?tenant_id=${DEFAULT_TENANT_ID}&limit=500`),
        ]);
        if (!cancelled) {
          setSites(sitesData);
          setDevices(devicesData);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load infrastructure data");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const deviceTypes = Array.from(new Set(devices.map((d) => d.device_type).filter(Boolean)));

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <ReportsBackButton />
            <h2>INFRASTRUCTURE SUMMARY</h2>
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</p>}
          {loaded && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16 }}>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{sites.length}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Sites</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{devices.length}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Devices</div>
              </div>
              <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{deviceTypes.length}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>Device Types</div>
              </div>
            </div>
          )}
          {loaded && devices.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h3 style={{ color: "#c5f3ff", fontSize: 13, marginBottom: 8 }}>DEVICE INVENTORY</h3>
              <div style={{ maxHeight: 400, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
                {devices.map((d) => (
                  <div key={d.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
                    <span style={{ color: "#c5f3ff", fontSize: 12 }}>{d.name}</span>
                    <span style={{ color: "var(--muted)", fontSize: 11 }}>{d.device_type}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}