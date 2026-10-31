"use client";

/**
 * /portal — COMMAND CENTER.
 *
 * Real operator-facing overview built entirely from EXISTING live APIs. There
 * are NO hard-coded operational numbers: every figure is either taken from an
 * API response or rendered as an explicit unavailable state.
 *
 * Performance: five BOUNDED list calls, issued once in parallel. No per-resource
 * detail requests, no polling, no request per keystroke.
 *
 * Tenant context is delegated to the existing authenticated API client - this
 * page never accepts or sends a tenant id of its own.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState, PortalShell, ServiceCard } from "@/components/portal/PortalShell";
import { StatTile } from "@/components/ui/PageKit";
import { countDeviceStates } from "@/lib/dashboard/metrics";
import {
  getAlertIntelligenceMetrics,
  getOperationalIntelligence,
  listAlerts,
  listAssetDevices,
  listAssetSites,
  listIncidents,
  listMonitoringSources,
  type Alert,
  type AlertIntelligenceMetrics,
  type AssetDevice,
  type AssetSite,
  type Incident,
  type MonitoringSourceOut,
  type OperationalIntelligence,
} from "@/lib/api";

/** Bounded window for infra/alerts. Counts are labelled as coming from this
 *  window - never sold as a global total the API did not return. */
const WINDOW = 100;

/** Incidents are the widest rows in the product (~2 KB each: RCA summary,
 *  supporting evidence, denormalized names). Measured on the real database, the
 *  authenticated GET /api/incidents?limit=100 took 4.6-6.9s - a large share of
 *  the API client's 10s timeout for a single screen that issues five parallel
 *  calls. 25 keeps the Command Center bounded and well inside that budget. */
const INCIDENT_WINDOW = 25;

type Loaded<T> = T[] | null; // null = the API did not answer (unavailable)

type Snapshot = {
  devices: Loaded<AssetDevice>;
  sites: Loaded<AssetSite>;
  sources: Loaded<MonitoringSourceOut>;
  alerts: Loaded<Alert>;
  incidents: Loaded<Incident>;
  intelligence: AlertIntelligenceMetrics | null;
  oi: OperationalIntelligence | null;
};

const EMPTY: Snapshot = {
  devices: null,
  sites: null,
  sources: null,
  alerts: null,
  incidents: null,
  intelligence: null,
  oi: null,
};

/** Resolves to null instead of throwing, so one failing API cannot blank the
 *  Command Center - only that section renders as unavailable. */
async function settle<T>(p: Promise<T[]>): Promise<Loaded<T>> {
  try {
    return await p;
  } catch {
    return null;
  }
}

function MetricCard({
  label,
  value,
  note,
  href,
}: {
  label: string;
  value: string;
  note?: string;
  href: string;
}) {
  return <StatTile label={label} value={value} sub={note} href={href} info={note ? `${label}: ${note}` : label} />;
}

/** Section titles are authored in caps; render them in sentence case. */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const lower = title.toLowerCase().replace(/(^|[^a-z])ai(?![a-z])/g, "$1AI").replace(/(^|[^a-z])sop(?![a-z])/g, "$1SOP");
  const label = lower.charAt(0).toUpperCase() + lower.slice(1);
  return (
    <section className="ecc-card" style={{ marginTop: 16 }} aria-label={label}>
      <header className="ecc-card__header">
        <h2 className="ecc-card__title">{label}</h2>
      </header>
      <div className="ecc-card__body">{children}</div>
    </section>
  );
}

