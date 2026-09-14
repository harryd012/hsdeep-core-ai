import React from "react";

interface CollectorChartProps {
  data: { label: string; value: number; max: number }[];
  height?: number;
}

export default function CollectorChart({ data, height = 200 }: CollectorChartProps) {
  if (!data.length) {
    return (
      <div style={{ height, border: "1px dashed var(--line)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>
        No collector data
      </div>
    );
  }
  return (
    <div style={{ height, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto" }}>
      {data.map((d, i) => {
        const pct = d.max > 0 ? (d.value / d.max) * 100 : 0;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 100, fontSize: 11, color: "var(--muted)", textAlign: "right" }}>{d.label}</div>
            <div style={{ flex: 1, background: "rgba(255,255,255,0.05)", borderRadius: 4, height: 12, overflow: "hidden" }}>
              <div
                style={{ width: `${Math.min(pct, 100)}%`, height: "100%", backgroundColor: pct > 90 ? "var(--red)" : pct > 70 ? "var(--amber)" : "var(--cyan)", borderRadius: 4 }}
              />
            </div>
            <div style={{ width: 60, fontSize: 11, color: "var(--muted)", textAlign: "right" }}>{d.value}</div>
          </div>
        );
      })}
    </div>
  );
}