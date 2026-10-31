"use client";

/**
 * /portal/operations — OPERATIONS CENTER V1.
 *
 * Answers: what is open, which alerts need attention, which incidents are active,
 * which operational systems are available, and what to open next.
 *
 * Real data only. Counts come from actual API responses and every table states
 * its bounded scope - nothing is hard-coded and no count is presented as a
 * complete tenant total the API did not return.
 *
 * Performance: two BOUNDED list calls issued once in parallel. No per-row detail
 * requests, no polling, no per-keystroke requests. A failure in one API degrades
 * only its own section.
 *
 * Tenant context stays with the existing authenticated API client; this page
 * never accepts or sends a tenant id.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PortalShell,
  ServiceCard,
} from "@/components/portal/PortalShell";
import { listAlerts, listIncidents, type Alert, type Incident } from "@/lib/api";

/** Bounded window for infra/alerts. Counts are labelled as coming from this
 *  window - never sold as a global total the API did not return. */
const WINDOW = 100;

/**
 * Incidents are the widest rows in the product (~2 KB each: RCA summary,
 * supporting evidence, denormalized names). Measured on the real database, the
 * authenticated `GET /api/incidents?limit=100` took 4.6-6.9s - a large share of
 * the API client's 10s `REQUEST_TIMEOUT_MS` budget, and it exceeds that budget
 * under concurrent load (the Command Center fires 5 parallel requests and the
 * dashboard can issue 28). 25 keeps the Operations fetch well inside the 10s
 * ceiling so incidents render reliably even on a cold cache. Mirrors the
 * Command Center's `INCIDENT_WINDOW` (see app/(portal)/portal/page.tsx).
 */
const INCIDENT_WINDOW = 25;

type Loaded<T> = T[] | null; // null = the API did not answer (unavailable)

/** Resolves to null instead of throwing, so one failing API cannot blank the
 *  other section. Local mirror of the Command Center's helper (not exported). */
async function settle<T>(p: Promise<T[]>): Promise<Loaded<T>> {
  try {
    return await p;
  } catch {
    return null;
  }
}

