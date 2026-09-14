import React from "react";

interface UsageTrendChartProps {
  data: { label: string; value: number }[];
  height?: number;
  color?: string;
}

export default function UsageTrendChart({ data, height = 200, color = "#00ffcc" }: UsageTrendChartProps) {
  if (!data.length) {
    return (
      <div style={{ height, border: "1px dashed var(--line)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>
        No usage data available
      </div>
    );
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div style={{ height, display: "flex", alignItems: "end", gap: 4, overflowX: "auto" }}>
      {data.map((d, i) => {
        const pct = (d.value / max) * 100;
        return (
          <div key={i} style={{ flex: "1 0 24px", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "end", height: "100%" }}>
            <div
              style={{ width: "100%", backgroundColor: color, borderRadius: "2px 2px 0 0", height: `${Math.max(pct, 2)}%`, minHeight: 2 }}
              title={`${d.label}: ${d.value}`}
            />
            <span style={{ fontSize: 10, color: "var(--muted)", marginTop: 4, whiteSpace: "nowrap" }}>{d.label}</span>
          </div>
        );
      })}
    </div>
  );
}