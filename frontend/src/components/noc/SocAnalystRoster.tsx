"use client";

import Link from "next/link";
import { ShieldCheck, UserCheck, UserX, AlertTriangle } from "lucide-react";
import { useSocAnalysts, useSocAnalystSummary } from "@/hooks/useNocQueries";

function AvailabilityBadge({ availability }: { availability: string }) {
  const color =
    availability === "available"
      ? "var(--green, #2ecc71)"
      : availability === "busy"
        ? "var(--amber, #f5a623)"
        : "var(--muted)";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />
      {availability}
    </span>
  );
}

export default function SocAnalystRoster() {
  const { data: analysts, isLoading, isError, refetch } = useSocAnalysts();
  const { data: summary } = useSocAnalystSummary();

  if (isLoading && !analysts) {
    return <p style={{ color: "var(--muted)" }}>Loading SOC roster…</p>;
  }

  if (isError && !analysts) {
    return (
      <div>
        <p style={{ color: "var(--red, #ff5c5c)" }}>SOC roster unavailable.</p>
        <button
          onClick={() => refetch()}
          style={{
            border: "1px solid var(--line)",
            background: "rgba(1,10,17,0.8)",
            color: "var(--cyan)",
            padding: "8px 16px",
            cursor: "pointer",
            marginTop: 12,
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  const items = analysts ?? [];

  return (
    <div className="soc-analyst-roster">
      {/* Summary strip */}
      {summary && (
        <div
          style={{
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
            marginBottom: 12,
            padding: "8px 12px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            background: "rgba(1,10,17,0.55)",
          }}
        >
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <ShieldCheck size={13} style={{ color: "var(--cyan)" }} />
            <span style={{ color: "var(--muted)", fontSize: 11 }}>TOTAL </span>
            <span style={{ color: "var(--cyan)", fontWeight: 700 }}>{summary.total_analysts}</span>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <UserCheck size={13} style={{ color: "var(--green, #2ecc71)" }} />
            <span style={{ color: "var(--muted)", fontSize: 11 }}>AVAILABLE </span>
            <span style={{ color: "var(--green, #2ecc71)", fontWeight: 700 }}>{summary.available_analysts}</span>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <UserX size={13} style={{ color: "var(--amber, #f5a623)" }} />
            <span style={{ color: "var(--muted)", fontSize: 11 }}>BUSY </span>
            <span style={{ color: "var(--amber, #f5a623)", fontWeight: 700 }}>{summary.busy_analysts}</span>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <AlertTriangle size={13} style={{ color: "var(--red, #ff5c5c)" }} />
            <span style={{ color: "var(--muted)", fontSize: 11 }}>UNASSIGNED EVENTS </span>
            <span style={{ color: "var(--red, #ff5c5c)", fontWeight: 700 }}>{summary.unassigned_open_events}</span>
          </div>
        </div>
      )}

      {/* Roster list */}
      {items.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>No SOC analysts registered.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {items.map((analyst) => (
            <Link
              key={analyst.id}
              href={`/soc-analyst-roster/${analyst.id}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "8px 12px",
                border: "1px solid var(--line)",
                borderRadius: 6,
                background: "rgba(1,10,17,0.55)",
                textDecoration: "none",
                color: "inherit",
                transition: "border-color 0.15s",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, color: "#ccecf4" }}>{analyst.name}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>
                  {analyst.agent_id} · {analyst.level} · {analyst.tasks_completed} resolved
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 12, color: "var(--cyan)", fontWeight: 700 }}>
                  {analyst.open_noc_events + analyst.acknowledged_noc_events} active
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>
                  {analyst.total_assigned_events} total
                </div>
              </div>
              <AvailabilityBadge availability={analyst.availability} />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
