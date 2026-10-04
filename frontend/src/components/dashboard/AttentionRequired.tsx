"use client";

import Link from "next/link";
import { memo } from "react";
import type { CSSProperties } from "react";
import type {
  AttentionDeviceRow,
  AttentionIncidentRow,
  AttentionRequiredData,
  SectionState,
} from "@/lib/dashboard/dashboardModels";
import type { CommandKpi } from "@/lib/dashboard/commandKpis";
import CommandKpiCards from "./CommandKpiCards";

/**
 * AttentionRequired — the top of the portal, ordered by what an operator has to
 * decide, so the first screen answers "what needs me?" before anything else.
 *
 * PHASE 2 level mapping (the captions rendered below are these levels):
 *   1-3  ATTENTION / IMPACT / HEALTH — the normalized PHASE 1 KPI strip; every
 *        card is label + value + state + context + click target
 *   4    WHY      — the incidents and devices BEHIND those counters, with the
 *        measured evidence attached to each row (member alerts, occurrences, RCA)
 *   5    EVIDENCE — aggregates the BACKEND computes, with its own definitions
 *        attached (correlation, noise reduction, collection-pipeline health)
 *   6    OPERATIONS — the queues where the work actually happens
 *   7    AI       — last on purpose: assistance, never a competing signal
 *
 * Presentational only (no fetching) and honest by construction: a counter the
 * backend did not answer renders "N/A" with the reason, never a 0. A measured 0
 * is a real answer and stays 0. UNKNOWN is never collapsed into DOWN: a device
 * with no reported status is not counted as degraded, and a collector with no
 * run in the window reads "unknown", not "failing".
 *
 * Every destination is an EXISTING route (no duplicate pages):
 *   KPI cards        -> /incidents (?severity= for the severity bands), /alerts,
 *                       /infrastructure, /monitoring, /ai-copilot
 *   incident rows    -> /incidents/{id}
 *   degraded devices -> /infrastructure/assets/{device id}
 *   evidence         -> /monitoring
 *   operations / AI  -> /incidents, /alerts, /changes, /automation, /ai-copilot
 *
 * Data ownership (see buildAttentionRequired in dashboardDataAdapter):
 *   CRITICAL INCIDENTS    <- listIncidents, open, severity critical
 *   HIGH PRIORITY         <- listIncidents, open, severity warning
 *   DEGRADED SYSTEMS      <- getDevices, status in the degraded set
 *   UNACKNOWLEDGED ALERTS <- listAlerts(open), acknowledged_at === null
 */

/** Severity -> the dashboard's existing colour tokens. */
function severityColor(severity: string): string {
  switch (severity.toLowerCase()) {
    case "critical":
      return "var(--red)";
    case "warning":
    case "high":
      return "var(--amber)";
    case "info":
      return "var(--information)";
    default:
      return "var(--muted)";
  }
}

/** A backend aggregate that could not be read renders N/A, never 0. */
function evidenceValue(value: number | null, suffix = ""): string {
  return value === null ? "N/A" : `${value.toLocaleString()}${suffix}`;
}

/**
 * LEVEL 6 - OPERATIONS. The queues where the work identified above actually
 * happens. Every href is an EXISTING route and none of them performs an action:
 * they are navigation shortcuts, never destructive operations.
 */
const OPERATIONS_ACTIONS = [
  { href: "/incidents", label: "Work the incident queue", hint: "Acknowledge - investigate - resolve" },
  { href: "/alerts", label: "Acknowledge open alerts", hint: "Clear the unacknowledged queue" },
  { href: "/changes", label: "Review the change queue", hint: "Requested and in-flight changes" },
  { href: "/automation", label: "Automation jobs", hint: "Runs, outcomes and SOP execution" },
] as const;

/**
 * LEVEL 7 - AI. Deliberately LAST, after every measured signal: assistance, not
 * a competing source of truth. The copilot answers from the backend AI service;
 * nothing is generated in the frontend.
 */
const AI_ACTIONS = [
  { href: "/ai-copilot", label: "Ask the AI copilot", hint: "What needs attention - why - what changed" },
] as const;

/** A restrained level marker so the hierarchy reads at a glance. */
function LevelCaption({ children }: { children: string }) {
  return (
    <h3
      style={{
        margin: "20px 0 0",
        paddingTop: 10,
        borderTop: "1px solid var(--line)",
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: 1.1,
        textTransform: "uppercase",
        color: "var(--muted)",
      }}
    >
      {children}
    </h3>
  );
}


