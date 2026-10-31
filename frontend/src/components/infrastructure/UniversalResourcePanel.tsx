"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getResource,
  getResourceInterfaces,
  getResourceNetwork,
  getResourceTelemetry,
  getTenantOverview,
  listResources,
  postCorrelationContext,
  type CapabilityReport,
  type CorrelationContextOut,
  type InterfacesOut,
  type OperationalStateOut,
  type ResourceDetailOut,
  type ResourceNetworkOut,
  type TenantOverviewOut,
  type TelemetryOut,
  type UniversalResourceOut,
} from "@/lib/infrastructureIntelligence";
import FailoverTimelinePanel from "@/components/infrastructure/FailoverTimelinePanel";
import { Pill, StatTile, ago, statusTone, titleCaseWords } from "@/components/ui/PageKit";
import { formatMinutes } from "@/lib/commandCenter/format";

/**
 * UniversalResourcePanel — the /infrastructure/universal operator surface for
 * the universal infrastructure intelligence API.
 *
 * Presentation only, and deliberately honest about absence: when a capability
 * is not supported (or not yet collected) the panel shows the API's own
 * `reason` instead of a zero. No value is ever inferred client-side.
 */

const cardStyle: React.CSSProperties = {
  background: "var(--ecc-surface)",
  border: "1px solid var(--ecc-border)",
  borderRadius: "var(--ecc-radius)",
  padding: 14,
  marginBottom: 16,
};

const kpiStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
  gap: 12,
};


const thStyle: React.CSSProperties = {};

const tdStyle: React.CSSProperties = {};

const HEALTH_COLOR: Record<string, string> = {
  healthy: "#4ade80",
  warning: "#fbbf24",
  critical: "#f87171",
  maintenance: "#60a5fa",
  unknown: "#94a3b8",
};

function healthColor(health: string | undefined): string {
  return HEALTH_COLOR[health ?? "unknown"] ?? HEALTH_COLOR.unknown;
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  const nice = (label.charAt(0) + label.slice(1).toLowerCase()).replace(/bgp/i, "BGP");
  const shown = /^\d+$/.test(value) ? Number(value).toLocaleString("en-US") : value;
  return <StatTile label={nice} value={shown} info={nice} alarm={tone === HEALTH_COLOR.critical} />;
}

function NotAvailable({ reason, detail }: { reason?: string | null; detail?: string }) {
  return (
    <div
      style={{
        padding: "10px 12px",
        border: "1px dashed rgba(148, 163, 184, 0.5)",
        borderRadius: 8,
        fontSize: 12,
        opacity: 0.85,
      }}
    >
      <strong>Not available.</strong>{" "}
      {detail ?? "This provider or resource does not support this data."}
      {reason ? <div style={{ marginTop: 4, opacity: 0.8 }}>reason: {reason}</div> : null}
    </div>
  );
}

