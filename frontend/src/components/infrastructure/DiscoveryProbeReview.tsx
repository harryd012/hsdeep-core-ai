"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Database } from "lucide-react";
import {
  getDiscoveryProbeJob,
  listDiscoveryProbeJobs,
  materializeProbeJob,
  type DiscoveryProbeHost,
  type MaterializeResult,
} from "@/lib/infrastructureIntelligence";

/**
 * Universal Discovery — probe review + controlled materialization.
 * Observation-only on the left (durable probe jobs); the operator selects
 * reachable hosts and the backend materializes ONLY those into devices.
 */

const OUTCOME_TONE: Record<string, string> = {
  created: "ok",
  existing: "ok",
  rejected: "warn",
  failed: "bad",
};

function ProtocolState({ host }: { host: DiscoveryProbeHost }) {
  const entries = Object.entries(host.protocols ?? {});
  if (entries.length === 0) return <span className="disc-mono">icmp only</span>;
  return (
    <span className="disc-mono">
      {entries.map(([p, s]) => `${p}:${s}`).join(" · ")}
    </span>
  );
}

export default function DiscoveryProbeReview() {
  const queryClient = useQueryClient();
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedHosts, setSelectedHosts] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<MaterializeResult | null>(null);

  const jobsQuery = useQuery({
    queryKey: ["discovery-probe-jobs"],
    queryFn: () => listDiscoveryProbeJobs(),
  });

  const detailQuery = useQuery({
    queryKey: ["discovery-probe-job", selectedJobId],
    queryFn: () => getDiscoveryProbeJob(selectedJobId as string),
    enabled: selectedJobId !== null,
  });

  const materializeMutation = useMutation({
    mutationFn: () =>
      materializeProbeJob(selectedJobId as string, [...selectedHosts]),
    onSuccess: (r) => {
      setResult(r);
      queryClient.invalidateQueries({
        queryKey: ["discovery-probe-job", selectedJobId],
      });
      queryClient.invalidateQueries({ queryKey: ["discovery-probe-jobs"] });
    },
  });

  const jobs = jobsQuery.data ?? [];
  const detail = detailQuery.data ?? null;
  const reachable = (detail?.hosts ?? []).filter((h) => h.reachable);

  function toggle(addr: string) {
    setSelectedHosts((prev) => {
      const next = new Set(prev);
      if (next.has(addr)) next.delete(addr);
      else next.add(addr);
      return next;
    });
  }

  return (
    <section className="hud-panel ni-panel">
      <div className="ni-header">
        <div>
          <h2>PROBE JOBS — REVIEW &amp; MATERIALIZE</h2>
          <p className="ni-header__sub">
            DURABLE OBSERVATIONS · SELECT ELIGIBLE HOSTS · CONTROLLED DEVICE CREATION
          </p>
        </div>
      </div>

      {jobs.length === 0 ? (
        <p className="ni-empty">
          No probe jobs recorded yet. Run a scope probe to produce durable results.
        </p>
      ) : (
        <ul className="ni-audit">
          {jobs.map((j) => (
            <li
              key={j.id}
              className="ni-audit__row"
              style={{ cursor: "pointer" }}
              onClick={() => {
                setSelectedJobId(j.id);
                setSelectedHosts(new Set());
                setResult(null);
              }}
            >
              <span className="ni-audit__engine disc-mono">{j.id.slice(0, 8)}</span>
              <span className="ni-audit__stage">{j.status.toUpperCase()}</span>
              <span className="ni-audit__decision">
                {j.reachable} reachable · {j.materialization_state}
              </span>
              <span className="ni-audit__meta">
                {j.target}
                {j.error ? ` · ${j.error}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}

      {detail && (
        <div style={{ marginTop: 16 }}>
          <div className="ni-header">
            <div>
              <h3>JOB {detail.id.slice(0, 8)} — {detail.target}</h3>
              <p className="ni-header__sub">
                {detail.hosts_probed} probed · {detail.reachable} reachable ·{" "}
                {detail.status}
              </p>
            </div>
          </div>

          {reachable.length === 0 ? (
            <p className="ni-empty">No reachable hosts eligible for materialization.</p>
          ) : (
            <>
              <div className="disc-protocols" style={{ marginBottom: 8 }}>
                {reachable.map((h) => (
                  <label key={h.id} className="disc-check">
                    <input
                      type="checkbox"
                      checked={selectedHosts.has(h.address)}
                      disabled={
                        h.materialization_outcome === "created" ||
                        h.materialization_outcome === "existing"
                      }
                      onChange={() => toggle(h.address)}
                      aria-label={`Select ${h.address}`}
                    />
                    <span className="disc-mono">{h.address}</span>
                    <ProtocolState host={h} />
                    {h.materialization_outcome && (
                      <span className="disc-mono">· {h.materialization_outcome}</span>
                    )}
                  </label>
                ))}
              </div>

              <button
                className="ni-select disc-input"
                style={{ width: "auto", cursor: "pointer" }}
                disabled={selectedHosts.size === 0 || materializeMutation.isPending}
                onClick={() => materializeMutation.mutate()}
              >
                <Database size={13} /> Materialize {selectedHosts.size} selected
              </button>

              {materializeMutation.error && (
                <p className="ni-error">
                  <AlertTriangle size={12} />{" "}
                  {materializeMutation.error instanceof Error
                    ? materializeMutation.error.message
                    : "Materialization failed"}
                </p>
              )}
            </>
          )}

          {result && (
            <div style={{ marginTop: 12 }}>
              <p className="disc-mono">
                <CheckCircle2 size={12} /> created {result.created} · existing{" "}
                {result.existing} · rejected {result.rejected} · failed{" "}
                {result.failed}
              </p>
              <ul className="ni-audit">
                {result.hosts.map((o) => (
                  <li key={o.address} className="ni-audit__row">
                    <span className="ni-audit__engine disc-mono">{o.address}</span>
                    <span className={`ni-audit__stage ${OUTCOME_TONE[o.outcome] ?? ""}`}>
                      {o.outcome.toUpperCase()}
                    </span>
                    <span className="ni-audit__decision">
                      {o.device_id ? `device ${o.device_id.slice(0, 8)}` : ""}
                    </span>
                    <span className="ni-audit__meta">{o.error ?? ""}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
