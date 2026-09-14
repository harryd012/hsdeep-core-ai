"use client";

import { X, ExternalLink } from "lucide-react";

interface UsageDrilldownProps {
  resource: string;
  used: number;
  limit: number;
  onClose: () => void;
}

/**
 * Side panel showing the actual items counted for a usage metric.
 * Reuses Infrastructure page data where available.
 */
export default function UsageDrilldown({ resource, used, limit, onClose }: UsageDrilldownProps) {
  // TODO: fetch actual items from backend (e.g., GET /api/devices for "Devices")
  const items: string[] = [];

  return (
    <div style={{
      position: "fixed",
      top: 0,
      right: 0,
      bottom: 0,
      width: 380,
      maxWidth: "90vw",
      background: "rgba(7, 19, 27, 0.98)",
      borderLeft: "1px solid var(--line)",
      zIndex: 200,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "var(--text)" }}>{resource}</h3>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>{used} of {limit} used</span>
        </div>
        <button onClick={onClose} aria-label="Close" style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", padding: 4 }}>
          <X size={18} />
        </button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
        {items.length === 0 ? (
          <div style={{ textAlign: "center", padding: "40px 16px" }}>
            <p style={{ fontSize: 13, color: "var(--muted)", margin: "0 0 12px" }}>
              Item details will appear here once the backend endpoint is available.
            </p>
            <a href="/infrastructure" style={{ fontSize: 12, color: "var(--cyan)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
              View in Infrastructure <ExternalLink size={11} />
            </a>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {items.map((item, i) => (
              <div key={i} style={{ padding: "10px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "rgba(2,12,27,0.4)", fontSize: 12 }}>
                {item}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
