"use client";

import Link from "next/link";
import { Shield, UserCheck, UserX, AlertTriangle } from "lucide-react";
import type { SocAnalyst } from "@/lib/noc";

function WorkloadBar({ analyst }: { analyst: SocAnalyst }) {
  // A utilisation percentage needs a real capacity denominator. When the roster
  // reports max_workload = 0 there is no ratio to display, so the bar stays
  // neutral and empty instead of inventing a figure - or a red "saturated"
  // colour - from a missing denominator.
  const hasCapacity = analyst.max_workload > 0;
  const pct = hasCapacity
    ? Math.min(100, (analyst.current_workload / analyst.max_workload) * 100)
    : null;
  const color = pct === null ? "var(--muted)" : pct >= 90 ? "var(--red)" : pct >= 70 ? "var(--amber)" : "var(--green)";

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>
        <span>{analyst.name}</span>
        <span>{hasCapacity ? `${analyst.current_workload}/${analyst.max_workload}` : `${analyst.current_workload} · no capacity set`}</span>
      </div>
      <div
        style={{
          height: 6,
          background: "rgba(148, 200, 230, 0.1)",
          borderRadius: 999,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: pct === null ? "0%" : `${pct}%`,
            height: "100%",
            background: color,
            borderRadius: 999,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 2, fontSize: 10, color: "var(--text-dim)" }}>
        <span>{analyst.open_noc_events} open</span>
        <span>{analyst.acknowledged_noc_events} ack</span>
        <span>{analyst.resolved_today} today</span>
      </div>
    </div>
  );
}

export default function SocTeamAllocationPanel({ analysts }: { analysts: SocAnalyst[] }) {
  const busy = analysts.filter((a) => a.availability === "busy");
  const available = analysts.filter((a) => a.availability === "available");
  const offline = analysts.filter((a) => a.availability === "offline");

  return (
    <div
      className="soc-team-allocation-panel"
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
        <Shield size={14} style={{ color: "var(--cyan)" }} />
        Team Allocation
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 4, fontSize: 11 }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--green)" }}>
          <UserCheck size={12} /> {available.length} available
        </div>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--amber)" }}>
          <Shield size={12} /> {busy.length} busy
        </div>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--muted)" }}>
          <UserX size={12} /> {offline.length} offline
        </div>
      </div>

      {analysts.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>No analysts to display.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          {analysts.map((analyst) => (
            <Link
              key={analyst.id}
              href={`/soc-analyst-roster/${analyst.id}`}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                padding: "8px 12px",
                border: "1px solid var(--line)",
                borderRadius: 8,
                background: "rgba(1,10,17,0.55)",
                textDecoration: "none",
                color: "inherit",
                transition: "border-color 0.15s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, minWidth: 0 }}>
                <span style={{ fontSize: 13, color: "#ccecf4", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {analyst.name}
                </span>
                <span style={{ fontSize: 10, color: "var(--muted)", border: "1px solid var(--line)", borderRadius: 4, padding: "1px 6px", flex: "none" }}>
                  {analyst.level}
                </span>
              </div>
              <WorkloadBar analyst={analyst} />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
