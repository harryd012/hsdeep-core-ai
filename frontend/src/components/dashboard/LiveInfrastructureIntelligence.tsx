"use client";

/**
 * LiveInfrastructureIntelligence — the Command Center's intelligence zone.
 *
 * It sits BETWEEN "Attention Required" and "Operational Health" and answers, in
 * the operator's order of need:
 *
 *   WHAT IS HAPPENING?   the focus resource + its recorded relationships
 *   WHAT IS INVOLVED?    the recorded dependency path
 *   WHAT IS AFFECTED?    the backend's impact aggregates
 *   WHY BELIEVE IT?      per-hop evidence + the graph's own limitations
 *   WHAT NEXT?           navigation only (no action is executed here)
 *
 * It is presentational: it owns no fetching (the hook does), no metric the
 * backend already aggregates, and no fabricated value. Every number traces to a
 * field of an existing contract, and every unavailable value renders as such
 * rather than as a 0.
 *
 * Reused, not duplicated: the canonical dependency/impact contract, the existing
 * topology graph read model, the HSI-GNCE correlation read, the dashboard's own
 * collector-health contract, the dashboard's real activity feed, the existing
 * `hud-panel` / `hs-zone` / `panel-header` visual language, and existing routes
 * only (/infrastructure-topology, /infrastructure/assets/{id}, /incidents/{id},
 * /monitoring).
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowDown, ArrowUp, Network, Server } from "lucide-react";

import "@/app/styles/live-infrastructure-intelligence.css";

import type { DashboardData } from "@/lib/dashboard/dashboardModels";
import type {
  InfraCorrelation,
  InfraFocus,
  InfraRelationRow,
  LiveInfraIntel,
} from "@/lib/dashboard/liveInfraIntelModel";
import { useLiveInfraIntel } from "@/hooks/useLiveInfraIntel";

/** Unavailable is never a 0: it says so. */
function count(value: number | null): string {
  return value === null ? "N/A" : value.toLocaleString();
}

