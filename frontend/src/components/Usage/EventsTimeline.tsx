import React from "react";

interface EventsTimelineProps {
  data: { time: string; label: string; count: number }[];
  height?: number;
}

export default function EventsTimeline({ data, height = 200 }: EventsTimelineProps) {
  if (!data.length) {
    return (
      <div style={{ height, border: "1px dashed var(--line)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>
        No events data
      </div>
    );
  }
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div style={{ height, display: "flex", alignItems: "end", gap: 2, overflowX: "auto" }}>
      {data.map((d, i) => {
        const pct = (d.count / max) * 100;
        return (
          <div key={i} style={{ flex: "1 0 20px", display: "flex", flexDirection: "column", alignItems: "center", height: "100%", justifyContent: "end" }}>
            <div
              style={{ width: "100%", backgroundColor: "var(--amber)", borderRadius: "2px 2px 0 0", height: `${Math.max(pct, 2)}%`, minHeight: 2 }}
              title={`${d.label}: ${d.count}`}
            />
            <span style={{ fontSize: 9, color: "var(--muted)", marginTop: 2, whiteSpace: "nowrap" }}>{d.time}</span>
          </div>
        );
      })}
    </div>
  );
}