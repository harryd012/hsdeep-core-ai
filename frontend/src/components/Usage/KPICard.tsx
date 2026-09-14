import React from "react";

interface KPICardProps {
  label: string;
  value: string;
  subtitle?: string;
  color?: "cyan" | "green" | "red" | "amber";
}

export default function KPICard({ label, value, subtitle, color = "cyan" }: KPICardProps) {
  const colorMap: Record<string, string> = {
    cyan: "var(--cyan)",
    green: "var(--green)",
    red: "var(--red)",
    amber: "var(--amber)",
  };
  return (
    <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
      <div style={{ fontSize: 28, color: colorMap[color] || colorMap.cyan, fontWeight: 700 }}>{value}</div>
      <div style={{ color: "var(--muted)", fontSize: 12 }}>{label}</div>
      {subtitle && <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 4 }}>{subtitle}</div>}
    </div>
  );
}