export default function UniversalResourcePanel() {
  const [overview, setOverview] = useState<TenantOverviewOut | null>(null);
  const [resources, setResources] = useState<UniversalResourceOut[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [selected, setSelected] = useState<UniversalResourceOut | null>(null);
  const [detail, setDetail] = useState<ResourceDetailOut | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityReport | null>(null);
  const [interfaces, setInterfaces] = useState<InterfacesOut | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryOut | null>(null);
  const [network, setNetwork] = useState<ResourceNetworkOut | null>(null);
  const [correlation, setCorrelation] = useState<CorrelationContextOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Filter changes drive the fetch directly from the dependency array. Every
  // state write happens after an await, so this neither cascades renders nor
  // needs a manual "load" callback.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [summary, page] = await Promise.all([
          getTenantOverview(),
          listResources({
            search: search || undefined,
            resourceType: typeFilter || undefined,
            limit: 50,
          }),
        ]);
        if (cancelled) return;
        setOverview(summary);
        setResources(page.items);
        setTotal(page.total);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load infrastructure data",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [search, typeFilter]);

  const select = useCallback(async (resource: UniversalResourceOut) => {
    setSelected(resource);
    setDetail(null);
    setCapabilities(null);
    setInterfaces(null);
    setTelemetry(null);
    setNetwork(null);
    setCorrelation(null);
    try {
      const [detailResult, ifacesResult, telemetryResult, networkResult] =
        await Promise.all([
          getResource(resource.resource_id),
          getResourceInterfaces(resource.resource_id),
          getResourceTelemetry(resource.resource_id, { limit: 50 }),
          getResourceNetwork(resource.resource_id),
        ]);
      setDetail(detailResult);
      setCapabilities(detailResult.capabilities);
      setInterfaces(ifacesResult);
      setTelemetry(telemetryResult);
      setNetwork(networkResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load resource detail");
    }
  }, []);

  const correlate = useCallback(async () => {
    if (!resources.length) return;
    try {
      setCorrelation(
        await postCorrelationContext(
          resources.slice(0, 25).map((resource) => resource.resource_id),
        ),
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to build correlation context",
      );
    }
  }, [resources]);

  return (
    <section className="pk-page">
      <header className="pk-header">
        <div>
        <nav className="pk-crumbs" aria-label="Breadcrumb"><a href="/infrastructure">Infrastructure</a> / Universal Resources</nav>
        <h1 className="pk-title">
          Universal Resources
        </h1>
        <p className="pk-subtitle">
          Provider-neutral view of every resource: firewalls, routers, switches,
          access points, servers, cloud resources, endpoints and storage. Every
          value comes from persisted collection data; a capability that was not
          collected is reported as unavailable with the reason the API returned.
        </p>
        </div>
      </header>

      {error ? (
        <div style={{ ...cardStyle, borderColor: "#f87171", color: "#fecaca" }}>
          {error}
        </div>
      ) : null}

      {overview ? (
        <>
          <div style={kpiStyle}>
            <Kpi
              label="TENANT HEALTH"
              value={`${titleCaseWords(overview.health.health)} · ${overview.health.score}`}
              tone={healthColor(overview.health.health)}
            />
            <Kpi label="SITES" value={String(overview.sites.total)} />
            <Kpi label="RESOURCES" value={String(overview.resources.total)} />
            <Kpi label="INTERFACES" value={String(overview.interfaces.total)} />
            <Kpi
              label="ROUTING ENTRIES"
              value={String(overview.network.routing_entries)}
            />
            <Kpi label="BGP NEIGHBORS" value={String(overview.network.bgp_neighbors)} />
            <Kpi label="CLOUD RESOURCES" value={String(overview.cloud.resources)} />
            <Kpi label="OPEN INCIDENTS" value={String(overview.incidents.open)} />
            <Kpi
              label="TELEMETRY"
              value={
                overview.telemetry.freshness_seconds === null
                  ? "no telemetry yet"
                  : `${formatMinutes(overview.telemetry.freshness_seconds / 60)} old`
              }
              tone={overview.telemetry.quality === "stale" ? "#fbbf24" : undefined}
            />
            <Kpi
              label="CAPABILITIES ASSERTED"
              value={String(overview.capabilities.asserted)}
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 14 }}>
            <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
              Providers wired:{" "}
              {overview.providers.wired.length
                ? overview.providers.wired.join(", ")
                : "none"}{" "}
              · in use:{" "}
              {overview.providers.in_use.length
                ? overview.providers.in_use.join(", ")
                : "none collected yet"}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 12 }}>
              {Object.entries(overview.resources.by_resource_type).map(
                ([type, count]) => (
                  <span
                    key={type}
                    style={{
                      padding: "2px 8px",
                      borderRadius: 999,
                      border: "1px solid rgba(90, 140, 200, 0.35)",
                    }}
                  >
                    {type}: {count}
                  </span>
                ),
              )}
            </div>
          </div>
        </>
      ) : null}

      <div
        style={{ ...cardStyle, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}
      >
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search resources"
          aria-label="Search resources"
          className="pk-input"
          style={{ minWidth: 240 }}
        />
        <input
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
          placeholder="Resource type, e.g. firewall"
          aria-label="Filter by resource type"
          className="pk-input"
          style={{ minWidth: 200 }}
        />
        <button
          type="button"
          onClick={() => void correlate()}
          className="pk-button"
        >
          Correlate visible resources
        </button>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          {loading ? "Loading…" : `${resources.length} of ${total}`}
        </span>
      </div>

      <div className="pk-table-wrap" style={{ maxHeight: 560, marginBottom: 16 }}>
        <table className="pk-table">
          <thead>
            <tr>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>Family</th>
              <th style={thStyle}>Provider</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Health</th>
              <th style={thStyle}>Lifecycle</th>
              <th style={thStyle}>Operational state</th>
              <th style={thStyle}>Last telemetry</th>
            </tr>
          </thead>
          <tbody>
            {resources.map((resource) => (
              <tr
                key={resource.resource_id}
                onClick={() => void select(resource)}
                style={{
                  cursor: "pointer",
                  background:
                    selected?.resource_id === resource.resource_id
                      ? "var(--ecc-surface-raised)"
                      : undefined,
                }}
              >
                <td style={tdStyle}><span className="pk-cell-main pk-mono">{resource.name}</span></td>
                <td style={tdStyle}>{titleCaseWords(resource.resource_type)}</td>
                <td style={tdStyle} className="pk-muted">{titleCaseWords(resource.family)}</td>
                <td style={tdStyle}>{resource.provider ? titleCaseWords(resource.provider) : "—"}</td>
                <td style={tdStyle}><Pill tone={statusTone(resource.status)}>{titleCaseWords(resource.status)}</Pill></td>
                <td style={tdStyle}><Pill tone={resource.health === "healthy" ? "good" : statusTone(resource.health)}>{titleCaseWords(resource.health)}</Pill></td>
                <td style={tdStyle} className="pk-muted">{titleCaseWords(resource.lifecycle_state)}</td>
                <td style={tdStyle}>
                  <OperationalSummary state={resource.operational_state} />
                </td>
                <td style={tdStyle}>
                  {resource.last_telemetry_at ? (
                    <span title={new Date(resource.last_telemetry_at).toLocaleString()}>{ago(resource.last_telemetry_at)}</span>
                  ) : (
                    <span className="pk-muted">Never</span>
                  )}
                </td>
              </tr>
            ))}
            {!resources.length && !loading ? (
              <tr>
                <td style={tdStyle} colSpan={9}>
                  No resources match. Either nothing has been discovered for this
                  tenant yet, or the filters exclude everything.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {selected ? (
        <div style={cardStyle}>
          <h2 style={{ fontSize: 15, fontWeight: 600, marginTop: 0 }}>
            {selected.name}{" "}
            <span style={{ fontSize: 12, opacity: 0.7 }}>
              · {selected.resource_type} · provider {selected.provider ?? "unknown"}
            </span>
          </h2>

          <OperationalStateBlock state={selected.operational_state ?? null} />

          {detail ? (
            <div style={{ fontSize: 12, marginBottom: 10 }}>
              <div>
                health:{" "}
                <span style={{ color: healthColor(detail.health.health) }}>
                  {detail.health.health}
                </span>{" "}
                (score {detail.health.score}
                {detail.health.raw_score !== detail.health.score
                  ? `, raw ${detail.health.raw_score}`
                  : ""}
                ) · status {detail.health.status}
                {detail.health.in_maintenance ? " · in planned maintenance" : ""}
              </div>
              <div style={{ opacity: 0.8, marginTop: 4 }}>
                telemetry: {detail.health.telemetry_quality}
                {detail.health.telemetry_freshness_seconds === null
                  ? " (no telemetry has ever been received)"
                  : ` (${Math.round(detail.health.telemetry_freshness_seconds)}s old)`}
              </div>
              {detail.health.reasons.map((reason) => (
                <div key={reason.reason} style={{ opacity: 0.8, marginTop: 2 }}>
                  reason: {reason.reason}
                  {reason.detail ? ` — ${reason.detail}` : ""}
                </div>
              ))}
              {detail.health.dependencies.length ? (
                <div style={{ marginTop: 8 }}>
                  <div style={{ opacity: 0.75 }}>Dependencies</div>
                  {detail.health.dependencies.map((dependency) => (
                    <div key={`${dependency.resource_id}-${dependency.relationship_type}`}>
                      {dependency.name ?? dependency.resource_id} ·{" "}
                      {dependency.relationship_type} ·{" "}
                      <span style={{ color: healthColor(dependency.health) }}>
                        {dependency.health}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div style={{ fontSize: 12, opacity: 0.7 }}>loading detail…</div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                Capabilities
              </div>
              {capabilities?.counts ? (
                <div style={{ fontSize: 12, marginBottom: 6 }}>
                  supported {capabilities.counts.supported ?? 0} · unsupported{" "}
                  {capabilities.counts.unsupported ?? 0} · unknown{" "}
                  {capabilities.counts.unknown ?? 0}
                </div>
              ) : null}
              {capabilities?.supported?.length ? (
                <div style={{ fontSize: 12, marginBottom: 6 }}>
                  <span style={{ opacity: 0.75 }}>supported: </span>
                  {capabilities.supported.join(", ")}
                </div>
              ) : null}
              {capabilities?.unsupported?.length ? (
                <div style={{ fontSize: 12 }}>
                  <div style={{ opacity: 0.75, marginBottom: 2 }}>
                    not supported by this provider/resource:
                  </div>
                  {capabilities.unsupported.map((entry) => (
                    <div key={entry.capability}>
                      {entry.capability} — {entry.reason}
                    </div>
                  ))}
                </div>
              ) : null}
              {capabilities && !capabilities.unsupported?.length &&
              !capabilities.supported?.length ? (
                <NotAvailable reason={null} detail="No capability evidence for this resource yet." />
              ) : null}
            </div>

            <div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                Interfaces ({interfaces?.total ?? 0})
              </div>
              {interfaces?.supported ? (
                <div style={{ fontSize: 12 }}>
                  up {interfaces.up} · down {interfaces.down} · unknown{" "}
                  {interfaces.unknown}
                  {interfaces.items.slice(0, 8).map((iface) => (
                    <div key={iface.interface_id} style={{ marginTop: 2 }}>
                      {iface.name} · {iface.interface_type} · {iface.oper_state}
                      {iface.measured.length
                        ? ` · measured: ${iface.measured.join(", ")}`
                        : " · no rate metrics reported"}
                    </div>
                  ))}
                </div>
              ) : (
                <NotAvailable
                  reason={interfaces?.reason ?? undefined}
                  detail="This resource has no collected interfaces."
                />
              )}
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
              Current telemetry ({telemetry?.current.length ?? 0})
            </div>
            {telemetry?.current.length ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 12 }}>
                {telemetry.current.map((record) => (
                  <span
                    key={`${record.capability}-${record.metric_name}`}
                    style={{
                      padding: "2px 8px",
                      borderRadius: 6,
                      border: "1px solid rgba(90, 140, 200, 0.3)",
                    }}
                  >
                    {record.capability}/{record.metric_name}:{" "}
                    {record.numeric_value ?? record.text_value ?? "—"}
                    {record.unit ? ` ${record.unit}` : ""}
                    <span style={{ opacity: 0.6 }}> ({record.quality})</span>
                  </span>
                ))}
              </div>
            ) : (
              <NotAvailable
                reason={null}
                detail="No canonical telemetry has been persisted for this resource yet."
              />
            )}
          </div>

          <div
            style={{
              marginTop: 12,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 10,
              fontSize: 12,
            }}
          >
            {(["wan", "sdwan", "vpn", "routing", "bgp"] as const).map((section) => {
              const data = network?.[section];
              return (
                <div
                  key={section}
                  style={{
                    border: "1px solid rgba(90, 140, 200, 0.2)",
                    borderRadius: 8,
                    padding: "8px 10px",
                  }}
                >
                  <div style={{ fontWeight: 600, textTransform: "uppercase" }}>
                    {section}
                  </div>
                  {data?.supported ? (
                    <div style={{ marginTop: 4 }}>
                      {data.total ?? 0} item(s)
                      {data.by_protocol
                        ? ` · ${Object.entries(data.by_protocol)
                            .map(([proto, count]) => `${proto}:${count}`)
                            .join(", ")}`
                        : ""}
                      {typeof data.established === "number"
                        ? ` · established ${data.established}`
                        : ""}
                      {data.available === false ? " · nothing to score yet" : ""}
                    </div>
                  ) : (
                    <div style={{ marginTop: 4, opacity: 0.8 }}>
                      not available · {data?.reason ?? "no data collected"}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {correlation ? (
        <div style={cardStyle}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            Correlation context — {correlation.groups.length} group(s) from{" "}
            {correlation.resources.length} resource(s)
          </div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>
            basis: {correlation.correlation_basis.join(", ") || "no edges yet"}
          </div>
          {correlation.groups.map((group, index) => (
            <div key={index} style={{ fontSize: 12, marginTop: 6 }}>
              <strong>group {index + 1}</strong> — {String(group.member_count)} member(s),
              worst health {String((group.priority_context as Record<string, unknown>)?.worst_health ?? "unknown")}
            </div>
          ))}
          {correlation.limitations.map((limitation) => (
            <div key={limitation.reason} style={{ fontSize: 12, opacity: 0.8, marginTop: 6 }}>
              limitation: {limitation.reason}
              {limitation.detail ? ` — ${limitation.detail}` : ""}
            </div>
          ))}
        </div>
      ) : null}

      {/* WAN failover intelligence (detect -> trace -> verify).
          Scoped to the selected resource's SITE, because a failover is a site-
          edge event rather than a single-resource one; with nothing selected it
          shows the tenant-wide view. Read-only unless the operator holds
          incidents.write for manual recovery verification. */}
      <FailoverTimelinePanel
        siteId={selected?.site_id ?? null}
        title={
          selected?.site_id
            ? "WAN failover intelligence (this site)"
            : "WAN failover intelligence (all sites)"
        }
      />
    </section>
  );
}

/**
 * Compact operational state for the resource table.
 *
 * Shows the three tokens the operator needs to triage a row without opening it:
 * how far the resource is known (identity stage), whether anything is
 * responsible for its telemetry (monitored vs unmonitored), and whether what we
 * hold is usable (freshness). `null` renders as an explicit "not evaluated yet"
 * rather than a misleading healthy-looking value.
 */
function OperationalSummary({
  state,
}: {
  state?: OperationalStateOut | null;
}) {
  if (!state) {
    return <span style={{ opacity: 0.55 }}>not evaluated</span>;
  }
  const monitored = state.binding.is_bound;
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
      <Chip
        text={state.identity_stage}
        tone={state.is_operational_resource ? "#4fae7a" : "#d9a441"}
      />
      <Chip text={monitored ? "monitored" : "unmonitored"} tone={monitored ? "#4fae7a" : "#8a94a6"} />
      <Chip
        text={state.freshness.state}
        tone={
          state.freshness.state === "fresh"
            ? "#4fae7a"
            : state.freshness.state === "aging"
              ? "#d9a441"
              : "#c9534f"
        }
      />
    </span>
  );
}

function Chip({ text, tone }: { text: string; tone: string }) {
  return (
    <span
      style={{
        padding: "1px 7px",
        borderRadius: 6,
        border: `1px solid ${tone}`,
        color: tone,
        fontSize: 11,
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </span>
  );
}

/**
 * Full operational state for the selected resource.
 *
 * Deliberately four separate blocks - identity, monitoring, freshness, topology -
 * because collapsing them is what lets "we have never collected this" look like
 * "this is fine". Blockers are listed verbatim so the next operator action is
 * obvious.
 */
function OperationalStateBlock({ state }: { state: OperationalStateOut | null }) {
  if (!state) {
    return (
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 10 }}>
        Operational state not available for this resource.
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          gap: 10,
          fontSize: 12,
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>Identity stage</div>
          <div>{state.identity_stage}</div>
          <div style={{ opacity: 0.7 }}>{state.identity_reason}</div>
        </div>
        <div>
          <div style={{ fontWeight: 600 }}>Monitoring</div>
          <div>{state.binding.is_bound ? "MONITORED (bound)" : "UNMONITORED"}</div>
          <div style={{ opacity: 0.7 }}>{state.binding.detail}</div>
          <div style={{ opacity: 0.7 }}>
            source {state.binding.monitoring_source_name ?? "—"} · provider id{" "}
            {state.binding.provider_resource_id ?? "—"}
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 600 }}>Telemetry freshness</div>
          <div>{state.freshness.state}</div>
          <div style={{ opacity: 0.7 }}>{state.freshness.detail}</div>
        </div>
        <div>
          <div style={{ fontWeight: 600 }}>Topology evidence</div>
          <div>{state.topology.state}</div>
          <div style={{ opacity: 0.7 }}>{state.topology.detail}</div>
          {state.evidence_sources.length ? (
            <div style={{ opacity: 0.7 }}>
              sources: {state.evidence_sources.join(", ")}
            </div>
          ) : null}
        </div>
      </div>

      {state.blockers.length ? (
        <div
          style={{
            marginTop: 10,
            border: "1px solid rgba(217, 164, 65, 0.4)",
            borderRadius: 8,
            padding: "6px 10px",
            color: "#d9a441",
            fontSize: 12,
          }}
        >
          <div style={{ fontWeight: 600 }}>What is missing</div>
          <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
            {state.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