function shortTime(value: string | null): string {
  if (!value) return "never";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  const seconds = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/** State -> token. Text is always rendered too, never colour alone. */
function stateTone(state: string): string {
  switch (state.toLowerCase()) {
    case "up":
    case "online":
    case "healthy":
    case "operational":
    case "active":
      return "var(--green)";
    case "degraded":
    case "warning":
      return "var(--amber)";
    case "down":
    case "offline":
    case "failed":
    case "critical":
      return "var(--red)";
    default:
      return "var(--muted)";
  }
}

/* ---------------------------------------------------------------------------
 * Relationship context (LEFT)
 * ------------------------------------------------------------------------ */

function RelationRow({ row }: { row: InfraRelationRow }) {
  const upstream = row.direction === "upstream";
  return (
    <li className="lii-rel">
      <span className="lii-rel__dir" aria-hidden="true">
        {upstream ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
      </span>
      <span className="lii-rel__text">
        <b>{row.fromName}</b>
        <span className="lii-rel__type">{row.relationshipType.replace(/_/g, " ")}</span>
        <b>{row.toName ?? "unresolved endpoint"}</b>
      </span>
      <span className="lii-rel__meta">
        <span className="lii-chip">hop {row.depth}</span>
        {row.inferred ? <span className="lii-chip lii-chip--warn">inferred</span> : null}
        {row.discoveredBy ? <span className="lii-chip">{row.discoveredBy}</span> : null}
        {row.confidence !== null ? <span className="lii-chip">{row.confidence}%</span> : null}
        {row.freshness ? <span className="lii-chip">{row.freshness}</span> : null}
        {!row.resolved ? <span className="lii-chip lii-chip--bad">unresolved</span> : null}
      </span>
      <span className="lii-rel__sr">
        {upstream
          ? `depends on ${row.toName ?? "an unresolved resource"}`
          : `feeds ${row.toName ?? "an unresolved resource"}`}
      </span>
    </li>
  );
}

function ContextPanel({ model }: { model: LiveInfraIntel }) {
  const { focus } = model;

  return (
    <div className="hud-panel lii-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-icon" aria-hidden="true">
            <Server size={14} />
          </span>
          <h3>Infrastructure Context</h3>
        </div>
        <Link className="lii-link" href="/infrastructure-topology">
          View topology
        </Link>
      </div>

      <div className="panel-body">
        {model.state === "loading" && !focus && (
          <div className="lii-loading" role="status" aria-live="polite">
            <span className="lii-skel" />
            <span className="lii-skel" />
            <span className="lii-skel lii-skel--wide" />
            <span className="lii-skel" />
            <span className="lii-loading__label">Resolving infrastructure context…</span>
          </div>
        )}

        {model.state === "error" && !focus && (
          <p className="lii-note lii-note--error">
            Infrastructure context unavailable. The topology and dependency APIs did not
            respond.
          </p>
        )}

        {model.state === "empty" && !focus && (
          <div className="lii-empty">
            <p className="lii-empty__title">No active infrastructure context available</p>
            <p className="lii-empty__body">
              Live graph context will appear when authoritative infrastructure relationships
              or active resource context are available.
            </p>
            {model.graph && (
              <ul className="lii-empty__counts">
                <li>Recorded devices: {count(model.graph.devices)}</li>
                <li>Recorded relationships: {count(model.graph.relationships)}</li>
                <li>Graph state: {model.graph.status.replace(/_/g, " ")}</li>
              </ul>
            )}
            {model.graph?.limitations?.length ? (
              <ul className="lii-empty__notes">
                {model.graph.limitations.slice(0, 3).map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
          </div>
        )}

        {focus && (
          <>
            <div className="lii-focus">
              <div className="lii-focus__main">
                <span className="lii-focus__name">{focus.name}</span>
                <span className="lii-focus__type">{focus.type ?? "unclassified"}</span>
              </div>
              <span
                className="lii-state"
                style={{ color: stateTone(focus.state), borderColor: stateTone(focus.state) }}
              >
                {focus.state}
              </span>
            </div>
            <p className="lii-focus__why">
              Focused because: {focus.reason}
              {focus.incident ? ` — ${focus.incident.title}` : ""}
            </p>
            <div className="lii-focus__links">
              <Link className="lii-link" href={`/infrastructure/assets/${focus.id}`}>
                Open {focus.name}
              </Link>
              {focus.incident && (
                <Link className="lii-link" href={`/incidents/${focus.incident.id}`}>
                  Open incident
                </Link>
              )}
            </div>

            {model.impactUnavailable ? (
              <p className="lii-note">
                {model.state === "loading"
                  ? "Loading relationship evidence for this resource…"
                  : "Relationship evidence for this resource is unavailable right now."}
              </p>
            ) : model.relations.length === 0 ? (
              <p className="lii-note">
                No recorded relationships for this resource. Topology relationships come from
                collected evidence, so an empty list means none has been recorded yet — not
                that the resource is unconnected.
              </p>
            ) : (
              <>
                <p className="lii-subhead">Recorded relationships</p>
                <ul className="lii-rows">
                  {model.relations.map((row) => (
                    <RelationRow key={`${row.direction}-${row.id}`} row={row} />
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Impact + correlation (RIGHT)
 * ------------------------------------------------------------------------ */

/** One impact counter. `null` means the read is not in yet / unavailable. */
function Metric({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  return (
    <li className={`lii-metric${value === null ? " lii-metric--empty" : ""}`}>
      <small className="lii-metric__label">{label}</small>
      <b className="lii-metric__value">{value === null ? "—" : value.toLocaleString()}</b>
      <small className="lii-metric__hint">{hint}</small>
    </li>
  );
}

const CORRELATION_LABEL: Record<string, string> = {
  loading: "ANALYZING",
  unavailable: "UNAVAILABLE",
  no_data: "NO DATA",
  correlated: "CORRELATED",
};

/**
 * CORRELATION — the HSI-GNCE result for the focused resource set, plus the root
 * cause the platform actually recorded on the incident. A missing root cause
 * reads "Not established"; the zone never infers one.
 */
function CorrelationBlock({
  correlation,
  focus,
}: {
  correlation: InfraCorrelation;
  focus: InfraFocus;
}) {
  return (
    <>
      <p className="lii-subhead">Correlation</p>
      <div className="lii-corr">
        <span className={`lii-corr__state lii-corr__state--${correlation.state}`}>
          {CORRELATION_LABEL[correlation.state]}
        </span>
        <span className="lii-corr__detail">
          {correlation.state === "correlated"
            ? `${count(correlation.groupCount)} correlation group(s) across ${count(
                correlation.resourceCount,
              )} resource(s)`
            : correlation.state === "no_data"
              ? "The correlation service returned nothing for this resource set."
              : correlation.state === "unavailable"
                ? "The correlation service could not be reached."
                : "Asking the correlation service…"}
        </span>
      </div>

      <ul className="lii-corr__facts">
        <li>
          <span className="lii-corr__k">Root cause</span>
          <span className="lii-corr__v">{correlation.rootCause ?? "Not established"}</span>
        </li>
        <li>
          <span className="lii-corr__k">Member alerts</span>
          <span className="lii-corr__v">{count(correlation.memberAlerts)}</span>
        </li>
        <li>
          <span className="lii-corr__k">Correlated incident</span>
          <span className="lii-corr__v">
            {focus.incident ? (correlation.correlated ? "yes" : "no") : "N/A"}
          </span>
        </li>
      </ul>

      {correlation.basis.length > 0 && (
        <p className="lii-chips">
          {correlation.basis.map((item) => (
            <span className="lii-chip" key={item}>
              {item}
            </span>
          ))}
        </p>
      )}
      {correlation.limitations.slice(0, 2).map((note) => (
        <p className="lii-note lii-note--small" key={note}>
          {note}
        </p>
      ))}

      <div className="lii-focus__links">
        <Link className="lii-link" href="/monitoring">
          Open monitoring
        </Link>
        {focus.incident && (
          <Link className="lii-link" href={`/incidents/${focus.incident.id}`}>
            Open investigation
          </Link>
        )}
      </div>
    </>
  );
}

function ImpactPanel({ model }: { model: LiveInfraIntel }) {
  const { impact, correlation, focus } = model;

  return (
    <div className="hud-panel lii-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-icon" aria-hidden="true">
            <Network size={14} />
          </span>
          <h3>Impact &amp; Correlation</h3>
        </div>
        <Link className="lii-link" href="/infrastructure-topology">
          View dependencies
        </Link>
      </div>

      <div className="panel-body">
        {!focus && (
          <p className="lii-note">
            Impact is measured per resource. It appears here once the estate reports an active
            or degraded resource to focus on.
          </p>
        )}

        {focus && model.impactUnavailable && (
          <p className="lii-note">
            {model.state === "loading"
              ? "Measuring impact for the focused resource…"
              : "Impact data unavailable for the focused resource."}
          </p>
        )}

        {focus && !model.impactUnavailable && impact && (
          <>
            <ul className="lii-metrics">
              <Metric label="Direct impact" value={impact.direct} hint="depends on it, 1 hop" />
              <Metric label="Transitive impact" value={impact.transitive} hint="beyond the first hop" />
              <Metric
                label="Direct dependencies"
                value={impact.directDependencies}
                hint="recorded upstream"
              />
              <Metric label="Unresolved" value={impact.unresolved} hint="endpoint not a known resource" />
              <Metric label="Stale" value={impact.stale} hint="evidence past its freshness window" />
              <Metric label="Conflicts" value={impact.conflicts} hint="contradicting evidence" />
              <Metric label="Cycles" value={impact.cycles} hint="loops detected in traversal" />
            </ul>

            <p className="lii-subhead">Dependency path</p>
            {model.path.length === 0 ? (
              <p className="lii-note">No recorded dependency path for this resource.</p>
            ) : (
              <ol className="lii-path">
                {model.path.map((step, index) => (
                  <li key={`${step.depth}-${step.fromName}-${index}`} className="lii-path__step">
                    <span className="lii-path__depth">L{step.depth}</span>
                    <span className="lii-path__hops">
                      <b>{step.fromName}</b>
                      <span className="lii-path__arrow" aria-hidden="true">
                        →
                      </span>
                      <b>{step.toName ?? "unresolved endpoint"}</b>
                    </span>
                    <span className="lii-rel__meta">
                      <span className="lii-chip">{step.relationshipType.replace(/_/g, " ")}</span>
                      {step.evidence ? <span className="lii-chip">{step.evidence}</span> : null}
                      {step.inferred ? (
                        <span className="lii-chip lii-chip--warn">inferred</span>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}

        {focus && <CorrelationBlock correlation={correlation} focus={focus} />}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Evidence strip
 * ------------------------------------------------------------------------ */

function EvidenceBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="lii-ev">
      <p className="lii-ev__title">{title}</p>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <li className="lii-ev__row">
      <span className="lii-ev__k">{label}</span>
      <b className="lii-ev__v">{value}</b>
    </li>
  );
}

function EvidenceStrip({ model }: { model: LiveInfraIntel }) {
  const { telemetry, graph, collection, activity } = model;

  return (
    <div className="lii-strip">
      {/* TELEMETRY — the dashboard's existing sensor data, no new request. */}
      <EvidenceBlock title="Telemetry">
        <ul className="lii-ev__rows">
          <Row label="Healthy sensors" value={count(telemetry.healthy)} />
          <Row label="Recorded sensors" value={count(telemetry.total)} />
          <Row label="Newest observation" value={shortTime(telemetry.newestObservationAt)} />
        </ul>
      </EvidenceBlock>

      {/* GRAPH EVIDENCE — the canonical graph's own counts and state. */}
      <EvidenceBlock title="Graph evidence">
        <ul className="lii-ev__rows">
          <Row label="Relationships" value={count(graph?.relationships ?? null)} />
          <Row label="Inferred" value={count(graph?.inferred ?? null)} />
          <Row label="Graph state" value={graph ? graph.status.replace(/_/g, " ") : "N/A"} />
        </ul>
      </EvidenceBlock>

      {/* COLLECTION — the authoritative PHASE 3.1 collector-health contract. */}
      <EvidenceBlock title="Collection">
        <ul className="lii-ev__rows">
          <Row label="Healthy" value={count(collection.collectorsHealthy)} />
          <Row label="Degraded" value={count(collection.collectorsDegraded)} />
          <Row label="Failing" value={count(collection.collectorsFailing)} />
          <Row label="No run in window" value={count(collection.collectorsUnknown)} />
        </ul>
        <p className="lii-note lii-note--small">
          A source with no run recorded in the window is unknown, not failed.
        </p>
      </EvidenceBlock>

      {/* RECENT ACTIVITY — the dashboard's real event feed, or an honest note. */}
      <EvidenceBlock title="Recent activity">
        {activity.length === 0 ? (
          <p className="lii-note lii-note--small">Activity data unavailable.</p>
        ) : (
          <ul className="lii-activity">
            {activity.map((item) => (
              <li className="lii-activity__row" key={item.id}>
                <span className="lii-activity__time">{shortTime(item.timestamp)}</span>
                <span className="lii-activity__title">{item.title}</span>
                <span className="lii-activity__meta">
                  {item.type}
                  {item.resource ? ` · ${item.resource}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </EvidenceBlock>
    </div>
  );
}

/**
 * The zone itself. It renders BETWEEN Attention Required and Operational Health
 * (see UnifiedCommandCenter) so the page reads attention → context → impact →
 * evidence → action, and AI never outranks a measured signal.
 */
export default function LiveInfrastructureIntelligence({ nocData }: { nocData: DashboardData }) {
  const model = useLiveInfraIntel(nocData);

  return (
    <section
      id="infrastructure-intelligence"
      className="hs-zone hs-zone--intel"
      aria-labelledby="infra-intel-title"
    >
      <div className="hs-zone__header">
        <h2 id="infra-intel-title" className="hs-zone__title">
          Live Infrastructure Intelligence
        </h2>
        <p className="hs-zone__subtitle">
          The infrastructure involved, what it affects, and the evidence behind each claim
        </p>
      </div>

      <div className="lii-split">
        <ContextPanel model={model} />
        <ImpactPanel model={model} />
      </div>

      <EvidenceStrip model={model} />
    </section>
  );
}

