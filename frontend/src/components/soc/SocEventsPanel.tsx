"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle, Clock, List } from "lucide-react";
import { useNocEvents } from "@/hooks/useNocQueries";
import type { NocEventSeverity, NocEventStatus, NocEventSummary } from "@/lib/noc";

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

const STATUS_ICON: Record<NocEventStatus, React.ComponentType<{ size: number; style?: React.CSSProperties }>> = {
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

export default function SocEventsPanel({
  summary,
}: {
  summary?: NocEventSummary;
}) {
  const { data, isLoading, isError, refetch } = useNocEvents({ status: "open" }, 50, 0);

  const items = data?.items ?? [];

  return (
    <div
      className="soc-events-panel"
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
        <List size={14} style={{ color: "var(--cyan)" }} />
        Open Events — Analysis Queue
      </div>

      {summary && (
        <div
          style={{
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
            fontSize: 11,
            padding: "6px 10px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            background: "rgba(1,10,17,0.55)",
          }}
        >
          <div>
            <span style={{ color: "var(--muted)" }}>TOTAL </span>
            <span style={{ color: "var(--cyan)", fontWeight: 700 }}>{summary.total}</span>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>CRIT </span>
            <span style={{ color: "var(--red)", fontWeight: 700 }}>{summary.by_severity.critical}</span>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>WARN </span>
            <span style={{ color: "var(--amber)", fontWeight: 700 }}>{summary.by_severity.warning}</span>
          </div>
          <div>
            <span style={{ color: "var(--muted)" }}>UNASSIGNED </span>
            <span style={{ color: summary.unassigned_open > 0 ? "var(--amber)" : "var(--green)", fontWeight: 700 }}>
              {summary.unassigned_open}
            </span>
          </div>
        </div>
      )}

      {isLoading && !data && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Loading SOC events…</p>
      )}

      {isError && !data && (
        <div style={{ color: "var(--red)" }}>
          <p style={{ fontSize: 13 }}>SOC events unavailable.</p>
          <button
            onClick={() => refetch()}
            style={{
              border: "1px solid var(--line)",
              background: "rgba(1,10,17,0.8)",
              color: "var(--cyan)",
              padding: "6px 12px",
              cursor: "pointer",
              marginTop: 6,
              fontSize: 12,
            }}
          >
            Retry
          </button>
        </div>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>No open SOC events.</p>
      )}


      {!isLoading && items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
          {items.slice(0, 20).map((event) => {
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
          {items.length > 20 && (
            <p style={{ color: "var(--muted)", fontSize: 11 }}>
              +{items.length - 20} more (showing top 20)
            </p>
          )}
        </div>
      )}
    </div>
  );
}

