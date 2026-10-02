/**
 * Incident Investigation Workspace — normalized view model (PHASE 3).
 *
 * Pure functions only: they turn data the backend already returned into the
 * investigation view. Nothing is fetched here and nothing is invented.
 *
 * TRUTH RULES
 *   - Every statement is either OBSERVED (a field the backend reported) or
 *     derived deterministically from observed fields; the two are labelled.
 *   - A missing value is `null` and renders N/A. No confidence, impact,
 *     probability or timestamp is fabricated when the data is absent.
 *   - Correlation is not causation: candidate explanations are listed WITH the
 *     evidence for and against them, never as a verdict.
 *   - Recovery is never asserted from a status change alone: that requires
 *     verification evidence, which is reported as a gap when absent.
 */
import type { Alert, GraphResponse, Incident, IncidentDetail } from "@/lib/api";
import type { CapabilityReport } from "@/lib/infrastructureIntelligence";

/** Kind of a timeline entry — the audit chain the directive requires. */
export type TimelineKind = "OBSERVED" | "ACTION";

export interface TimelineEvent {
  id: string;
  at: string;
  kind: TimelineKind;
  /** Where the fact came from (API payload field / lifecycle transition). */
  source: string;
  entity: string | null;
  summary: string;
  severity: string | null;
}

export interface EvidenceItem {
  id: string;
  source: string;
  entity: string | null;
  observation: string;
  /** Expected value, e.g. the alert threshold (null = not reported). */
  expected: number | null;
  /** Actual value the backend recorded (null = not reported). */
  actual: number | null;
  metric: string | null;
  at: string | null;
  freshness: string;
  severity: string;
  /** Evidence QUALITY (are the values present?), not causal confidence. */
  quality: "HIGH" | "LOW";
}

export interface Hypothesis {
  id: string;
  statement: string;
  confidence: "LOW" | "MEDIUM" | "HIGH" | "INSUFFICIENT";
  supporting: string[];
  contradicting: string[];
  missing: string[];
  /** Recorded correlator output vs. deterministic workspace inference. */
  provenance: "dependency_intelligence" | "workspace_inference";
}

export interface DiagnosticSuggestion {
  key: string;
  label: string;
  /** Capability the backend must support for this test to be runnable. */
  capability: string | null;
  purpose: string;
  risk: "READ_ONLY";
  availability: "supported" | "unsupported" | "unknown";
  reason: string | null;
  expected: string;
  /**
   * Where the suggestion came from: the backend's own Dependency Intelligence
   * suggestion, or this workspace's capability-gated catalogue. Recorded so the
   * two are never confused for one another.
   */
  origin: "dependency_intelligence" | "capability_catalogue";
}

/** One Dependency Intelligence finding stored on the incident. */
export interface CorrelationFinding {
  id: string;
  capability: string;
  confidence: string | null;
  failureScope: string | null;
  dependencyIdentifiers: string[];
  healthy: string[];
  failed: string[];
  unknown: string[];
  excludedConclusions: string[];
  candidateCauses: string[];
  suggestedChecks: string[];
}

/** A candidate cause the correlator recorded, kept in its own vocabulary. */
export interface RecordedCandidate {
  id: string;
  statement: string;
  /** The correlator's own confidence value, verbatim - never re-labelled. */
  confidence: string | null;
  capability: string;
}

/** The root cause the backend or an operator recorded (not a workspace claim). */
export interface RecordedRootCause {
  summary: string | null;
  /** The member alert the backend identified as the root cause, if any. */
  alertId: string | null;
  /** How the backend grouped the alerts (its own vocabulary), if reported. */
  correlationType: string | null;
}

/**
 * A status transition this workspace knows is exposed by the API. `expectedFrom`
 * is the workspace's expectation of which statuses permit it - the BACKEND is the
 * authority and may reject a call, which is why nothing is executed here.
 */
