"use client";

import Link from "next/link";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { useSocAnalystDetail } from "@/hooks/useNocQueries";
import type { NocEventSeverity, NocEventStatus } from "@/lib/noc";

const SEVERITY_COLOR: Record<NocEventSeverity, string> = {
  critical: "var(--red, #ff5c5c)",
  warning: "var(--amber, #f5a623)",
  info: "var(--cyan, #00dcff)",
};

const STATUS_COLOR: Record<NocEventStatus, string> = {
  open: "var(--red, #ff5c5c)",
  acknowledged: "var(--amber, #f5a623)",
  resolved: "var(--green, #2ecc71)",
};

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </dt>
      <dd style={{ margin: "2px 0 0", fontSize: 13, color: "#ccecf4" }}>{value}</dd>
    </div>
  );
}

export default function SocAnalystDetail({ analystId }: { analystId: string }) {
  const { data: analyst, isLoading, isError, refetch } = useSocAnalystDetail(analystId);

  if (isLoading && !analyst) {
    return <p style={{ color: "var(--muted)" }}>Loading analyst…</p>;
  }

  if (isError && !analyst) {
    return (
      <div>
        <p style={{ color: "var(--red, #ff5c5c)" }}>Analyst unavailable.</p>
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

  if (!analyst) {
    return <p style={{ color: "var(--muted)" }}>Analyst not found.</p>;
  }

  const assignedEvents = analyst.assigned_events ?? [];

  return (
    <div className="soc-analyst-detail">
      {/* Back link */}
      <Link
        href="/soc-analyst-roster"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          color: "var(--cyan)",
          fontSize: 13,
          textDecoration: "none",
          marginBottom: 16,
        }}
      >
        <ArrowLeft size={14} /> Back to SOC roster
      </Link>

      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
          padding: "12px 16px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "rgba(1,10,17,0.55)",
          marginBottom: 16,
        }}
      >
        <ShieldCheck size={20} style={{ color: "var(--cyan)", marginTop: 2 }} />
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 16, color: "#ccecf4" }}>{analyst.name}</h2>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
            {analyst.agent_id} · {analyst.level} · {analyst.department}
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: "var(--cyan)", fontWeight: 700 }}>
              {analyst.open_noc_events + analyst.acknowledged_noc_events} active events
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {analyst.total_assigned_events} total assigned
            </span>
            <span style={{ fontSize: 12, color: "var(--green, #2ecc71)" }}>
              {Math.round(analyst.success_rate)}% success
            </span>
          </div>
        </div>
      </div>

      {/* Details grid */}
      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: 12,
          margin: "0 0 16px",
          padding: "12px 16px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "rgba(1,10,17,0.55)",
        }}
      >
        <Field label="Availability" value={analyst.availability} />
        <Field label="Status" value={analyst.status} />
        <Field label="Workload" value={`${analyst.current_workload}/${analyst.max_workload}`} />
        <Field label="Tasks Completed" value={analyst.tasks_completed} />
        <Field label="Tasks Failed" value={analyst.tasks_failed} />
        <Field label="Avg Resolution" value={analyst.avg_resolution_minutes ? `${analyst.avg_resolution_minutes} min` : "—"} />
        <Field label="Skills" value={analyst.skills.length > 0 ? analyst.skills.join(", ") : "—"} />
      </dl>

      {/* Assigned events */}
      <div
        style={{
          padding: "12px 16px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "rgba(1,10,17,0.55)",
        }}
      >
        <h3 style={{ margin: "0 0 8px", fontSize: 13, color: "var(--cyan)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Assigned NOC Events
        </h3>
        {assignedEvents.length === 0 ? (
          <p style={{ color: "var(--muted)", fontSize: 13 }}>No events assigned.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {assignedEvents.map((event) => (
              <Link
                key={event.id}
                href={`/noc-event-log/${event.id}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  borderRadius: 4,
                  background: "rgba(0,0,0,0.2)",
                  textDecoration: "none",
                  color: "inherit",
                }}
              >
                <span style={{ fontSize: 12, color: SEVERITY_COLOR[event.severity], fontWeight: 700, textTransform: "uppercase" }}>
                  {event.severity}
                </span>
                <span style={{ flex: 1, fontSize: 12, color: "#ccecf4", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {event.title}
                </span>
                <span style={{ fontSize: 11, color: STATUS_COLOR[event.status], textTransform: "uppercase" }}>
                  {event.status}
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
