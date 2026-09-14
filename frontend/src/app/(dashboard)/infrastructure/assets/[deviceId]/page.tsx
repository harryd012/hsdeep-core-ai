"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AssetDevice, AssetRelationship, getAssetDevice, getAssetDeviceRelationships } from "@/lib/api";

export default function AssetDetailsPage() {
  const params = useParams();
  const deviceId = params.deviceId as string;
  const [device, setDevice] = useState<AssetDevice | null>(null);
  const [relationships, setRelationships] = useState<AssetRelationship[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!deviceId) return;
    let cancelled = false;
    async function load() {
      try {
        const [dev, rels] = await Promise.all([
          getAssetDevice(deviceId),
          getAssetDeviceRelationships(deviceId),
        ]);
        if (!cancelled) {
          setDevice(dev);
          setRelationships(rels);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load asset details");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
  }, [deviceId]);

  const statusColor = (status: string) => {
    switch (status) {
      case "up": return "var(--green)";
      case "down": return "var(--red)";
      case "warning": return "var(--amber)";
      default: return "var(--muted)";
    }
  };

  if (loading) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--muted)" }}>Loading asset…</p></div>;
  if (error) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--amber)" }}>{error}</p></div>;
  if (!device) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--muted)" }}>Asset not found</p></div>;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>{device.name}</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginTop: 16 }}>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>STATUS</div>
              <span style={{ color: statusColor(device.status), fontWeight: 600, fontSize: 16 }}>{device.status}</span>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>TYPE</div>
              <div style={{ fontSize: 16 }}>{device.device_type}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>ENVIRONMENT</div>
              <div style={{ fontSize: 16 }}>{device.environment ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>CRITICALITY</div>
              <div style={{ fontSize: 16 }}>{device.criticality ?? "—"}</div>
            </div>
          </div>
        </section>

        <section className="hud-panel">
          <h3>NETWORK</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginTop: 12 }}>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>HOSTNAME</div>
              <div style={{ fontSize: 14 }}>{device.hostname ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>IP ADDRESS</div>
              <div style={{ fontSize: 14 }}>{device.ip_address ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>MAC ADDRESS</div>
              <div style={{ fontSize: 14 }}>{device.mac_address ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>VENDOR / MODEL</div>
              <div style={{ fontSize: 14 }}>{device.vendor ?? "—"} {device.model ? `/ ${device.model}` : ""}</div>
            </div>
          </div>
        </section>

        <section className="hud-panel">
          <h3>CMDB DETAILS</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginTop: 12 }}>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>BUSINESS OWNER</div>
              <div style={{ fontSize: 14 }}>{device.business_owner ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>SUPPORT TEAM</div>
              <div style={{ fontSize: 14 }}>{device.support_team ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>MAINTENANCE WINDOW</div>
              <div style={{ fontSize: 14 }}>{device.maintenance_window ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>TAGS</div>
              <div style={{ fontSize: 14 }}>{(device.tags ?? []).join(", ") || "—"}</div>
            </div>
          </div>
        </section>

        {relationships.length > 0 && (
          <section className="hud-panel">
            <h3>RELATIONSHIPS ({relationships.length})</h3>
            <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
              {relationships.map((rel) => (
                <div key={rel.id} style={{ padding: "8px 12px", border: "1px solid var(--line)", borderRadius: 4, fontSize: 13 }}>
                  <span style={{ color: "var(--cyan)" }}>{rel.relationship_type}</span>
                  {rel.label && <span style={{ color: "var(--muted)", marginLeft: 8 }}>({rel.label})</span>}
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
