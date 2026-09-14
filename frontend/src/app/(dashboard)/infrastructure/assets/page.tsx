"use client";

import { useEffect, useState } from "react";
import { AssetDevice, AssetSite, listAssetDevices, listAssetSites } from "@/lib/api";

export default function AssetInventoryPage() {
  const [devices, setDevices] = useState<AssetDevice[]>([]);
  const [sites, setSites] = useState<AssetSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [devs, sts] = await Promise.all([
          listAssetDevices(undefined, { limit: 100 }),
          listAssetSites(undefined, { limit: 100 }),
        ]);
        if (!cancelled) {
          setDevices(devs);
          setSites(sts);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load assets");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = window.setInterval(load, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const statusColor = (status: string) => {
    switch (status) {
      case "up": return "var(--green)";
      case "down": return "var(--red)";
      case "warning": return "var(--amber)";
      default: return "var(--muted)";
    }
  };

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>ASSET INVENTORY</h2>
          {error && <p style={{ color: "var(--amber)", fontSize: 12 }}>{error}</p>}
          {loading && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading assets…</p>}
          {!loading && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginBottom: 16 }}>
                <div style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                  <div style={{ fontSize: 24, color: "var(--cyan)", fontWeight: 700 }}>{devices.length}</div>
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>DEVICES</div>
                </div>
                <div style={{ textAlign: "center", padding: 16, border: "1px solid var(--line)", borderRadius: 8 }}>
                  <div style={{ fontSize: 24, color: "var(--cyan)", fontWeight: 700 }}>{sites.length}</div>
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>SITES</div>
                </div>
              </div>
              <div style={{ overflowY: "auto", maxHeight: 500 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--cyan)" }}>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>Name</th>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>Type</th>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>Environment</th>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>Status</th>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>IP</th>
                      <th style={{ textAlign: "left", padding: "8px 8px" }}>Site</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((d) => {
                      const site = sites.find(s => s.id === d.site_id);
                      return (
                        <tr key={d.id} style={{ borderBottom: "1px solid rgba(0,217,255,.08)" }}>
                          <td style={{ padding: "8px 8px" }}>{d.name}</td>
                          <td style={{ padding: "8px 8px", color: "var(--muted)" }}>{d.device_type}</td>
                          <td style={{ padding: "8px 8px", color: "var(--muted)" }}>{d.environment ?? "—"}</td>
                          <td style={{ padding: "8px 8px" }}>
                            <span style={{ color: statusColor(d.status), fontWeight: 600 }}>{d.status}</span>
                          </td>
                          <td style={{ padding: "8px 8px", color: "var(--muted)" }}>{d.ip_address ?? "—"}</td>
                          <td style={{ padding: "8px 8px", color: "var(--muted)" }}>{site?.name ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
