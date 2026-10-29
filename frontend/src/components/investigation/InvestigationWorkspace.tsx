"use client";

/**
 * InvestigationWorkspace (PHASE 3) — the engineer-facing Incident Investigation
 * Workspace for /incidents/{id}.
 *
 * It follows the investigation chain the platform is specified on:
 *   Header -> Executive summary -> Timeline -> Graph/impact -> Evidence ->
 *   Correlation -> Hypotheses -> Next diagnostic -> RCA -> Action ->
 *   Verification -> Limitations.
 *
 * Data policy (truth contract):
 *   - Every value comes from a documented endpoint. A field the backend did not
 *     report renders "N/A" with the reason; it is never replaced by 0, a dash or
 *     a plausible-looking default.
 *   - Correlation is not causation: the correlator's findings and candidate
 *     causes are reproduced verbatim, and workspace inference is labelled as
 *     inference rather than presented with equal authority.
 *   - Recovery is never asserted from a status change; the Verification section
 *     states the absence of independent evidence instead.
 *   - This view performs NO mutation. Action names the endpoint each transition
 *     requires and states plainly that nothing is executed from here.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Brain, CheckCircle2, Eye, Layers, XCircle } from "lucide-react";

import {
  getCollectorHealth,
  getDeviceImpactAnalysis,
  getIncident,
  type CollectorHealthList,
  type GraphResponse,
  type IncidentDetail,
} from "@/lib/api";
import { getResourceCapabilities, type CapabilityReport } from "@/lib/infrastructureIntelligence";
import { buildIncidentInvestigation, type InvestigationModel } from "@/lib/investigation/investigationModel";

/* ------------------------------------------------------------------ styles */

const MUTED = { color: "var(--muted)" } as const;
const BODY = { fontSize: 13, lineHeight: 1.5 } as const;
const DL: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(120px, 200px) 1fr",
  gap: "6px 14px",
  margin: 0,
  fontSize: 12.5,
};
const DT = { ...MUTED, fontSize: 11.5, letterSpacing: 0.3, textTransform: "uppercase" } as const;
const DD = { margin: 0, color: "var(--fg, #e8eef5)" } as const;
const TABLE: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 12 };
const TH = { textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--line)", ...MUTED, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 } as const;
const TD = { padding: "6px 8px", borderBottom: "1px solid var(--line)", verticalAlign: "top" } as const;

/* ------------------------------------------------------- presentational bits */

/** A value the backend did not report always renders N/A, never a substitute. */
function Text({ value, reason }: { value: string | null | undefined; reason?: string }) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) {
    return (
      <span style={MUTED} title={reason ?? "The backend did not report this value."}>
        N/A
      </span>
    );
  }
  return <>{text}</>;
}

function Num({ value, suffix = "" }: { value: number | null; suffix?: string }) {
  if (value === null) {
    return (
      <span style={MUTED} title="The backend did not report this value.">
        N/A
      </span>
    );
  }
  return (
    <>
      {value}
      {suffix}
    </>
  );
}

function Msg({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" | "bad" | "ok" }) {
  const color = tone === "bad" ? "var(--red)" : tone === "warn" ? "var(--amber)" : tone === "ok" ? "var(--green)" : "var(--muted)";
  const Icon = tone === "bad" ? XCircle : tone === "warn" ? AlertTriangle : tone === "ok" ? CheckCircle2 : Eye;
  return (
    <p style={{ ...BODY, color, display: "flex", gap: 8, alignItems: "flex-start", margin: "6px 0" }}>
      <Icon size={14} style={{ flex: "0 0 auto", marginTop: 2 }} aria-hidden />
      <span>{children}</span>
    </p>
  );
}

function Panel({ step, title, caption, children }: { step: number; title: string; caption?: string; children: React.ReactNode }) {
  return (
    <section className="hud-panel" style={{ marginBottom: 16, padding: 16 }} aria-label={`${step}. ${title}`}>
      <header style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: caption ? 4 : 10 }}>
        <span style={{ color: "var(--cyan)", fontSize: 11, letterSpacing: 1 }}>{String(step).padStart(2, "0")}</span>
        <h2 style={{ fontSize: 13, letterSpacing: 1, margin: 0 }}>{title}</h2>
      </header>
      {caption ? <p style={{ ...MUTED, fontSize: 11, margin: "0 0 10px", lineHeight: 1.5 }}>{caption}</p> : null}
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "contents" }}>
      <dt style={DT}>{label}</dt>
      <dd style={DD}>{children}</dd>
    </div>
  );
}

