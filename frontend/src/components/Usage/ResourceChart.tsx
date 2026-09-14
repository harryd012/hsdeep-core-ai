import React from "react";

interface ResourceChartProps {
  data: { label: string; value: number; total: number }[];
  height?: number;
}

export default function ResourceChart({ data, height = 200 }: ResourceChartProps) {
  if (!data.length) {
    return (
      <div style={{ height, border: "1px dashed var(--line)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>
        No resource data
      </div>
    );
  }
  const max = Math.max(...data.map((d) => d.total), 1);
  return (
    <div style={{ height, display: "flex", flexDirection: "column", gap: 10, overflowY: "auto" }}>
      {data.map((d, i) => {
        const pct = d.total > 0 ? (d.value / d.total) * 100 : 0;
        return (
          <div key={i}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: "#c5f3ff" }}>{d.label}</span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>{d.value} / {d.total}</span>
            </div>
            <div style={{ width: "100%", background: "rgba(255,255,255,0.05)", borderRadius: 4, height: 10, overflow: "hidden" }}>
              <div
                style={{ width: `${Math.min(pct, 100)}%`, height: "100%", backgroundColor: "var(--cyan)", borderRadius: 4 }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}