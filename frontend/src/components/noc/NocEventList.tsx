"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle, Clock, Info } from "lucide-react";
import { useNocEvents, useNocEventSummary } from "@/hooks/useNocQueries";
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

function SeverityBadge({ severity }: { severity: NocEventSeverity }) {
  return (
    <span
      style={{
        color: SEVERITY_COLOR[severity],
        fontSize: 11,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
      }}
    >
      {severity}
    </span>
  );
}

export default function NocEventList() {
  const { data, isLoading, isError, refetch } = useNocEvents({}, 50, 0);
  const { data: summary } = useNocEventSummary();

  if (isLoading && !data) {
    return <p style={{ color: "var(--muted)" }}>Loading NOC events…</p>;
  }

  if (isError && !data) {
    return (
      <div>
        <p style={{ color: "var(--red, #ff5c5c)" }}>NOC events unavailable.</p>
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

  const items = data?.items ?? [];

  return (
    <div className="noc-event-list">
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
          <div>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>TOTAL </span>
            <span style={{ color: "var(--cyan)", fontWeight: 700 }}>{summary.total}</span>
          </div>
          <div>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>CRIT </span>
            <span style={{ color: "var(--red, #ff5c5c)", fontWeight: 700 }}>
              {summary.by_severity.critical}
            </span>
          </div>
          <div>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>WARN </span>
            <span style={{ color: "var(--amber, #f5a623)", fontWeight: 700 }}>
              {summary.by_severity.warning}
            </span>
          </div>
          <div>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>UNASSIGNED </span>
            <span style={{ color: "var(--amber, #f5a623)", fontWeight: 700 }}>
              {summary.unassigned_open}
            </span>
          </div>
        </div>
      )}

      {/* Event list */}
      {items.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>No NOC events recorded.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {items.map((event) => {
            const StatusIcon = STATUS_ICON[event.status];
            return (
              <Link
                key={event.id}
                href={`/noc-event-log/${event.id}`}
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
                <StatusIcon size={14} style={{ color: STATUS_COLOR[event.status] }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: "#ccecf4", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {event.title}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    {event.source} · {event.site_name || event.device_name || "—"}
                    {event.assigned_analyst_name ? ` · ${event.assigned_analyst_name}` : " · unassigned"}
                  </div>
                </div>
                <SeverityBadge severity={event.severity} />
                <span style={{ fontSize: 11, color: STATUS_COLOR[event.status], textTransform: "uppercase" }}>
                  {event.status}
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