function Chip({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "bad" | "warn" | "ok" | "info" }) {
  const color = tone === "bad" ? "var(--red)" : tone === "warn" ? "var(--amber)" : tone === "ok" ? "var(--green)" : tone === "info" ? "var(--cyan)" : "var(--muted)";
  return (
    <span
      style={{
        color,
        border: `1px solid ${color}`,
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: 10.5,
        letterSpacing: 0.5,
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

function severityTone(severity: string): "bad" | "warn" | "info" | "muted" {
  const s = severity.toLowerCase();
  if (s === "critical" || s === "fatal" || s === "emergency") return "bad";
  if (s === "warning" || s === "major" || s === "minor") return "warn";
  if (s === "info" || s === "informational") return "info";
  return "muted";
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === "string" && error ? error : "The request failed.";
}

function absoluteTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  return `${t.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

function relativeTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function When({ iso }: { iso: string | null }) {
  const absolute = absoluteTime(iso);
  if (!absolute) {
    return (
      <span style={MUTED} title="The backend did not report this timestamp.">
        N/A
      </span>
    );
  }
  const relative = relativeTime(iso);
  return (
    <>
      {absolute}
      {relative ? <span style={MUTED}> · {relative}</span> : null}
    </>
  );
}

/* -------------------------------------------------------------- data loading */

type LoadState = "loading" | "ready" | "error";

export default function InvestigationWorkspace({
  incidentId,
  tenantId,
}: {
  incidentId: string;
  tenantId?: string;
}) {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [impact, setImpact] = useState<GraphResponse | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityReport | null>(null);
  const [collectors, setCollectors] = useState<CollectorHealthList | null>(null);
  // Each secondary source records WHY it is missing, so an unavailable panel can
  // never be mistaken for a clean result.
  const [impactUnavailable, setImpactUnavailable] = useState<string | null>(null);
  const [capabilityUnavailable, setCapabilityUnavailable] = useState<string | null>(null);
  const [collectorUnavailable, setCollectorUnavailable] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setState("loading");
      setError(null);
      setImpactUnavailable(null);
      setCapabilityUnavailable(null);
      setCollectorUnavailable(null);
      try {
        const detail = await getIncident(incidentId, tenantId);
        if (cancelled) return;
        setIncident(detail);
        setState("ready");

        const jobs: Promise<void>[] = [
          getCollectorHealth(tenantId)
            .then((r) => {
              if (!cancelled) setCollectors(r);
            })
            .catch((e) => {
              if (!cancelled) setCollectorUnavailable(messageOf(e));
            }),
        ];

        if (detail.device_id) {
          const deviceId = detail.device_id;
          jobs.push(
            getDeviceImpactAnalysis(deviceId, tenantId)
              .then((r) => {
                if (!cancelled) setImpact(r);
              })
              .catch((e) => {
                if (!cancelled) setImpactUnavailable(messageOf(e));
              }),
          );
          jobs.push(
            getResourceCapabilities(deviceId)
              .then((r) => {
                if (!cancelled) setCapabilities(r);
              })
              .catch((e) => {
                if (!cancelled) setCapabilityUnavailable(messageOf(e));
              }),
          );
        } else {
          setImpactUnavailable("the incident is not device-scoped, so there is no graph root");
          setCapabilityUnavailable("the incident is not device-scoped, so no resource could be queried");
        }

        await Promise.all(jobs);
      } catch (e) {
        if (!cancelled) {
          setError(messageOf(e));
          setState("error");
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [incidentId, tenantId, reloadKey]);

  const model: InvestigationModel | null = useMemo(() => {
    if (!incident) return null;
    return buildIncidentInvestigation({
      incident,
      impact: impact ?? undefined,
      capabilities: capabilities ?? undefined,
    });
  }, [incident, impact, capabilities]);

  if (state === "loading") {
    return (
      <Panel step={0} title="INVESTIGATION WORKSPACE" caption={`Loading incident ${incidentId}...`}>
        <Msg>Reading the incident, its member alerts, the impact graph and the device capabilities.</Msg>
      </Panel>
    );
  }

  if (state === "error") {
    return (
      <Panel step={0} title="INVESTIGATION WORKSPACE">
        <Msg tone="bad">
          The incident could not be loaded: <Text value={error} />. Nothing is inferred in its place - the
          workspace stays empty rather than presenting a partial picture as if it were complete.
        </Msg>
        <button
          type="button"
          onClick={() => setReloadKey((k) => k + 1)}
          style={{ marginTop: 8, padding: "6px 12px", fontSize: 12, cursor: "pointer" }}
        >
          Retry
        </button>
      </Panel>
    );
  }

  if (!model) {
    return (
      <Panel step={0} title="INVESTIGATION WORKSPACE">
        <Msg tone="warn">The API returned no incident body, so there is nothing to investigate.</Msg>
      </Panel>
    );
  }

  const m = model;
  const impactReason = m.blastRadius.unavailableReason ?? impactUnavailable;
  const capabilityReason = capabilityUnavailable;

  return (
    <InvestigationBody
      model={m}
      collectors={collectors}
      collectorUnavailable={collectorUnavailable}
      impactReason={impactReason}
      capabilityReason={capabilityReason}
    />
  );
}

/* ------------------------------------------------------------------ sections */

function InvestigationBody({
  model: m,
  collectors,
  collectorUnavailable,
  impactReason,
  capabilityReason,
}: {
  model: InvestigationModel;
  collectors: CollectorHealthList | null;
  collectorUnavailable: string | null;
  impactReason: string | null;
  capabilityReason: string | null;
}) {
  const observedCount = m.timeline.filter((e) => e.kind === "OBSERVED").length;
  const actionCount = m.timeline.filter((e) => e.kind === "ACTION").length;

  return (
    <div>
      {/* 1 ------------------------------------------------------------ HEADER */}
      <Panel step={1} title="INCIDENT HEADER">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
          <strong style={{ fontSize: 15 }}>{m.header.title}</strong>
          <Chip tone={severityTone(m.header.severity)}>{m.header.severity || "severity unknown"}</Chip>
          <Chip tone="muted">{m.header.status || "status unknown"}</Chip>
          <Chip tone={m.header.priority === null ? "muted" : "info"}>
            {m.header.priority === null ? "priority N/A" : `priority ${m.header.priority}`}
          </Chip>
        </div>
        <dl style={DL}>
          <Field label="Incident">
            <Text value={m.header.id} />
          </Field>
          <Field label="Owner">
            <Text value={m.header.owner} />
          </Field>
          <Field label="Team">
            <Text value={m.header.team} />
          </Field>
          <Field label="First observed">
            <When iso={m.header.firstObserved} />
          </Field>
          <Field label="Last updated">
            <When iso={m.header.lastUpdated} />
          </Field>
          <Field label={m.header.durationLabel}>
            <Text value={m.header.duration} />
            <span style={MUTED}>
              {" "}
              ({m.header.durationLabel === "duration" ? "measured from open to close" : "elapsed and still running"})
            </span>
          </Field>
          <Field label="Tenant">
            <Text value={m.header.tenantId} />
          </Field>
        </dl>
        {m.header.priority === null ? (
          <Msg tone="info">
            Priority is not exposed by the incident API. It is deliberately left N/A rather than derived from
            severity, which would be an invented ranking.
          </Msg>
        ) : null}
      </Panel>

      {/* 2 --------------------------------------------------- EXECUTIVE SUMMARY */}
      <Panel step={2} title="EXECUTIVE SUMMARY">
        <p style={BODY}>
          <strong>What happened: </strong>
          <Text value={m.summary.whatHappened} reason="The incident records no description." />
        </p>
        <p style={BODY}>
          <strong>Current state: </strong>
          {m.summary.currentState}
        </p>
        <p style={BODY}>
          <strong>Impact: </strong>
          <Text value={m.summary.impact} reason="No member alerts, occurrences or graph neighbours were reported." />
        </p>
        <p style={BODY}>
          <strong>Likely cause: </strong>
          {m.summary.likelyCause}{" "}
          <Chip tone={m.summary.causeBasis === "recorded" ? "ok" : "muted"}>
            {m.summary.causeBasis === "recorded" ? "recorded root cause" : "not established"}
          </Chip>
        </p>
        <p style={BODY}>
          <strong>Next action: </strong>
          {m.summary.nextAction}
        </p>
      </Panel>

      {/* 3 ------------------------------------------------------------ TIMELINE */}
      <Panel
        step={3}
        title="TIMELINE"
        caption={`${observedCount} observation(s) from monitoring and ${actionCount} recorded lifecycle transition(s), oldest first. Observations and operator/system actions are labelled separately.`}
      >
        {m.timeline.length === 0 ? (
          <Msg tone="warn">No timestamps were reported for this incident, so no timeline can be shown.</Msg>
        ) : (
          <table style={TABLE}>
            <thead>
              <tr>
                <th style={TH}>When</th>
                <th style={TH}>Type</th>
                <th style={TH}>Source</th>
                <th style={TH}>Entity</th>
                <th style={TH}>Event</th>
              </tr>
            </thead>
            <tbody>
              {m.timeline.map((e) => (
                <tr key={e.id}>
                  <td style={TD}>
                    <When iso={e.at} />
                  </td>
                  <td style={TD}>
                    <Chip tone={e.kind === "OBSERVED" ? "warn" : "info"}>{e.kind}</Chip>
                  </td>
                  <td style={TD}>
                    <Text value={e.source} />
                  </td>
                  <td style={TD}>
                    <Text value={e.entity} />
                  </td>
                  <td style={TD}>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* 4 --------------------------------------------------------- GRAPH / IMPACT */}
      <Panel step={4} title="GRAPH / IMPACT" caption={`Blast radius from the stored relationship graph. Source: ${m.blastRadius.source}.`}>
        {m.blastRadius.direct === null ? (
          <Msg tone="warn">
            Impact graph unavailable: <Text value={impactReason} />. The absence of graph data is not evidence
            that nothing else was affected.
          </Msg>
        ) : (
          <>
            <dl style={DL}>
              <Field label="Direct neighbours">
                <Num value={m.blastRadius.direct} />
              </Field>
              <Field label="Transitive nodes">
                <Num value={m.blastRadius.downstream} />
              </Field>
              <Field label="Relationship edges">
                <Num value={m.blastRadius.edges} />
              </Field>
              <Field label="By node type">
                {m.blastRadius.byType.length === 0
                  ? "N/A"
                  : m.blastRadius.byType.map((t) => `${t.type} (${t.count})`).join(", ")}
              </Field>
              <Field label="Relationship types">
                {m.blastRadius.relationships.length === 0
                  ? "N/A"
                  : m.blastRadius.relationships.map((t) => `${t.type} (${t.count})`).join(", ")}
              </Field>
            </dl>

            <h3 style={{ fontSize: 11.5, letterSpacing: 0.4, margin: "14px 0 6px" }}>
              EDGES TOUCHING THIS INCIDENT&apos;S NODE
            </h3>
            {m.blastRadius.touchingRoot.length === 0 ? (
              <Msg>No edge in the traversal touches the incident&apos;s own node.</Msg>
            ) : (
              <table style={TABLE}>
                <thead>
                  <tr>
                    <th style={TH}>From</th>
                    <th style={TH}>To</th>
                    <th style={TH}>Relationship</th>
                    <th style={TH}>Direction as stored</th>
                  </tr>
                </thead>
                <tbody>
                  {m.blastRadius.touchingRoot.slice(0, 25).map((e) => (
                    <tr key={e.id}>
                      <td style={TD}>
                        <Text value={e.from} />
                      </td>
                      <td style={TD}>
                        <Text value={e.to} />
                      </td>
                      <td style={TD}>
                        <Text value={e.type} />
                      </td>
                      <td style={TD}>
                        <Chip tone={e.direction === "inbound" ? "info" : "muted"}>{e.direction}</Chip>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {m.blastRadius.note ? <Msg tone="info">{m.blastRadius.note}</Msg> : null}
          </>
        )}
      </Panel>

      {/* 5 ------------------------------------------------------------- EVIDENCE */}
      <Panel
        step={5}
        title="EVIDENCE"
        caption="Measured observations attached to the incident. Expected/Actual are the values the monitoring source recorded; N/A means the source did not report them."
      >
        {m.evidence.length === 0 ? (
          <Msg tone="warn">
            No member alerts are attached, so there is no measured evidence. This is an absence of evidence, not
            evidence that the incident is benign.
          </Msg>
        ) : (
          <table style={TABLE}>
            <thead>
              <tr>
                <th style={TH}>Severity</th>
                <th style={TH}>Source</th>
                <th style={TH}>Entity</th>
                <th style={TH}>Observation</th>
                <th style={TH}>Metric</th>
                <th style={TH}>Expected</th>
                <th style={TH}>Actual</th>
                <th style={TH}>Observed</th>
                <th style={TH}>Quality</th>
              </tr>
            </thead>
            <tbody>
              {m.evidence.map((e) => (
                <tr key={e.id}>
                  <td style={TD}>
                    <Chip tone={severityTone(e.severity)}>{e.severity}</Chip>
                  </td>
                  <td style={TD}>
                    <Text value={e.source} />
                  </td>
                  <td style={TD}>
                    <Text value={e.entity} />
                  </td>
                  <td style={TD}>{e.observation}</td>
                  <td style={TD}>
                    <Text value={e.metric} />
                  </td>
                  <td style={TD}>{e.expected === null ? <span style={MUTED}>N/A</span> : e.expected}</td>
                  <td style={TD}>{e.actual === null ? <span style={MUTED}>N/A</span> : e.actual}</td>
                  <td style={TD}>
                    {e.freshness}
                    {e.at ? <span style={MUTED}> · {absoluteTime(e.at)}</span> : null}
                  </td>
                  <td style={TD}>
                    <Chip tone={e.quality === "HIGH" ? "ok" : "muted"}>{e.quality}</Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3 style={{ fontSize: 11.5, letterSpacing: 0.4, margin: "16px 0 6px" }}>MONITORING SOURCE HEALTH</h3>
        {collectors === null ? (
          <Msg tone="warn">
            Collector health could not be read ({<Text value={collectorUnavailable} />}), so it is unknown
            whether the sources above are still collecting.
          </Msg>
        ) : (
          <>
            <dl style={DL}>
              <Field label="Healthy">
                <Num value={collectors.healthy_count} suffix={` of ${collectors.collectors.length}`} />
              </Field>
              <Field label="Degraded">
                <Num value={collectors.degraded_count} />
              </Field>
              <Field label="Failing">
                <Num value={collectors.failing_count} />
              </Field>
              <Field label="Unknown">
                <Num value={collectors.unknown_count} />
              </Field>
            </dl>
            {collectors.collectors.filter((c) => c.current_state !== "healthy").length === 0 ? (
              <Msg tone="ok">Every reporting collector is healthy, so the evidence above is arriving normally.</Msg>
            ) : (
              <table style={TABLE}>
                <thead>
                  <tr>
                    <th style={TH}>Source</th>
                    <th style={TH}>State</th>
                    <th style={TH}>Success rate</th>
                    <th style={TH}>Runs</th>
                    <th style={TH}>Last success</th>
                    <th style={TH}>Last error</th>
                  </tr>
                </thead>
                <tbody>
                  {collectors.collectors
                    .filter((c) => c.current_state !== "healthy")
                    .map((c) => (
                      <tr key={c.source_id}>
                        <td style={TD}>
                          <Text value={c.source_name} />
                        </td>
                        <td style={TD}>
                          <Chip tone={c.current_state === "failing" ? "bad" : c.current_state === "degraded" ? "warn" : "muted"}>
                            {c.current_state}
                          </Chip>
                        </td>
                        <td style={TD}>
                          <Num value={c.success_rate_pct} suffix="%" />
                        </td>
                        <td style={TD}>
                          <Num value={c.total_runs} />
                        </td>
                        <td style={TD}>
                          <When iso={c.last_success_at} />
                        </td>
                        <td style={TD}>
                          <Text value={c.last_error} />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Panel>

      {/* 6 ---------------------------------------------------------- CORRELATION */}
      <Panel step={6} title="CORRELATION" caption={m.correlation.note}>
        {!m.correlation.available ? (
          <Msg tone="warn">
            No dependency-correlation finding is recorded on this incident. No linkage between these alerts is
            asserted here - co-occurrence alone is not treated as a cause.
          </Msg>
        ) : (
          m.correlation.findings.map((f) => (
            <div key={f.id} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10, marginBottom: 10 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
                <Layers size={14} aria-hidden />
                <strong style={{ fontSize: 13 }}>{f.capability}</strong>
                <Chip tone="muted">confidence {f.confidence ?? "N/A"}</Chip>
                {f.failureScope ? <Chip tone="warn">scope {f.failureScope}</Chip> : null}
              </div>
              <dl style={DL}>
                <Field label="Dependencies">
                  {f.dependencyIdentifiers.length > 0 ? f.dependencyIdentifiers.join(", ") : "N/A"}
                </Field>
                <Field label="Healthy">
                  {f.healthy.length > 0 ? f.healthy.join(", ") : "N/A"}
                </Field>
                <Field label="Investigated / failed">
                  {f.failed.length > 0 ? f.failed.join(", ") : "N/A"}
                </Field>
                <Field label="Unknown">
                  {f.unknown.length > 0 ? f.unknown.join(", ") : "N/A"}
                </Field>
                <Field label="Excluded conclusions">
                  {f.excludedConclusions.length > 0 ? f.excludedConclusions.join(", ") : "N/A"}
                </Field>
              </dl>
              {f.candidateCauses.length > 0 ? (
                <p style={BODY}>
                  <strong>Candidate causes: </strong>
                  {f.candidateCauses.join(" · ")}
                </p>
              ) : null}
              {f.suggestedChecks.length > 0 ? (
                <p style={{ ...BODY, ...MUTED }}>
                  <strong>Suggested checks: </strong>
                  {f.suggestedChecks.join(" · ")}
                </p>
              ) : null}
            </div>
          ))
        )}

        <h3 style={{ fontSize: 11.5, letterSpacing: 0.4, margin: "14px 0 6px" }}>RECORDED CANDIDATE CAUSES</h3>
        {m.correlation.candidates.length === 0 ? (
          <Msg>No candidate cause has been recorded by the correlator.</Msg>
        ) : (
          <table style={TABLE}>
            <thead>
              <tr>
                <th style={TH}>Candidate</th>
                <th style={TH}>Confidence (as recorded)</th>
                <th style={TH}>Capability</th>
              </tr>
            </thead>
            <tbody>
              {m.correlation.candidates.map((c) => (
                <tr key={c.id}>
                  <td style={TD}>{c.statement}</td>
                  <td style={TD}>{c.confidence === null ? <span style={MUTED}>N/A</span> : c.confidence}</td>
                  <td style={TD}>{c.capability}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3 style={{ fontSize: 11.5, letterSpacing: 0.4, margin: "14px 0 6px" }}>ACTION BASIS (SOP CONTEXT)</h3>
        {m.correlation.sopContextAvailable ? (
          m.correlation.sopMatchKeys.length > 0 ? (
            <p style={BODY}>
              The correlator matched the incident on: <strong>{m.correlation.sopMatchKeys.join(", ")}</strong>
            </p>
          ) : (
            <Msg tone="warn">SOP context is present but records no match keys.</Msg>
          )
        ) : (
          <Msg>No SOP context is recorded, so no matched procedure can be shown as the action basis.</Msg>
        )}
      </Panel>

      {/* 7 ---------------------------------------------------------- HYPOTHESES */}
      <Panel
        step={7}
        title="HYPOTHESES"
        caption="Candidate explanations with the evidence for and against them. Workspace inference is labelled as inference; nothing here is a verdict, and an unlisted explanation is not ruled out."
      >
        {m.hypotheses.map((h) => (
          <div key={h.id} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10, marginBottom: 10 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <Brain size={14} aria-hidden />
              <strong style={{ fontSize: 13 }}>{h.statement}</strong>
              <Chip
                tone={
                  h.confidence === "HIGH" ? "bad" : h.confidence === "MEDIUM" ? "warn" : h.confidence === "LOW" ? "info" : "muted"
                }
              >
                {h.confidence}
              </Chip>
              <Chip tone="muted">
                {h.provenance === "dependency_intelligence" ? "correlator output" : "workspace inference"}
              </Chip>
            </div>
            <dl style={DL}>
              <Field label="Supporting evidence">
                <Bullets items={h.supporting} empty="No supporting evidence is attached." />
              </Field>
              <Field label="Contradicting evidence">
                <Bullets items={h.contradicting} empty="None recorded." />
              </Field>
              <Field label="What is missing">
                <Bullets items={h.missing} empty="N/A" />
              </Field>
            </dl>
          </div>
        ))}
      </Panel>

      {/* 8 ------------------------------------------------------ NEXT DIAGNOSTIC */}
      <Panel
        step={8}
        title="NEXT DIAGNOSTIC"
        caption={`Read-only tests. Runnability comes from the device's real capability report${
          capabilityReason ? ` (capabilities were not read: ${capabilityReason})` : ""
        }. Nothing is executed from this page.`}
      >
        <table style={TABLE}>
          <thead>
            <tr>
              <th style={TH}>Test</th>
              <th style={TH}>Runnability</th>
              <th style={TH}>Proposed by</th>
              <th style={TH}>Purpose</th>
              <th style={TH}>Expected result</th>
              <th style={TH}>Note</th>
            </tr>
          </thead>
          <tbody>
            {m.diagnostics.map((d) => (
              <tr key={d.key}>
                <td style={TD}>
                  {d.label}
                  {d.capability ? <span style={MUTED}> · capability: {d.capability}</span> : null}
                </td>
                <td style={TD}>
                  <Chip tone={d.availability === "supported" ? "ok" : d.availability === "unsupported" ? "bad" : "muted"}>
                    {d.availability}
                  </Chip>
                </td>
                <td style={TD}>
                  <Chip tone="muted">
                    {d.origin === "dependency_intelligence" ? "correlator" : "catalogue"}
                  </Chip>
                </td>
                <td style={TD}>{d.purpose}</td>
                <td style={TD}>{d.expected}</td>
                <td style={TD}>
                  <Text value={d.reason} reason="No caveat was supplied for this test." />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Msg tone="info">
          A test reported as &quot;unknown&quot; has not been shown to be unsupported - it has not been shown to be
          available either. No diagnostic is run, and no result is assumed, by this view.
        </Msg>
      </Panel>

      {/* 9 -------------------------------------------------- ROOT CAUSE ANALYSIS */}
      <Panel step={9} title="ROOT CAUSE ANALYSIS">
        {m.recordedRootCause.summary ? (
          <>
            <p style={BODY}>{m.recordedRootCause.summary}</p>
            <dl style={DL}>
              <Field label="Root-cause alert">
                <Text
                  value={m.recordedRootCause.alertId}
                  reason="The backend did not name a root-cause alert for this incident."
                />
              </Field>
              <Field label="Correlation type">
                <Text
                  value={m.recordedRootCause.correlationType}
                  reason="The backend did not report how it grouped these alerts."
                />
              </Field>
            </dl>
          </>
        ) : (
          <Msg tone="warn">
            No root cause is recorded for this incident. Nothing is asserted in its place: the candidate causes
            above remain candidates, and this workspace does not promote one of them to a conclusion.
          </Msg>
        )}
      </Panel>

      {/* 10 -------------------------------------------------------------- ACTION */}
      <Panel
        step={10}
        title="ACTION"
        caption="The status transitions the API exposes. This view performs no mutation: it holds no operator identity, and the backend is the authority on which transition the current status permits."
      >
        <table style={TABLE}>
          <thead>
            <tr>
              <th style={TH}>Transition</th>
              <th style={TH}>Endpoint</th>
              <th style={TH}>Requires</th>
              <th style={TH}>Expected from</th>
              <th style={TH}>Relevance</th>
            </tr>
          </thead>
          <tbody>
            {m.actions.map((a) => (
              <tr key={a.key} style={a.isExpectedNext ? { background: "rgba(53,199,240,0.06)" } : undefined}>
                <td style={TD}>{a.label}</td>
                <td style={TD}>
                  <code style={{ fontSize: 11.5 }}>{`${a.method} ${a.endpoint}`}</code>
                </td>
                <td style={TD}>
                  {a.requiredInputs.length > 0 ? a.requiredInputs.join(", ") : <span style={MUTED}>no body required</span>}
                </td>
                <td style={TD}>
                  {a.expectedFrom.join(", ")}
                </td>
                <td style={TD}>
                  {a.isExpectedNext ? <Chip tone="info">expected next</Chip> : <Chip tone="muted">not for this status</Chip>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Msg tone="info">
          Transitions are attributed by the server to the signed-in user; no actor id is sent. Run them from the
          Incidents page, which acts on behalf of the signed-in operator.
        </Msg>
      </Panel>

      {/* 11 -------------------------------------------------------- VERIFICATION */}
      <Panel
        step={11}
        title="VERIFICATION"
        caption="Recovery is a claim until it is checked. This section reports what was recorded and what could not be independently confirmed."
      >
        <dl style={DL}>
          <Field label="Recovery recorded">
            <When iso={m.header.recoveredAt} />
          </Field>
          <Field label="Resolved">
            <When iso={m.header.resolvedAt} />
          </Field>
          <Field label="Independent verification">
            <Chip tone="warn">not available</Chip>
          </Field>
        </dl>
        {m.header.recoveredAt ? (
          <Msg tone="warn">
            A recovery timestamp exists on this incident. It records that recovery was <em>declared</em>, not that
            it was measured - no post-recovery telemetry comparison exists in this workspace yet.
          </Msg>
        ) : (
          <Msg>No recovery has been recorded on this incident.</Msg>
        )}
        <Msg tone="info">
          Before closing, confirm the original symptom is absent in fresh telemetry. Until then, treat the incident
          as unverified regardless of its status.
        </Msg>
      </Panel>

      {/* 12 ------------------------------------------- LIMITATIONS & UNKNOWN DATA */}
      <Panel
        step={12}
        title="LIMITATIONS & UNKNOWN DATA"
        caption="Everything this workspace could not establish, stated explicitly so that an absence of data is never read as an absence of risk."
      >
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {m.gaps.map((g) => (
            <li key={g} style={{ ...BODY, marginBottom: 4 }}>
              {g}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

/** Renders a list, or an explicit statement when the list is empty. */
function Bullets({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <span style={MUTED}>{empty}</span>;
  return (
    <ul style={{ margin: 0, paddingLeft: 16 }}>
      {items.map((item, i) => (
        <li key={`${i}-${item.slice(0, 12)}`}>{item}</li>
      ))}
    </ul>
  );
}