export default function OperationsPage() {
  const [alerts, setAlerts] = useState<Loaded<Alert>>(null);
  const [incidents, setIncidents] = useState<Loaded<Incident>>(null);
  const [state, setState] = useState<"loading" | "ready">("loading");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [a, i] = await Promise.all([
        settle(listAlerts({ limit: WINDOW })),
        settle(listIncidents({ limit: INCIDENT_WINDOW })),
      ]);
      if (cancelled) return;
      setAlerts(a);
      setIncidents(i);
      setState("ready");
    })();

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // Real AlertSeverity domain is critical | warning | info. There is no "high".
  const criticalAlerts = alerts ? alerts.filter((a) => a.severity === "critical") : null;
  const warningAlerts = alerts ? alerts.filter((a) => a.severity === "warning") : null;

  const openIncidents = incidents
    ? incidents.filter((i) => !["resolved", "closed", "archived"].includes(String(i.status)))
    : null;

  const when = (v?: string | null) =>
    v ? String(v).replace("T", " ").replace("Z", "").slice(0, 19) : "-";

  return (
    <PortalShell
      breadcrumb={["Home", "Operations"]}
      title="Operations"
      description="Operational overview of alerts, incidents, changes, maintenance, events and tickets."
      actions={
        <button type="button" onClick={() => setReloadKey((k) => k + 1)} style={btnStyle}>
          Refresh
        </button>
      }
    >
      {state === "loading" && <LoadingState label="Loading operational state..." />}

      {state === "ready" && (
        <>
          {/* SUMMARY - real counts, scope stated explicitly. */}
          <section>
            <h2 style={headingStyle}>SUMMARY</h2>
            <div style={gridStyle}>
              <Metric
                label="Open incidents"
                value={openIncidents ? String(openIncidents.length) : "Unavailable"}
                note={incidents ? `from ${incidents.length} loaded` : "API did not respond"}
              />
              <Metric
                label="Critical alerts"
                value={criticalAlerts ? String(criticalAlerts.length) : "Unavailable"}
                note={alerts ? `from ${alerts.length} loaded` : "API did not respond"}
              />
              <Metric
                label="Warning alerts"
                value={warningAlerts ? String(warningAlerts.length) : "Unavailable"}
                note={alerts ? `from ${alerts.length} loaded` : "API did not respond"}
              />
            </div>
          </section>

          {/* OPERATIONAL SERVICES - links only to routes that actually exist. */}
          <section style={{ marginTop: 18 }}>
            <h2 style={headingStyle}>OPERATIONAL SERVICES</h2>
            <div style={gridStyle}>
              <ServiceCard
                title="Alerts"
                description="Alert workflow with resource detail."
                status="Available capability"
                href="/portal/resources/alerts"
              />
              <ServiceCard
                title="Incidents"
                description="Incident workflow with resource detail."
                status="Available capability"
                href="/portal/resources/incidents"
              />
              <ServiceCard
                title="Changes"
                description="Change management."
                status="Available capability"
                href="/changes"
              />
              <ServiceCard
                title="Tickets"
                description="Ticketing workspace."
                status="Available capability"
                href="/tickets"
              />
              <ServiceCard
                title="Event Log"
                description="NOC event log."
                status="Available capability"
                href="/noc-event-log"
              />
              <ServiceCard
                title="Maintenance"
                description="Maintenance windows."
                status="Coming soon"
                note="Maintenance management is not yet exposed through the portal."
              />
              <ServiceCard
                title="Problems"
                description="Problem management."
                status="Coming soon"
                note="Problem management is not yet exposed through the portal."
              />
            </div>
          </section>

          {/* OPEN INCIDENTS */}
          <section style={{ marginTop: 18 }}>
            <h2 style={headingStyle}>OPEN INCIDENTS</h2>
            {openIncidents === null ? (
              <EmptyState message="Unavailable - the incidents API did not respond." />
            ) : openIncidents.length === 0 ? (
              <EmptyState message="No open incidents were returned." />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr style={theadRow}>
                    <th style={thStyle}>Incident</th>
                    <th style={thStyle}>Severity</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Alerts</th>
                    <th style={thStyle}>Opened</th>
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {openIncidents.map((i) => (
                    <tr key={i.id} style={rowStyle}>
                      <td style={tdStyle}>{i.title}</td>
                      <td style={tdStyle}>{String(i.severity)}</td>
                      <td style={tdStyle}>{String(i.status)}</td>
                      <td style={tdStyle}>{String(i.alert_count)}</td>
                      <td style={tdStyle}>{when(i.opened_at)}</td>
                      <td style={tdStyle}>
                        <Link href={`/portal/resources/incidents/${i.id}`} style={linkStyle}>
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {/* CRITICAL ALERTS - severity "critical" only (real domain). */}
          <section style={{ marginTop: 18 }}>
            <h2 style={headingStyle}>CRITICAL ALERTS</h2>
            {criticalAlerts === null ? (
              <EmptyState message="Unavailable - the alerts API did not respond." />
            ) : criticalAlerts.length === 0 ? (
              <EmptyState message="No critical alerts" />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr style={theadRow}>
                    <th style={thStyle}>Alert</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Source</th>
                    <th style={thStyle}>Triggered</th>
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {criticalAlerts.map((a) => (
                    <tr key={a.id} style={rowStyle}>
                      <td style={tdStyle}>{a.title}</td>
                      <td style={tdStyle}>{String(a.status)}</td>
                      <td style={tdStyle}>{a.source}</td>
                      <td style={tdStyle}>{when(a.triggered_at)}</td>
                      <td style={tdStyle}>
                        <Link href={`/portal/resources/alerts/${a.id}`} style={linkStyle}>
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {/* WARNING ALERTS - severity "warning" only. "high" does not exist in
              HSDEEP's AlertSeverity domain (critical | warning | info). */}
          <section style={{ marginTop: 18 }}>
            <h2 style={headingStyle}>WARNING ALERTS</h2>
            {warningAlerts === null ? (
              <EmptyState message="Unavailable - the alerts API did not respond." />
            ) : warningAlerts.length === 0 ? (
              <EmptyState message="No warning alerts" />
            ) : (
              <table style={tableStyle}>
                <thead>
                  <tr style={theadRow}>
                    <th style={thStyle}>Alert</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Source</th>
                    <th style={thStyle}>Triggered</th>
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {warningAlerts.map((a) => (
                    <tr key={a.id} style={rowStyle}>
                      <td style={tdStyle}>{a.title}</td>
                      <td style={tdStyle}>{String(a.status)}</td>
                      <td style={tdStyle}>{a.source}</td>
                      <td style={tdStyle}>{when(a.triggered_at)}</td>
                      <td style={tdStyle}>
                        <Link href={`/portal/resources/alerts/${a.id}`} style={linkStyle}>
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {alerts === null && incidents === null && (
            <ErrorState message="No operations APIs responded. Check your session and try Refresh." />
          )}
        </>
      )}
    </PortalShell>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="hud-panel" style={metricStyle}>
      <span style={{ fontSize: 11, letterSpacing: 1, color: "var(--muted)" }}>
        {label.toUpperCase()}
      </span>
      <strong style={{ fontSize: 22, color: "var(--cyan)", marginTop: 4 }}>{value}</strong>
      {note && <span style={{ fontSize: 11, color: "var(--muted)" }}>{note}</span>}
    </div>
  );
}

const metricStyle: React.CSSProperties = {
  padding: 12,
  display: "flex",
  flexDirection: "column",
  minWidth: 160,
};
const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 12,
};
const headingStyle: React.CSSProperties = {
  fontSize: 12,
  letterSpacing: 1,
  color: "var(--cyan)",
  marginBottom: 8,
};
const btnStyle: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};
const theadRow: React.CSSProperties = { textAlign: "left", color: "var(--cyan)" };
const thStyle: React.CSSProperties = { padding: "8px 10px", whiteSpace: "nowrap" };
const tdStyle: React.CSSProperties = { padding: "8px 10px", color: "#a7bec7" };
const rowStyle: React.CSSProperties = { borderTop: "1px solid var(--line)" };
const linkStyle: React.CSSProperties = { color: "var(--cyan)", fontSize: 12 };
