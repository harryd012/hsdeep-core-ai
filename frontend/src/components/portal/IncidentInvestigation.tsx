"use client";

/**
 * Incident Investigation Workspace — the customer-facing section of
 * /portal/resources/incidents/{id}.
 *
 * Renders ONLY data that exists in the incident payload. Every absent field
 * degrades to an explicit state ("Not available" / "No correlated dependency
 * finding") - nothing is inferred, and no remediation control is fabricated:
 * the Actions section states plainly when no executable action is exposed.
 *
 * Evidence shape (verified against real persisted rows):
 *   supporting_evidence = { evidence: [ {source?, ...} ], generated_at? }
 * Entries whose source starts with "dependency_intelligence" are Dependency
 * Intelligence findings; entries without a source are correlated member alerts.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/portal/PortalShell";
import { getIncident, type IncidentDetail } from "@/lib/api";

type LoadState = "loading" | "ready" | "error";

type EvidenceEntry = Record<string, unknown>;

const DEP_SOURCE = "dependency_intelligence";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function when(value: unknown): string {
  const raw = text(value);
  return raw ? raw.replace("T", " ").replace("Z", "").slice(0, 19) : "-";
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v) => v !== null && v !== undefined).map(String) : [];
}

/** supporting_evidence is a dict with an `evidence` list (older rows may be a
 *  bare list); anything else yields [] rather than a guess. */
function readEvidence(incident: IncidentDetail | null): EvidenceEntry[] {
  const raw = (incident as unknown as Record<string, unknown> | null)?.supporting_evidence;
  const source = Array.isArray(raw)
    ? raw
    : raw !== null && typeof raw === "object"
      ? (raw as Record<string, unknown>).evidence
      : null;
  return Array.isArray(source)
    ? source.filter((e): e is EvidenceEntry => e !== null && typeof e === "object")
    : [];
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="hud-panel" style={sectionStyle} aria-label={title}>
      <h2 style={sectionTitleStyle}>{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: unknown }) {
  const shown = text(value) ?? "-";
  return (
    <div style={{ display: "contents" }}>
      <dt style={dtStyle}>{label}</dt>
      <dd style={ddStyle}>{shown}</dd>
    </div>
  );
}