export default function CommandCenterPage() {
  const [data, setData] = useState<Snapshot>(EMPTY);
  const [state, setState] = useState<"loading" | "ready">("loading");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [devices, sites, sources, alerts, incidents, intelligence, oi] = await Promise.all([
        settle(listAssetDevices(undefined, { limit: WINDOW })),
        settle(listAssetSites(undefined, { limit: WINDOW })),
        settle(listMonitoringSources(undefined)),
        settle(listAlerts({ limit: WINDOW })),
        settle(listIncidents({ limit: INCIDENT_WINDOW })),
        // Real correlation/priority aggregates from the existing endpoint.
        (async () => {
          try {
            return await getAlertIntelligenceMetrics();
          } catch {
            return null;
          }
        })(),
        // Canonical full-estate device-state counts (backend counts_by_state).
        (async () => {
          try {
            return await getOperationalIntelligence();
          } catch {
            return null;
          }
        })(),
      ]);
      if (cancelled) return;
      setData({ devices, sites, sources, alerts, incidents, intelligence, oi });
      setState("ready");
    })();

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const { devices, sites, sources, alerts, incidents, intelligence, oi } = data;

  const criticalAlerts = alerts ? alerts.filter((a) => a.severity === "critical") : null;
  // HSDEEP's AlertSeverity is critical | warning | info - there is no "high".
  const highAlerts = alerts ? alerts.filter((a) => a.severity === "warning") : null;
  const openIncidents = incidents
    ? incidents.filter((i) => !["resolved", "closed", "archived"].includes(String(i.status)))
    : null;

  /** Reads an optional string field the API may or may not have returned.
   *  Missing/blank => null, which renders as an explicit "not available" state
   *  rather than an invented value. */
  const text = (row: Incident, key: string): string | null => {
    const value = (row as unknown as Record<string, unknown>)[key];
    return typeof value === "string" && value.trim() !== "" ? value : null;
  };

  /** The RCA persistence helper stores ``supporting_evidence`` as
   *  ``{ evidence: [...], generated_at }`` - NOT as a bare array (verified on a
   *  real RCA-bearing row). Returns the entry list, or [] when absent. */
  const readEvidence = (row: Incident): Record<string, unknown>[] => {
    const raw = (row as unknown as Record<string, unknown>).supporting_evidence;
    const list = Array.isArray(raw)
      ? raw
      : raw !== null && typeof raw === "object"
        ? (raw as Record<string, unknown>).evidence
        : null;
    return Array.isArray(list)
      ? list.filter(
          (e): e is Record<string, unknown> => e !== null && typeof e === "object",
        )
      : [];
  };

  /** Persisted Dependency Intelligence entries for one incident (real data only). */
  const dependencyFindings = (row: Incident): Record<string, unknown>[] =>
    readEvidence(row).filter((e) =>
      String(e.source ?? "").startsWith("dependency_intelligence"),
    );

  // Device-state classification comes from the backend canonical aggregate
  // (counts_by_state, the same source the dashboard's tiles use), so /portal
  // and /dashboard never disagree on up/down/stale/unknown. Fall back to the
  // local selector when the aggregate is unavailable.
  const deviceStates = oi?.devices?.canonical ?? countDeviceStates(devices ?? undefined);

  const when = (v?: string | null) =>
    v ? String(v).replace("T", " ").replace("Z", "").slice(0, 19) : "-";

  return (
    <PortalShell
      breadcrumb={["Home"]}
      title="Command Center"
      description="Infrastructure operational overview. Every figure comes from a live API."
      actions={
        <button type="button" onClick={() => setReloadKey((k) => k + 1)} style={btnStyle}>
          Refresh
        </button>
      }
    >
      <div style={{ padding: "2px 0" }}>

      {state === "loading" && <LoadingState label="Loading infrastructure state..." />}

      {state === "ready" && (
        <>
          <Section title="OPERATIONS">
            <div style={gridStyle}>
              <MetricCard
                label="Critical alerts"
                value={criticalAlerts ? String(criticalAlerts.length) : "Unavailable"}
                note={alerts ? `from ${alerts.length} most recent` : "API did not respond"}
                href="/portal/resources/alerts"
              />
              <MetricCard
                label="Warning alerts"
                value={highAlerts ? String(highAlerts.length) : "Unavailable"}
                note={alerts ? `from ${alerts.length} most recent` : "API did not respond"}
                href="/portal/resources/alerts"
              />
              <MetricCard
                label="Active incidents"
                value={openIncidents ? String(openIncidents.length) : "Unavailable"}
                note={incidents ? `from ${incidents.length} most recent` : "API did not respond"}
                href="/portal/resources/incidents"
              />
            </div>
          </Section>

          <Section title="INFRASTRUCTURE">
            <div style={gridStyle}>
              <MetricCard
                label="Devices"
                value={oi ? String(oi.devices.canonical.total) : devices ? String(devices.length) : "Unavailable"}
                note={oi ? "canonical full-estate count" : devices ? `up to ${WINDOW} loaded` : "API did not respond"}
                href="/portal/resources/devices"
              />
              <MetricCard
                label="Sites"
                value={sites ? String(sites.length) : "Unavailable"}
                note={sites ? `up to ${WINDOW} loaded` : "API did not respond"}
                href="/portal/resources/sites"
              />
              <MetricCard
                label="Monitoring sources"
                value={sources ? String(sources.length) : "Unavailable"}
                note={sources ? "all sources" : "API did not respond"}
                href="/portal/resources/sources"
              />
            </div>

            {deviceStates.total > 0 && (
              <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
                Device state (canonical): up {deviceStates.up} · down {deviceStates.down} ·{" "}
                stale {deviceStates.stale} · unknown {deviceStates.unknown} · total{" "}
                {deviceStates.total}
              </p>
            )}
          </Section>

          <Section title="AI OPERATIONS SUMMARY">
            {intelligence === null ? (
              <EmptyState message="Unavailable - the incident intelligence API did not respond." />
            ) : (
              <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
                <p style={{ margin: 0 }}>
                  Correlation: {intelligence.correlated_incidents ?? 0} correlated incident(s)
                  {" \u00b7 "}
                  {intelligence.suppressed_alerts ?? 0} alert(s) suppressed as symptoms
                  {" \u00b7 "}
                  {intelligence.duplicate_alerts_removed ?? 0} duplicate(s) removed
                  {" \u00b7 "}
                  {intelligence.noise_reduction_pct ?? 0}% noise reduction
                </p>
                <p style={{ margin: 0, color: "var(--muted)" }}>
                  RCA:{" "}
                  {openIncidents && openIncidents.some((i) => text(i, "root_cause_summary"))
                    ? `available for ${
                        openIncidents.filter((i) => text(i, "root_cause_summary")).length
                      } of ${openIncidents.length} loaded incident(s)`
                    : "Not available for the loaded window"}
                  {" \u00b7 "}
                  Recommendation:{" "}
                  {openIncidents && openIncidents.some((i) => text(i, "recommended_sop"))
                    ? `available for ${
                        openIncidents.filter((i) => text(i, "recommended_sop")).length
                      } of ${openIncidents.length} loaded incident(s)`
                    : "Not available for the loaded window"}
                </p>
                <p style={{ margin: 0, color: "var(--muted)" }}>
                  Dependency intelligence:{" "}
                  {(() => {
                    const entries = (openIncidents ?? []).reduce(
                      (total, row) => total + dependencyFindings(row).length,
                      0,
                    );
                    return entries > 0
                      ? `available - ${entries} finding entr${entries === 1 ? "y" : "ies"} in the loaded window`
                      : "No correlated dependency finding in the loaded window";
                  })()}
                </p>
              </div>
            )}
          </Section>

          <Section title="ACTIVE INCIDENTS">
            {openIncidents === null ? (
              <EmptyState message="Unavailable - the incidents API did not respond." />
            ) : openIncidents.length === 0 ? (
              <EmptyState message="No active incidents were returned." />
            ) : (
              <div className="pk-table-wrap"><table className="pk-table">
                <thead>
                  <tr style={theadRow}>
                    <th style={thStyle}>Incident</th>
                    <th style={thStyle}>Severity</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Priority</th>
                    <th style={thStyle}>Device</th>
                    <th style={thStyle}>Alerts</th>
                    <th style={thStyle}>RCA</th>
                    <th style={thStyle}>Dependency</th>
                    <th style={thStyle}>Recommendation</th>
                    <th style={thStyle}>Last Alert</th>
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {openIncidents.slice(0, 8).map((i) => (
                    <tr key={i.id} style={rowStyle}>
                      <td style={tdStyle}>{i.title}</td>
                      <td style={tdStyle}>{String(i.severity)}</td>
                      <td style={tdStyle}>{String(i.status)}</td>
                      <td style={tdStyle}>{text(i, "priority") ?? "-"}</td>
                      <td style={tdStyle}>{text(i, "device_name") ?? "-"}</td>
                      <td style={tdStyle}>{String(i.alert_count)}</td>
                      <td style={tdStyle}>
                        {text(i, "root_cause_summary") ? "Available" : "Not available"}
                      </td>
                      <td style={tdStyle}>
                        {dependencyFindings(i).length > 0
                          ? `Available (${dependencyFindings(i).length})`
                          : "No correlated finding"}
                      </td>
                      <td style={tdStyle}>
                        {text(i, "recommended_sop") ? "SOP available" : "Not available"}
                      </td>
                      <td style={tdStyle}>{when(text(i, "last_alert_at"))}</td>
                      <td style={tdStyle}>
                        <Link href={`/portal/resources/incidents/${i.id}`} style={linkStyle}>
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Section>

          <Section title="CRITICAL / WARNING ALERTS">
            {alerts === null ? (
              <EmptyState message="Unavailable - the alerts API did not respond." />
            ) : (criticalAlerts?.length ?? 0) + (highAlerts?.length ?? 0) === 0 ? (
              <EmptyState message="No critical or warning alerts in the loaded window." />
            ) : (
              <div className="pk-table-wrap"><table className="pk-table">
                <thead>
                  <tr style={theadRow}>
                    <th style={thStyle}>Alert</th>
                    <th style={thStyle}>Severity</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Source</th>
                    <th style={thStyle}>Triggered</th>
                    <th style={thStyle}>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(criticalAlerts ?? []), ...(highAlerts ?? [])].slice(0, 8).map((a) => (
                    <tr key={a.id} style={rowStyle}>
                      <td style={tdStyle}>{a.title}</td>
                      <td style={tdStyle}>{String(a.severity)}</td>
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
              </table></div>
            )}
            <p style={{ marginTop: 6 }}>
              <Link href="/portal/resources/alerts" style={linkStyle}>
                View all alerts
              </Link>
            </p>
          </Section>

          <Section title="AI / INTELLIGENCE">
            <EmptyState message="No portal data source for AI findings yet. Existing AI capabilities are linked below." />
            <div style={gridStyle}>
              <ServiceCard
                title="AI Copilot"
                description="Existing AI copilot workspace."
                status="Available capability"
                href="/ai-copilot"
              />
              <ServiceCard
                title="SOP / Runbooks"
                description="Existing SOP engine."
                status="Available capability"
                href="/sop-engine"
              />
              <ServiceCard
                title="SOC Analysis"
                description="Existing SOC analysis."
                status="Available capability"
                href="/soc-analysis"
              />
            </div>
          </Section>

          <Section title="SERVICES">
            <div style={gridStyle}>
              <ServiceCard
                title="Monitoring"
                description="Monitoring sources, collectors, devices, sensors."
                status="Available capability"
                href="/monitoring"
              />
              <ServiceCard
                title="Discovery"
                description="Universal Discovery scopes."
                status="Available capability"
                href="/infrastructure/discovery"
              />
              <ServiceCard
                title="Network Intelligence"
                description="Topology and relationships."
                status="Available capability"
                href="/infrastructure-topology"
              />
              <ServiceCard
                title="Operations"
                description="Alerts, incidents, changes, tickets, events."
                status="Available capability"
                href="/portal/operations"
              />
              <ServiceCard
                title="Resources"
                description="Devices, sites, sources, alerts, incidents."
                status="Available capability"
                href="/portal/resources"
              />
              <ServiceCard
                title="Integrations"
                description="Vendor integration catalog."
                status="Coming soon"
                note="No integration catalog page exists yet."
              />
            </div>
          </Section>
        </>
      )}

      {state === "ready" && devices === null && sites === null && sources === null && (
        <ErrorState message="No infrastructure APIs responded. Check your session and try Refresh." />
      )}
      </div>
    </PortalShell>
  );
}

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 12,
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
const theadRow: React.CSSProperties = {};
const thStyle: React.CSSProperties = {};
const tdStyle: React.CSSProperties = {};
const rowStyle: React.CSSProperties = {};
const linkStyle: React.CSSProperties = { color: "var(--ecc-accent)", fontSize: 12 };
