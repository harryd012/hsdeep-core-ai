"use client";

import Link from "next/link";
import { ArrowLeft, AlertTriangle, Clock, CheckCircle, MapPin, Cpu } from "lucide-react";
import { useNocEvent } from "@/hooks/useNocQueries";
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

const STATUS_ICON: Record<NocEventStatus, typeof AlertTriangle> = {
  open: AlertTriangle,
  acknowledged: Clock,
  resolved: CheckCircle,
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

export default function NocEventDetail({ eventId }: { eventId: string }) {
  const { data: event, isLoading, isError, refetch } = useNocEvent(eventId);

  if (isLoading && !event) {
    return <p style={{ color: "var(--muted)" }}>Loading event…</p>;
  }

  if (isError && !event) {
    return (
      <div>
        <p style={{ color: "var(--red, #ff5c5c)" }}>Event unavailable.</p>
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

  if (!event) {
    return <p style={{ color: "var(--muted)" }}>Event not found.</p>;
  }

  const StatusIcon = STATUS_ICON[event.status];

  return (
    <div className="noc-event-detail">
      {/* Back link */}
      <Link
        href="/noc-event-log"
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
        <ArrowLeft size={14} /> Back to NOC events
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
        <StatusIcon size={20} style={{ color: STATUS_COLOR[event.status], marginTop: 2 }} />
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 16, color: "#ccecf4" }}>{event.title}</h2>
          {event.description && (
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--muted)" }}>
              {event.description}
            </p>
          )}
          <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: SEVERITY_COLOR[event.severity], fontWeight: 700, textTransform: "uppercase" }}>
              {event.severity}
            </span>
            <span style={{ fontSize: 12, color: STATUS_COLOR[event.status], fontWeight: 700, textTransform: "uppercase" }}>
              {event.status}
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {event.occurrence_count > 1 ? `${event.occurrence_count} occurrences` : "1 occurrence"}
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
          margin: 0,
          padding: "12px 16px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "rgba(1,10,17,0.55)",
        }}
      >
        <Field label="Source" value={event.source} />
        <Field label="Category" value={event.category} />
        <Field label="Triggered" value={event.triggered_at ? new Date(event.triggered_at).toLocaleString() : "—"} />
        <Field label="Last Occurred" value={event.last_occurred_at ? new Date(event.last_occurred_at).toLocaleString() : "—"} />
        <Field
          label="Assigned Analyst"
          value={
            event.assigned_analyst_id ? (
              <Link href={`/soc-analyst-roster/${event.assigned_analyst_id}`} style={{ color: "var(--cyan)" }}>
                {event.assigned_analyst_name || event.assigned_analyst_id}
              </Link>
            ) : (
              <span style={{ color: "var(--amber, #f5a623)" }}>Unassigned</span>
            )
          }
        />
        <Field label="Confidence" value={event.confidence_score !== null ? `${Math.round(event.confidence_score)}%` : "—"} />
        {event.site_name && (
          <Field
            label="Site"
            value={
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <MapPin size={12} style={{ color: "var(--muted)" }} />
                {event.site_name}
              </span>
            }
          />
        )}
        {event.device_name && (
          <Field
            label="Device"
            value={
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <Cpu size={12} style={{ color: "var(--muted)" }} />
                {event.device_name}
              </span>
            }
          />
        )}
        <Field label="Resolved" value={event.resolved_at ? new Date(event.resolved_at).toLocaleString() : "—"} />
      </dl>
    </div>
  );
}
