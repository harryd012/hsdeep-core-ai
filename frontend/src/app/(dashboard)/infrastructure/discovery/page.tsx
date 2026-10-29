"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Radar, ShieldCheck, Wifi } from "lucide-react";
import { Banner, DataTable, PageShell, Panel, Pill, ago, statusTone, titleCaseWords } from "@/components/ui/PageKit";
import {
  type DiscoveryRunResponse,
  listDiscoveryJobs,
  runDiscovery,
} from "@/lib/infrastructureIntelligence";
import DiscoveryProbeReview from "@/components/infrastructure/DiscoveryProbeReview";

/**
 * /infrastructure/discovery — Universal Discovery console.
 *
 * Backend-owned: this page submits a target and renders the REAL probe results,
 * recognition provenance and what was persisted. It never inserts a device,
 * never invents a vendor, and never shows a status the API did not report. An
 * unidentifiable target renders as UNKNOWN with the reason.
 */

const PROTOCOL_OPTIONS = ["icmp", "snmp", "https", "ssh"] as const;

const OUTCOME_TONE: Record<string, string> = {
  success: "ok",
  timeout: "warn",
  no_response: "bad",
  network_unreachable: "bad",
  unauthorized: "warn",
  configuration_error: "warn",
};

function tone(outcome: string): string {
  return OUTCOME_TONE[outcome] ?? "neutral";
}