export interface AvailableTransition {
  key: string;
  label: string;
  method: "POST";
  endpoint: string;
  requiredInputs: string[];
  expectedFrom: string[];
  isExpectedNext: boolean;
}

export interface CorrelationSection {
  /** True when the incident actually carries recorded correlation findings. */
  available: boolean;
  findings: CorrelationFinding[];
  /** The correlator's candidate causes, with its own confidence values. */
  candidates: RecordedCandidate[];
  /** SOP context the correlator matched on - the recorded "Action" basis. */
  sopMatchKeys: string[];
  sopContextAvailable: boolean;
  note: string;
}

export interface BlastRadius {
  direct: number | null;
  downstream: number | null;
  edges: number | null;
  byType: { type: string; count: number }[];
  /** Relationship types present in the traversal, verbatim from the edges. */
  relationships: { type: string; count: number }[];
  /**
   * Edges touching the incident's own node, with the direction AS STORED
   * (`source -> target`). `inbound` means the other node points at the root;
   * no causal claim is made from this.
   */
  touchingRoot: { id: string; from: string; to: string; type: string; direction: "inbound" | "outbound" }[];
  source: string;
  note: string | null;
  unavailableReason: string | null;
}

export interface InvestigationSummary {
  whatHappened: string | null;
  currentState: string;
  impact: string | null;
  likelyCause: string;
  /** Never numeric: the backend's own RCA presence, or "not established". */
  causeBasis: "recorded" | "not_established";
  nextAction: string;
}

/* ------------------------------------------------------------------ helpers */

/** Lifecycle timestamps the incident API records; all are OBSERVED facts. */
const LIFECYCLE: { field: keyof Incident; label: string }[] = [
  { field: "opened_at", label: "Incident opened" },
  { field: "acknowledged_at", label: "Acknowledged" },
  { field: "investigating_at", label: "Investigation started" },
  { field: "monitoring_at", label: "Set to monitoring" },
  { field: "recovered_at", label: "Recovery recorded" },
  { field: "resolved_at", label: "Resolved" },
  { field: "closed_at", label: "Closed" },
  { field: "archived_at", label: "Archived" },
];

const CLOSED_INCIDENT_STATUS = new Set(["resolved", "closed", "archived"]);

/** Strip the markup PRTG and similar providers embed in free text. */
function plainText(value: string | null | undefined): string | null {
  if (!value) return null;
  const text = value
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
  return text.length ? text : null;
}

function toMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : t;
}

/** String projection of unknown[]. Non-string members are dropped, not coerced. */
function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string" && v.trim() !== "");
}

/** `supporting_evidence` is a dict with an `evidence` list (older rows may hold
 *  a bare list). Anything else yields [] rather than a guess. */
function evidenceList(raw: unknown): Record<string, unknown>[] {
  const source = Array.isArray(raw)
    ? raw
    : raw !== null && typeof raw === "object"
      ? (raw as Record<string, unknown>).evidence
      : null;
  return Array.isArray(source)
    ? source.filter((e): e is Record<string, unknown> => e !== null && typeof e === "object")
    : [];
}

const DEP_SOURCE = "dependency_intelligence";
const DEP_SOP_SOURCE = `${DEP_SOURCE}_sop_context`;

