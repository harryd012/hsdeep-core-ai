"use client";

import { ShieldCheck, Users, UserCheck, UserX, AlertTriangle, BarChart3 } from "lucide-react";
import type { SocAnalystSummary, NocEventSummary } from "@/lib/noc";

function StatCard({
  icon: Icon,
  label,
  value,
  color = "var(--cyan)",
}: {
  icon: React.ComponentType<{ size: number; style?: React.CSSProperties }>;
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "12px 14px",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        flex: 1,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--muted)", letterSpacing: "0.06em" }}>
        <Icon size={12} style={{ color }} />
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: "#fff" }}>{value}</div>
    </div>
  );
}

export default function SocMetricsPanel({
  analystSummary,
  eventSummary,
}: {
  analystSummary?: SocAnalystSummary;
  eventSummary?: NocEventSummary;
}) {
  return (
    <div
      className="soc-metrics-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "12px 14px",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12,
          fontWeight: 700,
          letterSpacing: "0.08em",
          color: "var(--text-dim)",
          textTransform: "uppercase",
        }}
      >
        <BarChart3 size={14} style={{ color: "var(--cyan)" }} />
        SOC Metrics
      </div>

      {analystSummary ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <StatCard icon={Users} label="Total Analysts" value={analystSummary.total_analysts} />
          <StatCard icon={UserCheck} label="Available" value={analystSummary.available_analysts} color="var(--green)" />
          <StatCard icon={ShieldCheck} label="Busy" value={analystSummary.busy_analysts} color="var(--amber)" />
          <StatCard icon={UserX} label="Offline" value={analystSummary.offline_analysts} color="var(--muted)" />
          <StatCard icon={BarChart3} label="Success Rate" value={`${analystSummary.avg_success_rate}%`} color="var(--violet)" />
        </div>
      ) : (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Loading analyst metrics…</p>
      )}

      {eventSummary ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
          <StatCard icon={AlertTriangle} label="Total Events" value={eventSummary.total} />
          <StatCard icon={AlertTriangle} label="Critical" value={eventSummary.by_severity.critical} color="var(--red)" />
          <StatCard icon={AlertTriangle} label="Unassigned Open" value={eventSummary.unassigned_open} color="var(--amber)" />
          {eventSummary.avg_resolution_minutes != null && (
            <StatCard icon={ShieldCheck} label="Avg Resolution" value={`${Math.round(eventSummary.avg_resolution_minutes)}m`} color="var(--green)" />
          )}
        </div>
      ) : (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Loading event summary…</p>
      )}
    </div>
  );
}