export default function DiscoveryPage() {
  const [target, setTarget] = useState("");
  const [protocols, setProtocols] = useState<string[]>([...PROTOCOL_OPTIONS]);
  const [community, setCommunity] = useState("");
  const queryClient = useQueryClient();

  // Existing React Query convention (same as useNetworkIntelligence): the job
  // list is server state, so it is cached rather than mirrored into useState.
  const jobsQuery = useQuery({
    queryKey: ["discovery-jobs"],
    queryFn: () => listDiscoveryJobs(10),
  });

  const runMutation = useMutation({
    mutationFn: () =>
      runDiscovery({
        target: target.trim(),
        protocols,
        snmp_community: community ? community : undefined,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["discovery-jobs"] }),
  });

  const outcome: DiscoveryRunResponse | null = runMutation.data ?? null;
  const error = runMutation.error
    ? runMutation.error instanceof Error
      ? runMutation.error.message
      : "Discovery failed"
    : null;
  const jobs = jobsQuery.data ?? [];

  const duration = (a: string, b: string | null) => {
    if (!b) return "—";
    const sec = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));
    return sec < 60 ? `${sec} s` : `${Math.round(sec / 60)} min`;
  };

  return (
    <PageShell
      title="Discovery"
      subtitle="Probe a host, hostname or CIDR range. The backend identifies what answers and records it; the browser never creates devices."
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Discovery" }]}
    >
      <Panel title="Run discovery" info="Probes run from the backend using the protocols you select." state="ready">
        <div className="pk-form" style={{ maxWidth: "none", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          <div className="pk-field">
            <label htmlFor="disc-target">Target</label>
            <input
              id="disc-target"
              className="pk-input"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="10.0.0.0/24, 192.168.1.10 or core-sw01"
              autoComplete="off"
            />
            <small>IP address, hostname or CIDR range.</small>
          </div>
          <div className="pk-field">
            <label htmlFor="disc-community">SNMP community</label>
            <input
              id="disc-community"
              className="pk-input"
              type="password"
              value={community}
              onChange={(e) => setCommunity(e.target.value)}
              placeholder="Optional"
              autoComplete="new-password"
            />
            <small>Used for this probe only — never stored or returned.</small>
          </div>
        </div>
        <div className="pk-toolbar" style={{ marginTop: 12 }}>
          <span className="pk-count">Protocols</span>
          {PROTOCOL_OPTIONS.map((p) => (
            <label key={p} className="pk-switch">
              <input
                type="checkbox"
                checked={protocols.includes(p)}
                onChange={(e) =>
                  setProtocols((cur) => (e.target.checked ? [...cur, p] : cur.filter((x) => x !== p)))
                }
              />
              {p.toUpperCase()}
            </label>
          ))}
          <span className="pk-toolbar__spacer" />
          <button
            type="button"
            className="pk-button pk-button--primary"
            onClick={() => runMutation.mutate()}
            disabled={runMutation.isPending || !target.trim() || protocols.length === 0}
          >
            <Radar size={14} /> {runMutation.isPending ? "Discovering…" : "Run discovery"}
          </button>
        </div>
        {error && (
          <div style={{ marginTop: 12 }}>
            <Banner tone="bad">Discovery failed: {error}</Banner>
          </div>
        )}
      </Panel>

        {outcome && (
          <>
            <section className="ecc-card" aria-label="Probe Results">
              <header className="ecc-card__header"><h2 className="ecc-card__title">Probe results · {outcome.target}</h2></header>
              <div className="ni-status">
                <div className="ni-status__tile ni-status__tile--neutral">
                  <span className="ni-status__label">JOB</span>
                  <strong className="disc-mono">{outcome.job_id.slice(0, 8)}</strong>
                </div>
                <div
                  className={`ni-status__tile ni-status__tile--${outcome.status === "success" ? "ok" : "warn"}`}
                >
                  <span className="ni-status__label">JOB STATUS</span>
                  <strong>{outcome.status.toUpperCase()}</strong>
                </div>
                <div className="ni-status__tile ni-status__tile--neutral">
                  <span className="ni-status__label">DISCOVERED</span>
                  <strong>{outcome.result.discovered}</strong>
                </div>
                <div
                  className={`ni-status__tile ni-status__tile--${outcome.result.unknown > 0 ? "warn" : "ok"}`}
                >
                  <span className="ni-status__label">UNKNOWN</span>
                  <strong>{outcome.result.unknown}</strong>
                </div>
                <div className="ni-status__tile ni-status__tile--neutral">
                  <span className="ni-status__label">CANONICAL RESOURCES</span>
                  <strong>{outcome.result.persisted ?? 0}</strong>
                </div>
                <div
                  className={`ni-status__tile ni-status__tile--${(outcome.result.evidence_only ?? 0) > 0 ? "warn" : "ok"}`}
                >
                  <span className="ni-status__label">EVIDENCE ONLY</span>
                  <strong>{outcome.result.evidence_only ?? 0}</strong>
                </div>
                <div className="ni-status__tile ni-status__tile--neutral">
                  <span className="ni-status__label">WITH TOPOLOGY EVIDENCE</span>
                  <strong>{outcome.result.with_topology_evidence ?? 0}</strong>
                </div>
              </div>
              <p className="disc-detail">
                A canonical resource is a persisted identity. Evidence-only rows
                answered a probe but were not recognised, so they are reported as
                evidence rather than as operational resources.
              </p>

              {outcome.probe_results.map((bundle) => (
                <div key={bundle.target} className="ni-card">
                  <div className="ni-card__head">
                    <span className="ni-card__title">
                      <Wifi size={14} /> {bundle.target}
                    </span>
                  </div>
                  <table className="ni-compare">
                    <thead>
                      <tr>
                        <th>Protocol</th>
                        <th>Outcome</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bundle.probes.map((p) => (
                        <tr key={p.protocol}>
                          <td className="disc-mono">{p.protocol}</td>
                          <td className={`ni-text ni-text--${tone(p.outcome)}`}>
                            {p.outcome.toUpperCase()}
                          </td>
                          <td className="disc-detail">{p.detail || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </section>

            <section className="ecc-card" aria-label="Recognition · Identity · Inventory">
              <header className="ecc-card__header"><h2 className="ecc-card__title">Recognition, identity and inventory</h2></header>
              {outcome.result.resources.map((r) => {
                const matched = r.recognition_status === "identified";
                const canonical = r.canonical_resource;
                const evidence = r.probe_evidence;
                return (
                  <div key={r.address} className="ni-card">
                    <div className="ni-card__head">
                      <span className="ni-card__title">
                        <Radar size={14} /> {r.address}
                      </span>
                      <span className={`ni-pill ni-pill--${matched ? "ok" : "warn"}`}>
                        {matched ? "IDENTIFIED" : "UNKNOWN"}
                      </span>
                    </div>

                    <div className="ni-kv">
                      <div>
                        <dt>Probe evidence</dt>
                        <dd className="disc-mono">
                          {evidence?.transport ?? "—"} ·{" "}
                          {evidence?.reachable === true
                            ? "reachable"
                            : evidence?.reachable === false
                              ? "not reachable"
                              : "reachability not established"}{" "}
                          · auth {evidence?.authentication ?? "not_attempted"}
                        </dd>
                      </div>
                      <div>
                        <dt>Observed at</dt>
                        <dd className="disc-mono">{evidence?.observed_at ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Recognition</dt>
                        <dd className="disc-mono">
                          {r.recognition?.method ?? "—"} ·{" "}
                          {r.recognition_status ?? "unknown"}
                        </dd>
                      </div>
                      <div>
                        <dt>Resource Type</dt>
                        <dd>{r.resource_type.toUpperCase()}</dd>
                      </div>
                      <div>
                        <dt>Category</dt>
                        <dd>{r.resource_category.toUpperCase()}</dd>
                      </div>
                      <div>
                        <dt>Vendor</dt>
                        <dd>{r.vendor ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Provider</dt>
                        <dd>{r.provider ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Name</dt>
                        <dd>{r.name ?? "—"}</dd>
                      </div>
                      <div>
                        <dt>Identity</dt>
                        <dd className="disc-mono">
                          {r.identity_source ?? "—"} ({r.identity_confidence})
                        </dd>
                      </div>
                      <div>
                        <dt>Identity stage</dt>
                        <dd className="disc-mono">{r.identity_stage ?? "discovered"}</dd>
                      </div>
                      <div>
                        <dt>Persistence</dt>
                        <dd>
                          {r.persisted
                            ? canonical?.created
                              ? "ROW CREATED"
                              : "ROW UPDATED"
                            : "NOT PERSISTED"}
                        </dd>
                      </div>
                      <div>
                        <dt>Canonical resource</dt>
                        <dd className="disc-mono">
                          {canonical
                            ? `${canonical.device_id.slice(0, 8)} · ${
                                canonical.lifecycle_state ?? "discovered"
                              }`
                            : "none (evidence only)"}
                        </dd>
                      </div>
                      <div>
                        <dt>Topology evidence</dt>
                        <dd className="disc-mono">
                          {r.relationship_evidence_state ?? "no_relationship_evidence"}
                          {evidence?.neighbor_count !== undefined
                            ? ` · ${evidence.neighbor_count} neighbour(s) observed`
                            : ""}
                        </dd>
                      </div>
                    </div>

                    {evidence?.note && (
                      <div className="ni-why">
                        <span className="ni-subhead">EVIDENCE NOTE</span>
                        <p>{evidence.note}</p>
                      </div>
                    )}

                    <div className="ni-why">
                      <span className="ni-subhead">
                        <ShieldCheck size={11} /> CAPABILITIES (observed only)
                      </span>
                      <p className="disc-mono">
                        {Object.entries(r.capabilities)
                          .filter(([, v]) => v)
                          .map(([k]) => k.replace("supports_", ""))
                          .join(" · ") || "none observed"}
                        {r.provider_capabilities.length > 0 &&
                          ` · provider: ${r.provider_capabilities.join(", ")}`}
                      </p>
                    </div>

                    {!matched && r.recognition?.reason && (
                      <div className="ni-why">
                        <span className="ni-subhead">WHY UNKNOWN</span>
                        <p>{r.recognition.reason}</p>
                      </div>
                    )}

                    {r.errors.length > 0 && (
                      <p className="ni-error">
                        <AlertTriangle size={12} /> {r.errors.join("; ")}
                      </p>
                    )}
                  </div>
                );
              })}
            </section>
          </>
        )}

      <Panel
        title="Discovery jobs"
        info="The 10 most recent discovery runs for this tenant."
        aside={jobsQuery.data ? `${jobs.length} recent` : undefined}
        state={jobsQuery.data ? "ready" : jobsQuery.isError ? "error" : "loading"}
        onRetry={() => jobsQuery.refetch()}
      >
        <DataTable
          caption="Discovery jobs"
          rows={jobs}
          rowKey={(j) => j.job_id}
          emptyText="No discovery jobs recorded yet. Run one above."
          defaultSort={{ key: "started", dir: "desc" }}
          columns={[
            { key: "job", header: "Job", render: (j) => <span className="pk-mono">{j.job_id.slice(0, 8)}</span> },
            { key: "status", header: "Status", sortValue: (j) => j.status, render: (j) => <Pill tone={statusTone(j.status)}>{titleCaseWords(j.status)}</Pill> },
            { key: "created", header: "Devices created", numeric: true, sortValue: (j) => j.devices_upserted, render: (j) => j.devices_upserted },
            { key: "started", header: "Started", sortValue: (j) => new Date(j.started_at).getTime(), render: (j) => <span title={new Date(j.started_at).toLocaleString()}>{ago(j.started_at)}</span> },
            { key: "duration", header: "Duration", render: (j) => <span className="pk-muted">{duration(j.started_at, j.finished_at)}</span> },
            { key: "error", header: "Error", render: (j) => (j.error ? <span className="pk-truncate" style={{ color: "var(--ecc-bad)" }} title={j.error}>{j.error}</span> : <span className="pk-muted">—</span>) },
          ]}
        />
      </Panel>

      <DiscoveryProbeReview />
    </PageShell>
  );
}