/** Elapsed (open) or measured (closed) duration from a real start/end pair. */
function durationText(startIso: string, endIso: string | null): string {
  const start = toMs(startIso);
  if (start === null) return "—";
  const end = toMs(endIso) ?? Date.now();
  const secs = Math.max(0, Math.round((end - start) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** Age of a timestamp; "not reported" when absent (never a invented time). */
function freshness(iso: string | null | undefined): string {
  const t = toMs(iso);
  if (t === null) return "not reported";
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** The incident's own status, mapped to the investigation vocabulary. */
function investigationPhase(status: string): string {
  const s = (status || "").toLowerCase();
  switch (s) {
    case "open": return "NEW - not yet acknowledged";
    case "acknowledged": return "TRIAGING";
    case "investigating": return "INVESTIGATING";
    case "monitoring": return "MONITORING";
    case "recovered": return "RECOVERED - not yet verified";
    case "resolved": return "RESOLVED";
    case "closed": return "CLOSED";
    case "archived": return "ARCHIVED";
    default: return s ? s.toUpperCase() : "UNKNOWN";
  }
}

/**
 * Guidance only (product policy, not data): what an engineer should do next
 * given the incident's own status and the evidence actually present.
 */
function nextAction(status: string, hasRecordedRca: boolean, evidenceCount: number): string {
  const s = (status || "").toLowerCase();
  if (evidenceCount === 0 && !CLOSED_INCIDENT_STATUS.has(s)) {
    return "No evidence is attached yet - confirm the monitoring source is still collecting before investigating.";
  }
  switch (s) {
    case "open":
      return "Acknowledge the incident to start the investigation.";
    case "acknowledged":
      return "Review the correlated alerts and run the read-only diagnostics that separate the leading hypotheses.";
    case "investigating":
      return hasRecordedRca
        ? "Confirm the recorded root cause with an independent verification diagnostic."
        : "Compare the competing hypotheses and run the test that distinguishes them.";
    case "monitoring":
      return "Verify recovery with fresh telemetry before resolving.";
    case "recovered":
      return "Confirm the original symptom has not returned, then resolve.";
    default:
      return "Review the evidence and record the outcome.";
  }
}

export interface InvestigationModel {
  header: {
    id: string;
    title: string;
    severity: string;
    status: string;
    /** The incident API carries no priority field, so this stays null. */
    priority: string | null;
    owner: string | null;
    team: string | null;
    tenantId: string;
    firstObserved: string;
    lastUpdated: string;
    /** Elapsed while open, or measured duration once closed. */
    duration: string;
    durationLabel: string;
    /** Recorded recovery / resolution timestamps (null = never recorded). */
    recoveredAt: string | null;
    resolvedAt: string | null;
  };
  /** The root cause the backend or an operator RECORDED (may be absent). */
  recordedRootCause: RecordedRootCause;
  summary: InvestigationSummary;
  timeline: TimelineEvent[];
  evidence: EvidenceItem[];
  correlation: CorrelationSection;
  blastRadius: BlastRadius;
  hypotheses: Hypothesis[];
  diagnostics: DiagnosticSuggestion[];
  /** Status transitions the API exposes; none are executed from this view. */
  actions: AvailableTransition[];
  /** Everything the model could NOT establish, stated explicitly. */
  gaps: string[];
}

/** Diagnostics the platform can run, and the capability each one needs. */
const DIAGNOSTIC_CATALOGUE: {
  key: string;
  label: string;
  capability: string | null;
  purpose: string;
  expected: string;
}[] = [
  { key: "ping", label: "Ping host", capability: "ping", purpose: "Confirm the host answers ICMP (reachability only, not health).", expected: "Host replies within the configured timeout." },
  { key: "tcp", label: "TCP port test", capability: "tcp", purpose: "Confirm the service port accepts a connection.", expected: "Connection established on the service port." },
  { key: "dns", label: "DNS lookup", capability: "dns", purpose: "Confirm name resolution for the incident's target.", expected: "The name resolves to the expected address." },
  { key: "snmp", label: "SNMP read", capability: "snmp", purpose: "Read interface/CPU/memory counters from the device.", expected: "Counters return and are fresh." },
  { key: "ssh", label: "SSH / CLI read", capability: "ssh", purpose: "Read device state through the management CLI.", expected: "CLI responds and returns device state." },
  { key: "rest", label: "HTTP health read", capability: "rest", purpose: "Read the device/application health endpoint.", expected: "Health endpoint answers with a healthy status." },
];

/**
 * Status transitions the API exposes (verified against the incidents router,
 * which defines exactly these POST routes). `expectedFrom` encodes the workspace's
 * lifecycle expectation only; `acknowledge` is the sole transition that requires
 * an actor id, which this view does not hold, so it is never sent from here.
 */
const TRANSITIONS: Omit<AvailableTransition, "isExpectedNext">[] = [
  { key: "acknowledge", label: "Acknowledge", method: "POST", endpoint: "/api/incidents/{id}/acknowledge", requiredInputs: ["acknowledged_by (actor id)"], expectedFrom: ["open"] },
  { key: "investigate", label: "Start investigation", method: "POST", endpoint: "/api/incidents/{id}/investigate", requiredInputs: [], expectedFrom: ["open", "acknowledged"] },
  { key: "monitor", label: "Set to monitoring", method: "POST", endpoint: "/api/incidents/{id}/monitor", requiredInputs: [], expectedFrom: ["investigating", "recovered"] },
  { key: "recover", label: "Record recovery", method: "POST", endpoint: "/api/incidents/{id}/recover", requiredInputs: [], expectedFrom: ["investigating", "monitoring"] },
  { key: "resolve", label: "Resolve", method: "POST", endpoint: "/api/incidents/{id}/resolve", requiredInputs: [], expectedFrom: ["recovered", "monitoring"] },
  { key: "close", label: "Close", method: "POST", endpoint: "/api/incidents/{id}/close", requiredInputs: [], expectedFrom: ["resolved"] },
  { key: "archive", label: "Archive", method: "POST", endpoint: "/api/incidents/{id}/archive", requiredInputs: [], expectedFrom: ["closed", "resolved"] },
];

export function buildIncidentInvestigation(input: {
  /** GET /api/incidents/{id} - carries the incident AND its member alerts. */
  incident: IncidentDetail;
  /** Optional override; defaults to the incident's own member alerts. */
  alerts?: Alert[] | undefined;
  /** GET /api/assets/devices/{id}/impact-analysis (real graph traversal). */
  impact?: GraphResponse | undefined;
  /** GET /api/infrastructure/universal/resources/{id}/capabilities. */
  capabilities?: CapabilityReport | undefined;
}): InvestigationModel {
  const { incident, alerts, impact, capabilities } = input;
  const incidentAlerts = alerts ?? incident.alerts ?? [];

  /* ---- evidence: one item per member alert (real expected vs actual) ------ */
  const evidence: EvidenceItem[] = incidentAlerts.slice(0, 25).map((a, idx) => ({
    id: `evidence-${a.id}-${idx}`,
    source: a.source || "monitoring",
    entity: a.title || null,
    observation: plainText(a.message) ?? plainText(a.description) ?? a.title ?? "alert raised",
    expected: a.threshold_value,
    actual: a.triggered_value,
    metric: a.metric_type ?? null,
    at: a.triggered_at ?? null,
    freshness: freshness(a.last_occurred_at ?? a.triggered_at),
    severity: (a.severity || "info").toLowerCase(),
    // Evidence QUALITY: were the measured values actually reported?
    quality: a.triggered_value !== null ? "HIGH" : "LOW",
  }));

  /* ---- correlation: Dependency Intelligence findings the incident stores -- */
  // `supporting_evidence.evidence` is populated server-side by the correlator.
  // Entries whose source is exactly "dependency_intelligence" are dependency
  // findings; those suffixed "_sop_context" carry the SOP match keys. Entries
  // with no source are the raw member-alert snapshots (already rendered above).
  const rawEvidence = evidenceList(incident.supporting_evidence);
  const depEntries = rawEvidence.filter((e) => String(e.source ?? "").startsWith(DEP_SOURCE));
  const findings: CorrelationFinding[] = depEntries
    .filter((e) => String(e.source) === DEP_SOURCE)
    .map((e, idx) => ({
      id: `finding-${String(e.capability ?? idx)}-${idx}`,
      capability: String(e.capability ?? "unnamed capability"),
      confidence: typeof e.confidence === "number" || typeof e.confidence === "string" ? String(e.confidence) : null,
      failureScope: typeof e.failure_scope === "string" ? e.failure_scope : null,
      dependencyIdentifiers: stringList(e.dependency_identifiers),
      healthy: stringList(e.healthy_dependencies),
      failed: stringList(e.failed_dependencies),
      unknown: stringList(e.unknown_dependencies),
      excludedConclusions: stringList(e.excluded_conclusions),
      candidateCauses: stringList(e.candidate_causes),
      suggestedChecks: stringList(e.suggested_checks),
    }));
  const sopContext = depEntries.find((e) => String(e.source) === DEP_SOP_SOURCE);
  const sopMatchKeys = stringList(sopContext?.match_keys);

  const correlation: CorrelationSection = {
    available: findings.length > 0,
    findings,
    candidates: findings.flatMap((f) =>
      f.candidateCauses.map((statement, idx) => ({
        id: `candidate-${f.id}-${idx}`,
        statement,
        confidence: f.confidence,
        capability: f.capability,
      })),
    ),
    sopMatchKeys,
    sopContextAvailable: Boolean(sopContext),
    note:
      findings.length > 0
        ? "Findings are the correlator's recorded output for this incident - reproduced verbatim, not recomputed here."
        : "No dependency-correlation finding is recorded for this incident; no correlation is claimed in its absence.",
  };

  /* ---- timeline: observed alerts + recorded lifecycle transitions -------- */
  const timeline: TimelineEvent[] = [];
  for (const a of incidentAlerts) {
    if (a.triggered_at) {
      timeline.push({
        id: `alert-first-${a.id}`,
        at: a.triggered_at,
        kind: "OBSERVED",
        source: a.source || "monitoring",
        entity: a.title ?? null,
        summary: `First observation: ${plainText(a.message) ?? a.title ?? "alert"}`,
        severity: (a.severity || "info").toLowerCase(),
      });
    }
    if (a.last_occurred_at && a.last_occurred_at !== a.triggered_at) {
      timeline.push({
        id: `alert-last-${a.id}`,
        at: a.last_occurred_at,
        kind: "OBSERVED",
        source: a.source || "monitoring",
        entity: a.title ?? null,
        summary: `Latest occurrence (x${a.occurrence_count ?? 1})`,
        severity: (a.severity || "info").toLowerCase(),
      });
    }
    if (a.acknowledged_at) {
      timeline.push({
        id: `alert-ack-${a.id}`,
        at: a.acknowledged_at,
        kind: "ACTION",
        source: a.source || "monitoring",
        entity: a.title ?? null,
        summary: "Alert acknowledged",
        severity: null,
      });
    }
    if (a.resolved_at) {
      timeline.push({
        id: `alert-res-${a.id}`,
        at: a.resolved_at,
        kind: "ACTION",
        source: a.source || "monitoring",
        entity: a.title ?? null,
        summary: a.auto_resolved ? "Alert auto-resolved by the provider" : "Alert resolved",
        severity: null,
      });
    }
  }
  for (const step of LIFECYCLE) {
    const value = incident[step.field];
    if (typeof value === "string" && value) {
      timeline.push({
        id: `lifecycle-${String(step.field)}`,
        at: value,
        kind: "ACTION",
        source: "incident lifecycle",
        entity: incident.assigned_to ?? incident.assigned_team ?? null,
        summary: step.label,
        severity: null,
      });
    }
  }
  timeline.sort((a, b) => (toMs(a.at) ?? 0) - (toMs(b.at) ?? 0));

  /* ---- blast radius: real graph traversal, counts only ------------------- */
  const nodes = impact?.nodes ?? [];
  const byTypeMap = new Map<string, number>();
  for (const n of nodes) byTypeMap.set(n.type, (byTypeMap.get(n.type) ?? 0) + 1);
  const edges = impact?.edges ?? [];
  const relMap = new Map<string, number>();
  for (const e of edges) relMap.set(e.relationship_type, (relMap.get(e.relationship_type) ?? 0) + 1);
  const rootId = impact?.root_id ?? null;
  const touchingRoot = rootId
    ? edges
        .filter((e) => e.source === rootId || e.target === rootId)
        .map((e, idx) => ({
          id: `edge-${e.source}-${e.target}-${idx}`,
          from: e.source,
          to: e.target,
          type: e.relationship_type,
          direction: (e.source === rootId ? "outbound" : "inbound") as "inbound" | "outbound",
        }))
    : [];
  const blastRadius: BlastRadius = impact
    ? {
        direct: nodes.filter((n) => n.depth === 1).length,
        downstream: nodes.filter((n) => n.depth > 1).length,
        edges: impact.edges.length,
        byType: [...byTypeMap.entries()]
          .map(([type, count]) => ({ type, count }))
          .sort((a, b) => b.count - a.count),
        relationships: [...relMap.entries()]
          .map(([type, count]) => ({ type, count }))
          .sort((a, b) => b.count - a.count),
        touchingRoot,
        source: "GET /api/assets/devices/{deviceId}/impact-analysis",
        note: "Counts are nodes/edges from the stored relationship graph. UNAFFECTED entities cannot be derived from the impact closure.",
        unavailableReason: null,
      }
    : {
        direct: null,
        downstream: null,
        edges: null,
        byType: [],
        relationships: [],
        touchingRoot: [],
        source: "GET /api/assets/devices/{deviceId}/impact-analysis",
        note: null,
        unavailableReason: incident.device_id
          ? "impact graph unavailable - the endpoint did not answer"
          : "the incident is not device-scoped, so there is no graph root",
      };

  /* ---- hypotheses: candidate explanations from the measured evidence ----- */
  // Deterministic keyword matching over the member alerts. Each candidate
  // carries the ACTUAL alert titles that support it, what it cannot account for,
  // and - where the evidence genuinely contradicts it - why.
  const alertText = (a: Alert) =>
    `${a.metric_type ?? ""} ${a.title ?? ""} ${a.message ?? ""} ${a.description ?? ""}`.toLowerCase();

  const candidates: { id: string; statement: string; match: RegExp; missing: string[] }[] = [
    {
      id: "h-wan",
      statement: "WAN path degradation upstream of the service (loss / latency / SLA breach)",
      match: /loss|latency|jitter|sla|wan|circuit|packet/,
      missing: [
        "SD-WAN controller SLA history and ISP-side circuit telemetry",
        "whether a failover occurred (expected resilience vs failed failover)",
      ],
    },
    {
      id: "h-host",
      statement: "The monitored device or host became unreachable (host / access-layer fault)",
      match: /availability|unreachable|down|timeout|no response|icmp|ping/,
      missing: ["a host-level reachability check (ping / WMI / SSH) has not been run in this workspace"],
    },
    {
      id: "h-app",
      statement: "Application or service failure (HTTP / TLS / API) rather than the network path",
      match: /http|status|tls|cert|api|[45]\d\d|application|service/,
      missing: ["the application health response and its dependency health at the incident window"],
    },
    {
      id: "h-resource",
      statement: "Resource exhaustion on the device (CPU / memory / disk) rather than connectivity loss",
      match: /cpu|memory|disk|storage|i\/o|process|queue/,
      missing: ["resource counters for the incident time window"],
    },
  ];

  const evidenceFresh = evidence.some((e) => e.at !== null);
  const hypotheses: Hypothesis[] = candidates
    .map((c) => {
      const matched = incidentAlerts.filter((a) => c.match.test(alertText(a)));
      const supporting = matched
        .slice(0, 3)
        .map((a) => `${a.title ?? "alert"} (${(a.severity || "info").toLowerCase()}, ${freshness(a.last_occurred_at ?? a.triggered_at)})`);
      // Only a real contradiction is recorded: if telemetry is still arriving,
      // the source is reachable enough to report - which argues against a total
      // loss of communication for the host hypothesis.
      const contradicting =
        c.id === "h-host" && evidenceFresh && supporting.length > 0
          ? ["monitoring telemetry is still arriving from the source, so communication is not entirely lost"]
          : [];
      const confidence: Hypothesis["confidence"] =
        supporting.length >= 3 ? "HIGH" : supporting.length === 2 ? "MEDIUM" : "LOW";
      return {
        id: c.id,
        statement: c.statement,
        confidence,
        supporting,
        contradicting,
        missing: c.missing,
        provenance: "workspace_inference" as const,
      };
    })
    .filter((h) => h.supporting.length > 0)
    .sort((a, b) => b.supporting.length - a.supporting.length);

  if (hypotheses.length === 0) {
    hypotheses.push({
      id: "h-insufficient",
      statement:
        "Insufficient evidence to establish a cause - no member alert matches a known failure pattern.",
      confidence: "INSUFFICIENT",
      supporting: [],
      contradicting: [],
      missing: ["measurable alert evidence (metric, threshold, value) attached to the incident"],
      provenance: "workspace_inference" as const,
    });
  }

  /* ---- diagnostics: what is runnable, and what the backend supports ------ */
  // Availability comes from the REAL capability report - a test is only offered
  // as runnable when the backend says the resource supports it.
  const supported = new Set(capabilities?.supported ?? []);
  const unsupported = new Map((capabilities?.unsupported ?? []).map((u) => [u.capability, u.reason ?? null]));
  const unknownCaps = new Set((capabilities?.unknown ?? []).map((u) => u.capability));

  const diagnostics: DiagnosticSuggestion[] = DIAGNOSTIC_CATALOGUE.map((d) => {
    let availability: DiagnosticSuggestion["availability"] = "unknown";
    let reason: string | null = null;
    if (!capabilities) {
      reason = "device capabilities were not read";
    } else if (d.capability && supported.has(d.capability)) {
      availability = "supported";
    } else if (d.capability && unsupported.has(d.capability)) {
      availability = "unsupported";
      reason = unsupported.get(d.capability) ?? "reported unsupported";
    } else if (d.capability && unknownCaps.has(d.capability)) {
      reason = "the capability report lists this capability as unknown";
    } else {
      reason = "not listed by the capability report";
    }
    return {
      key: d.key,
      label: d.label,
      capability: d.capability,
      purpose: d.purpose,
      risk: "READ_ONLY" as const,
      availability,
      reason,
      expected: d.expected,
      origin: "capability_catalogue" as const,
    };
  });

  // Checks the correlator itself proposed are appended verbatim. They are free
  // text, so their capability is NOT asserted and their availability is unknown
  // rather than assumed runnable.
  const reportedChecks: DiagnosticSuggestion[] = findings.flatMap((f) =>
    f.suggestedChecks.map((check, idx) => ({
      key: `di-check-${f.id}-${idx}`,
      label: check,
      capability: null,
      purpose: `Proposed by the ${f.capability} correlation finding.`,
      risk: "READ_ONLY" as const,
      availability: "unknown" as const,
      reason: "free-text suggestion from the correlator; runnability is not asserted",
      expected: "Not specified by the correlator.",
      origin: "dependency_intelligence" as const,
    })),
  );
  diagnostics.push(...reportedChecks);

  /* ---- gaps: everything this workspace cannot establish ------------------ */
  const recordedRca = plainText(incident.root_cause_summary);
  const gaps: string[] = [];
  if (evidence.length === 0) {
    gaps.push("No member alerts are attached, so there is no measured evidence to reason from.");
  }
  if (!recordedRca) {
    gaps.push("No root cause is recorded on the incident, and this workspace does not assert one.");
  }
  if (!impact) {
    gaps.push(blastRadius.unavailableReason ?? "Impact graph unavailable.");
  } else {
    gaps.push("Unaffected entities are not derivable from the impact closure - only affected nodes are returned.");
  }
  if (!capabilities) {
    gaps.push("Device capabilities were not read, so diagnostic availability is unknown rather than false.");
  }
  if (!correlation.available) {
    gaps.push("No dependency-correlation finding is recorded for this incident, so no causal linkage is presented.");
  } else if (!correlation.sopContextAvailable) {
    gaps.push("The correlation finding carries no SOP context, so no matched procedure can be shown as the action basis.");
  }
  gaps.push("Recovery is not independently verified: no post-recovery telemetry comparison exists in this workspace yet.");
  gaps.push("The incident contract carries no priority field, so PRIORITY renders N/A.");
  const hasExternal = incidentAlerts.some((a) =>
    /wan|sla|isp|circuit|carrier/.test(`${a.metric_type ?? ""} ${a.title ?? ""}`.toLowerCase()),
  );
  if (!hasExternal) {
    gaps.push("No ISP / circuit-level telemetry is attached - external network evidence is missing.");
  }

  /* ---- executive summary: observed facts + explicit absence -------------- */
  const impactParts: string[] = [];
  if (incident.alert_count) impactParts.push(`${incident.alert_count} member alert(s)`);
  if (incident.total_occurrences) impactParts.push(`${incident.total_occurrences} occurrence(s)`);
  if (blastRadius.direct !== null) {
    impactParts.push(`${blastRadius.direct} directly connected resource(s)`);
  }
  if (blastRadius.downstream !== null && blastRadius.downstream > 0) {
    impactParts.push(`${blastRadius.downstream} downstream resource(s)`);
  }

  const summary: InvestigationSummary = {
    whatHappened: plainText(incident.description) ?? incident.title ?? null,
    currentState: investigationPhase(incident.status),
    impact: impactParts.length > 0 ? impactParts.join(" · ") : null,
    likelyCause: recordedRca ?? "Not established - no root cause is recorded and none is asserted here.",
    causeBasis: recordedRca ? "recorded" : "not_established",
    nextAction: nextAction(incident.status, Boolean(recordedRca), evidence.length),
  };

  /* ---- header ------------------------------------------------------------ */
  const closed = CLOSED_INCIDENT_STATUS.has((incident.status || "").toLowerCase());
  const endIso = incident.closed_at ?? incident.resolved_at ?? null;

  const header: InvestigationModel["header"] = {
    id: incident.id,
    title: incident.title,
    severity: (incident.severity || "").toLowerCase(),
    status: (incident.status || "").toLowerCase(),
    priority: null,
    owner: incident.assigned_to ?? null,
    team: incident.assigned_team ?? null,
    tenantId: incident.tenant_id,
    firstObserved: incident.opened_at,
    lastUpdated: incident.last_alert_at ?? incident.updated_at,
    duration: durationText(incident.opened_at, endIso),
    durationLabel: closed ? "duration" : "elapsed",
    recoveredAt: incident.recovered_at ?? null,
    resolvedAt: incident.resolved_at ?? null,
  };

  const recordedRootCause: RecordedRootCause = {
    summary: recordedRca,
    alertId: incident.root_cause_alert_id ?? null,
    correlationType: incident.correlation_type ?? null,
  };

  // Transitions the API exposes, marked with the workspace's expectation of which
  // one moves this incident on. Nothing here is executed by this view.
  const status = (incident.status || "").toLowerCase();
  const actions: AvailableTransition[] = TRANSITIONS.map((t) => ({
    ...t,
    isExpectedNext: t.expectedFrom.includes(status),
  }));

  return {
    header,
    recordedRootCause,
    summary,
    timeline,
    evidence,
    correlation,
    blastRadius,
    hypotheses,
    diagnostics,
    actions,
    gaps,
  };
}