export default function IncidentInvestigation({ incidentId }: { incidentId: string }) {
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setState("loading");
      setError(null);
      try {
        // One bounded detail fetch - the API resolves the tenant from the token.
        const row = await getIncident(incidentId);
        if (cancelled) return;
        setIncident(row);
        setState("ready");
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "The incident could not be loaded.");
        setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [incidentId]);

  if (state === "loading") return <LoadingState label="Loading incident intelligence…" />;
  if (state === "error") return <ErrorState message={error ?? "The incident could not be loaded."} />;
  if (!incident) return <EmptyState message="The incident was not returned by the API." />;

  const row = incident as unknown as Record<string, unknown>;
  const evidence = readEvidence(incident);
  const dependencyEntries = evidence.filter((e) =>
    String(e.source ?? "").startsWith(DEP_SOURCE),
  );
  const memberAlerts = evidence.filter((e) => !text(e.source));
  const rootCause = text(row.root_cause_summary);
  const sop = text(row.recommended_sop);
  const findings = dependencyEntries.filter(
    (e) => String(e.source ?? "") === DEP_SOURCE,
  );
  const sopContext = dependencyEntries.find(
    (e) => String(e.source ?? "") === `${DEP_SOURCE}_sop_context`,
  );
  const matchKeys = list(sopContext?.match_keys);
  // Only real recorded timestamps/owner fields become audit rows; blanks drop out.
  const auditRows = (
    [
      ["Opened", when(row.opened_at)],
      ["Last alert", when(row.last_alert_at)],
      ["Resolved", when(row.resolved_at)],
      ["Assigned team", text(row.assigned_team) ?? "-"],
      ["Assigned to", text(row.assigned_to) ?? "-"],
      ["Escalation level", row.escalation_level == null ? "-" : String(row.escalation_level)],
    ] as [string, string][]
  ).filter(([, value]) => value !== "-");

  return (
    <>
      <Section title="1 · INCIDENT HEADER">
        <dl style={dlStyle}>
          <Field label="Incident ID" value={row.id ?? incidentId} />
          <Field label="Title" value={row.title} />
          <Field label="Severity" value={row.severity} />
          <Field label="Status" value={row.status} />
          <Field label="Priority" value={row.priority} />
          <Field label="Device" value={row.device_name ?? row.device_id} />
          <Field label="Site" value={row.site_name ?? row.site_id} />
          <Field label="Opened" value={when(row.opened_at)} />
          <Field label="Last alert" value={when(row.last_alert_at)} />
          <Field label="Member alerts" value={row.alert_count} />
        </dl>
      </Section>

      <Section title="2 · WHAT HAPPENED">
        {text(row.description) ? (
          <p style={bodyStyle}>{text(row.description)}</p>
        ) : (
          <p style={bodyStyle}>No incident description was recorded.</p>
        )}
        <p style={mutedStyle}>
          {text(row.title) ?? "Incident"} ·{" "}
          {row.alert_count === null || row.alert_count === undefined
            ? "correlated alerts unavailable"
            : `${row.alert_count} correlated alert(s)`}{" "}
          · state {text(row.status) ?? "-"}
          {text(row.root_cause_alert_id) ? ` · root-cause alert ${text(row.root_cause_alert_id)}` : ""}
        </p>
      </Section>

      <Section title="3 · EVIDENCE">
        {memberAlerts.length === 0 ? (
          <EmptyState message="No correlated alert evidence is recorded for this incident." />
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Alert</th>
                <th style={thStyle}>Severity</th>
                <th style={thStyle}>Category</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>Role</th>
              </tr>
            </thead>
            <tbody>
              {memberAlerts.map((e, i) => (
                <tr key={`${String(e.alert_id ?? i)}`}>
                  <td style={tdStyle}>{text(e.title) ?? "-"}</td>
                  <td style={tdStyle}>{text(e.severity) ?? "-"}</td>
                  <td style={tdStyle}>{text(e.category) ?? "-"}</td>
                  <td style={tdStyle}>{text(e.status) ?? "-"}</td>
                  <td style={tdStyle}>{text(e.reason) ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="4 · ROOT CAUSE ANALYSIS">
        {rootCause ? (
          <>
            <p style={bodyStyle}>{rootCause}</p>
            <dl style={dlStyle}>
              <Field label="Confidence score" value={row.incident_confidence_score} />
              <Field label="Supporting evidence items" value={evidence.length} />
              <Field label="Root-cause alert" value={row.root_cause_alert_id} />
            </dl>
          </>
        ) : (
          <EmptyState message="RCA not available for this incident." />
        )}
      </Section>

      <Section title="5 · DEPENDENCY INTELLIGENCE">
        {findings.length === 0 ? (
          <EmptyState message="No correlated dependency finding for this incident." />
        ) : (
          findings.map((find, i) => (
            <div key={`${String(find.capability ?? i)}`} style={findingStyle}>
              <div style={findingHeadStyle}>
                <strong style={{ fontSize: 13 }}>{String(find.capability ?? "capability")}</strong>
                <span style={mutedStyle}>
                  confidence {String(find.confidence ?? "-")}
                  {text(find.failure_scope) ? ` · scope ${text(find.failure_scope)}` : ""}
                  {list(find.dependency_identifiers).length
                    ? ` · ${list(find.dependency_identifiers).join(", ")}`
                    : ""}
                </span>
              </div>
              <dl style={dlStyle}>
                <Field label="Healthy dependencies" value={list(find.healthy_dependencies).join(", ")} />
                <Field label="Investigated (failed)" value={list(find.failed_dependencies).join(", ")} />
                <Field label="Unknown dependencies" value={list(find.unknown_dependencies).join(", ")} />
                <Field label="Excluded conclusions" value={list(find.excluded_conclusions).join(", ")} />
              </dl>
              {list(find.candidate_causes).length > 0 && (
                <p style={bodyStyle}>
                  <span style={labelStyle}>Candidate causes: </span>
                  {list(find.candidate_causes).join(" · ")}
                </p>
              )}
              {list(find.suggested_checks).length > 0 && (
                <p style={mutedStyle}>
                  <span style={labelStyle}>Suggested checks: </span>
                  {list(find.suggested_checks).join(" · ")}
                </p>
              )}
            </div>
          ))
        )}
      </Section>

      <Section title="6 · RECOMMENDATION">
        {sop ? (
          <dl style={dlStyle}>
            <Field label="Recommended SOP" value={sop} />
            <Field label="Recommended next action" value={row.recommended_next_action} />
            <Field label="Recommended team" value={row.recommended_team} />
            <Field
              label="Recommended response (minutes)"
              value={row.recommended_response_time_minutes}
            />
            <Field
              label="Approval requirement"
              value={text(row.requires_approval) ?? "Not recorded on the incident"}
            />
          </dl>
        ) : (
          <EmptyState message="Recommendation not available for this incident." />
        )}
        {matchKeys.length > 0 && (
          <p style={mutedStyle}>
            <span style={labelStyle}>Matching keys used: </span>
            {matchKeys.join(", ")}
          </p>
        )}
      </Section>

      <Section title="7 · ACTIONS">
        <p style={bodyStyle}>
          {row.automation_available === true
            ? "Automation is flagged available for this incident."
            : "No autonomous action available for this incident."}
        </p>
        <p style={mutedStyle}>
          This workspace does not expose remediation controls: no executable action is wired
          into the Portal for incidents yet, and none is simulated here.
        </p>
        <p style={mutedStyle}>
          Related resources:{" "}
          <Link href="/portal/resources/incidents" style={linkStyle}>
            incident list
          </Link>{" "}
          ·{" "}
          <Link href="/portal/resources/alerts" style={linkStyle}>
            alerts
          </Link>
        </p>
      </Section>

      <Section title="8 · AUDIT">
        {auditRows.length === 0 ? (
          <EmptyState message="No lifecycle timestamps are recorded for this incident." />
        ) : (
          <dl style={dlStyle}>
            {auditRows.map(([label, value]) => (
              <Field key={label} label={label} value={value} />
            ))}
          </dl>
        )}
      </Section>
    </>
  );
}

const sectionStyle: React.CSSProperties = { padding: 12, marginBottom: 12 };
const sectionTitleStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--cyan)",
  letterSpacing: "0.08em",
  margin: "0 0 8px",
};
const dlStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "240px 1fr",
  rowGap: 6,
  columnGap: 12,
  margin: 0,
};
const dtStyle: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const ddStyle: React.CSSProperties = { margin: 0, fontSize: 13, color: "#a7bec7", overflowWrap: "anywhere" };
const bodyStyle: React.CSSProperties = { fontSize: 13, color: "#a7bec7", margin: "0 0 6px" };
const mutedStyle: React.CSSProperties = { fontSize: 12, color: "var(--muted)", margin: "4px 0 0" };
const labelStyle: React.CSSProperties = { color: "var(--cyan)" };
const tableStyle: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 12 };
const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  color: "var(--muted)",
  borderBottom: "1px solid var(--line)",
  fontWeight: 500,
};
const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid var(--line)",
  color: "#a7bec7",
  overflowWrap: "anywhere",
};
const findingStyle: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 6,
  padding: 10,
  marginBottom: 8,
};
const findingHeadStyle: React.CSSProperties = {
  display: "flex",
  gap: 10,
  alignItems: "baseline",
  flexWrap: "wrap",
  marginBottom: 6,
};
const linkStyle: React.CSSProperties = { color: "var(--cyan)" };