/** Navigation shortcuts. These are links only - no action is executed here. */
function ActionLinks({
  actions,
}: {
  actions: readonly { href: string; label: string; hint: string }[];
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginTop: 12 }}>
      {actions.map((action) => (
        <Link
          key={action.href}
          href={action.href}
          style={{ display: "grid", gap: 2, padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 6, textDecoration: "none" }}
        >
          <span style={{ fontSize: 13, fontWeight: 600 }}>{action.label}</span>
          <small style={{ fontSize: 11, color: "var(--muted)" }}>{action.hint}</small>
        </Link>
      ))}
    </div>
  );
}

function rowStyle(accent: string): CSSProperties {
  return {
    display: "flex",
    gap: 10,
    alignItems: "baseline",
    padding: "8px 10px",
    border: "1px solid var(--line)",
    borderLeft: `3px solid ${accent}`,
    borderRadius: 6,
  };
}

/**
 * One incident behind the counters, linked to the existing investigation
 * workspace at /incidents/{id}. The row keeps every measured field it already
 * carried (member alerts, occurrences, recorded RCA, asset, status, age) and is
 * a real link, so it is keyboard reachable with a visible focus ring.
 */
function IncidentRows({ rows }: { rows: AttentionIncidentRow[] }) {
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
      {rows.map((row) => (
        <li key={row.id}>
          <Link
            href={`/incidents/${row.id}`}
            style={{ ...rowStyle(severityColor(row.severity)), textDecoration: "none", color: "inherit" }}
            aria-label={`Open incident ${row.title}, severity ${row.severity}, status ${row.status}`}
          >
            <b style={{ fontSize: 11, letterSpacing: 0.4, textTransform: "uppercase", color: severityColor(row.severity) }}>
              {row.severity}
            </b>
            <span style={{ flex: 1, fontSize: 13 }}>
              {row.title}
              {/* EVIDENCE / IMPACT / RCA — all measured fields from listIncidents. */}
              <span style={{ display: "block", fontSize: 11, color: "var(--muted)" }}>
                {row.correlated ? "correlated incident" : "single alert"} ·{" "}
                {row.memberAlerts} alert(s) · {row.occurrences} occurrence(s) ·{" "}
                {row.rca ? `RCA: ${row.rca.length > 90 ? `${row.rca.slice(0, 90)}…` : row.rca}` : "no RCA recorded"}
              </span>
            </span>
            {row.asset && <small style={{ fontSize: 12, color: "var(--muted)" }}>{row.asset}</small>}
            <small style={{ fontSize: 12, color: "var(--muted)" }}>{row.status}</small>
            <small style={{ fontSize: 12, color: "var(--muted)" }}>{row.ageLabel}</small>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/**
 * One degraded system, linked to its own existing detail page (LEVEL 4 detail).
 *
 * The row is built from a `Device` returned by the device API, so `id` is the
 * authoritative record id and the link uses it. The displayed `name` is often an
 * IP address (10.10.13.0, 192.168.159.10, ...) — an IP is never assumed to BE the
 * device, and never used as a route parameter. `type` is the backend's own
 * device_type, shown as the resource type; when the backend reports none, the row
 * says so rather than inventing a class.
 */
function DeviceRows({ rows }: { rows: AttentionDeviceRow[] }) {
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
      {rows.map((row) => (
        <li key={row.id}>
          <Link
            href={`/infrastructure/assets/${row.id}`}
            style={{ ...rowStyle("var(--amber)"), textDecoration: "none", color: "inherit" }}
            aria-label={`Open device ${row.name}${row.type ? `, type ${row.type}` : ""}, current status ${row.status}`}
          >
            <span style={{ flex: 1, fontSize: 13 }}>{row.name}</span>
            <small style={{ fontSize: 12, color: "var(--muted)" }}>
              {row.type || "type not reported"}
            </small>
            <b style={{ fontSize: 11, letterSpacing: 0.4, textTransform: "uppercase", color: "var(--amber)" }}>
              {row.status}
            </b>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function EvidenceBlock({
  title,
  rows,
  href,
  linkLabel,
}: {
  title: string;
  rows: { label: string; value: string; hint: string }[];
  /** Optional EXISTING route where this evidence can be acted on. */
  href?: string;
  linkLabel?: string;
}) {
  const unavailable = rows.every((row) => row.value === "N/A");
  return (
    <div className="hud-panel">
      <div className="panel-header"><h2>{title}</h2></div>
      <div className="panel-body">
        {unavailable ? (
          <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
            Not available — the aggregation endpoint did not respond.
          </p>
        ) : (
          <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10, margin: 0 }}>
            {rows.map((row) => (
              <div key={row.label}>
                <dt style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: 0.4 }}>{row.label}</dt>
                <dd style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>{row.value}</dd>
                {/* The backend's own definition, so the number cannot be misread. */}
                <small style={{ fontSize: 10, color: "var(--muted)" }}>{row.hint}</small>
              </div>
            ))}
          </dl>
        )}
        {href && linkLabel && (
          <p style={{ margin: "10px 0 0" }}>
            <Link href={href} style={{ fontSize: 12 }}>{linkLabel}</Link>
          </p>
        )}
      </div>
    </div>
  );
}

const AttentionRequired = memo(function AttentionRequired({
  data,
  kpis,
  state,
}: {
  data: AttentionRequiredData;
  /** Normalized KPI view model for the Level-1 strip (PHASE 1). */
  kpis: CommandKpi[];
  state: SectionState;
}) {
  return (
    <section id="attention" className="hs-zone hs-zone--attention" aria-labelledby="attention-title">
      <div className="hs-zone__header">
        <h2 id="attention-title" className="hs-zone__title">Attention required</h2>
        <p className="hs-zone__subtitle">
          What needs attention - why it needs it - what to do next
        </p>
      </div>

      {state === "loading" && <div className="op-health__status">Loading attention data…</div>}
      {state === "error" && (
        <div className="op-health__status op-health__status--error">
          Attention data unavailable - incidents, alerts and devices could not be read.
        </div>
      )}
      {state !== "loading" && state !== "error" && data.partial && (
        <div className="op-health__status">
          Some sources did not respond - those counters show N/A rather than 0.
        </div>
      )}

      {/* LEVEL 1-3 - what needs attention first, then what it is impacting and how
          the estate is holding up. The PHASE 1 normalized KPI strip carries title,
          value, state and context per KPI, and renders N/A when a source is silent. */}
      <LevelCaption>Levels 1-3 - Attention, impact and health</LevelCaption>
      <CommandKpiCards kpis={kpis} />

      <LevelCaption>Level 4 - Why</LevelCaption>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16, marginTop: 16 }}>
        <div className="hud-panel">
          <div className="panel-header"><h2>WHY - OPEN INCIDENTS</h2></div>
          <div className="panel-body">
            {data.incidents.length > 0
              ? <IncidentRows rows={data.incidents} />
              : <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
                  {data.metrics[0].value === null
                    ? "Not available - the incident API did not respond."
                    : "Nothing critical or high-priority is open."}
                </p>}
          </div>
        </div>

        <div className="hud-panel">
          <div className="panel-header"><h2>WHY - DEGRADED SYSTEMS</h2></div>
          <div className="panel-body">
            {data.degradedDevices.length > 0
              ? <DeviceRows rows={data.degradedDevices} />
              : <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
                  {data.metrics[2].value === null
                    ? "Not available - the device API did not respond."
                    : "No device is reporting a degraded or down state."}
                </p>}
          </div>
        </div>
      </div>

      <LevelCaption>Level 5 - Evidence</LevelCaption>

      {/* Numbers the BACKEND aggregates (no client maths). The collection-pipeline
          definitions are the authoritative PHASE 3.1 collector-health contract
          (CollectorHealthService over CollectorRun rows, 24h window):
            failed   = run status "error"   (status "partial" counts as SUCCESS)
            healthy  = no failed run in the window
            degraded = failure rate above 0 and below 50%
            failing  = failure rate 50% or more
            unknown  = no run recorded in the window (NOT a failure) */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16, marginTop: 16 }}>
        <EvidenceBlock
          title="EVIDENCE - ALERT CORRELATION"
          rows={[
            { label: "Active incidents", value: evidenceValue(data.evidence.activeIncidents), hint: "open + acknowledged + investigating" },
            { label: "Correlated incidents", value: evidenceValue(data.evidence.correlatedIncidents), hint: "open incidents with more than one alert" },
            { label: "Suppressed alerts", value: evidenceValue(data.evidence.suppressedAlerts), hint: "alerts stored as symptoms of another" },
            { label: "Noise reduction", value: evidenceValue(data.evidence.noiseReductionPct, "%"), hint: "suppressed / (open + suppressed)" },
          ]}
        />
        <EvidenceBlock
          title="EVIDENCE - COLLECTION PIPELINE"
          rows={[
            { label: "Healthy sources", value: evidenceValue(data.evidence.collectorsHealthy), hint: "no failed run in the window" },
            { label: "Degraded sources", value: evidenceValue(data.evidence.collectorsDegraded), hint: "some runs failed - under half" },
            { label: "Failing sources", value: evidenceValue(data.evidence.collectorsFailing), hint: "half or more of the runs failed" },
            { label: "Unknown state", value: evidenceValue(data.evidence.collectorsUnknown), hint: "no run recorded - not a failure" },
          ]}
          href="/monitoring"
          linkLabel="Open monitoring sources"
        />
      </div>

      <LevelCaption>Level 6 - Operations</LevelCaption>
      <ActionLinks actions={OPERATIONS_ACTIONS} />

      <LevelCaption>Level 7 - AI</LevelCaption>
      <ActionLinks actions={AI_ACTIONS} />
    </section>
  );
});

export default AttentionRequired;
