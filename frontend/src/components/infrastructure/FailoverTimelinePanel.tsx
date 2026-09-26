"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getFailoverEvent,
  getFailoverSummary,
  getFailoverVerification,
  listFailoverEvents,
  postFailoverVerify,
  type BlastRadiusOut,
  type EvidenceItemOut,
  type FailoverEventDetailResponse,
  type FailoverEventOut,
  type FailoverReplayOut,
  type FailoverStatus,
  type FailoverSummaryOut,
  type VerificationOut,
} from "@/lib/infrastructureIntelligence";
import OperationalSlaPolicyPanel from "@/components/infrastructure/OperationalSlaPolicyPanel";

/**
 * FailoverTimelinePanel — observed WAN/HA failover episodes for one site edge.
 *
 * Presentation only, and deliberately honest about absence:
 *
 * - Episode status is a forward-only FSM. `aborted` and `verification_failed`
 *   are real outcomes, displayed as such - the UI never paints them green.
 * - Every replay entry carries `provenance`: `captured` (written while it
 *   happened), `derived` (HSDEEP's interpretation) or `context` (joined from
 *   another table with a different cadence). The badge is rendered for all
 *   three, because they are not equally strong evidence.
 * - `gaps` from the replay are shown verbatim: a silent routing section means
 *   "not captured", never "nothing happened".
 * - A null value renders as "not measured"/"unknown" - never 0.
 *
 * Manual verification requires `incidents.write`; a 403 is shown as a
 * permission notice rather than an error.
 */

const POLL_MS = 30_000;

const cardStyle: React.CSSProperties = {
  background: "rgba(9, 16, 28, 0.72)",
  border: "1px solid rgba(90, 140, 200, 0.25)",
  borderRadius: 10,
  padding: "14px 16px",
  marginBottom: 14,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid rgba(90, 140, 200, 0.3)",
  opacity: 0.75,
  fontWeight: 600,
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid rgba(90, 140, 200, 0.12)",
};

const chipStyle: React.CSSProperties = {
  display: "inline-block",
  padding: "1px 7px",
  borderRadius: 6,
  border: "1px solid rgba(90, 140, 200, 0.35)",
  fontSize: 11,
  marginRight: 4,
};

const STATUS_COLOR: Record<FailoverStatus, string> = {
  detected: "#d9a441",
  failed_over: "#e0714a",
  recovering: "#4a90d9",
  recovered: "#4fae7a",
  verification_failed: "#c9534f",
  aborted: "#8a94a6",
};

const STATUS_LABEL: Record<FailoverStatus, string> = {
  detected: "detected (breach, no path switch yet)",
  failed_over: "failed over",
  recovering: "recovering",
  recovered: "recovered (verified)",
  verification_failed: "verification failed",
  aborted: "aborted (no path switch)",
};

const FINDING_COLOR: Record<string, string> = {
  supports: "#4fae7a",
  neutral: "#8a94a6",
  contradicts: "#c9534f",
};

const PROVENANCE_COLOR: Record<string, string> = {
  captured: "#4a90d9",
  derived: "#8a7bd9",
  context: "#8a94a6",
};

const PROVENANCE_HINT: Record<string, string> = {
  captured: "recorded while it happened",
  derived: "HSDEEP interpretation of captured data",
  context: "joined from another table (different collection cadence)",
};

function StatusBadge({ status }: { status: FailoverStatus }) {
  return (
    <span
      style={{
        ...chipStyle,
        borderColor: STATUS_COLOR[status] ?? "#8a94a6",
        color: STATUS_COLOR[status] ?? "#8a94a6",
      }}
      title={STATUS_LABEL[status] ?? status}
    >
      {status}
    </span>
  );
}

function ProvenanceBadge({ provenance }: { provenance: string }) {
  return (
    <span
      style={{
        ...chipStyle,
        borderColor: PROVENANCE_COLOR[provenance] ?? "#8a94a6",
        color: PROVENANCE_COLOR[provenance] ?? "#8a94a6",
      }}
      title={PROVENANCE_HINT[provenance] ?? provenance}
    >
      {provenance}
    </span>
  );
}

function when(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 60) return `${value.toFixed(1)}s`;
  if (value < 3600) return `${(value / 60).toFixed(1)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function NotAvailable({ detail }: { detail: string }) {
  return <div style={{ fontSize: 12, opacity: 0.8 }}>{detail}</div>;
}

export default function FailoverTimelinePanel({
  siteId = null,
  edgeDeviceId = null,
  title = "WAN failover intelligence",
}: {
  siteId?: string | null;
  edgeDeviceId?: string | null;
  title?: string;
}) {
  const [summary, setSummary] = useState<FailoverSummaryOut | null>(null);
  const [events, setEvents] = useState<FailoverEventOut[]>([]);
  const [total, setTotal] = useState(0);
  const [openOnly, setOpenOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FailoverEventDetailResponse | null>(null);
  const [verification, setVerification] = useState<VerificationOut | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [summaryOut, listOut] = await Promise.all([
        getFailoverSummary(),
        listFailoverEvents({ siteId, edgeDeviceId, openOnly, limit: 25 }),
      ]);
      setSummary(summaryOut);
      setEvents(listOut.events);
      setTotal(listOut.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [siteId, edgeDeviceId, openOnly]);

  const fetchDetail = useCallback(async (eventId: string) => {
    try {
      const [detailOut, verificationOut] = await Promise.all([
        getFailoverEvent(eventId),
        getFailoverVerification(eventId),
      ]);
      setDetail(detailOut);
      setVerification(verificationOut.current);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Filter changes drive the fetch directly from the dependency array. Every
  // state write happens after an await, so this neither cascades renders nor
  // needs a manual "load" callback (same convention as UniversalResourcePanel).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [summaryOut, listOut] = await Promise.all([
          getFailoverSummary(),
          listFailoverEvents({ siteId, edgeDeviceId, openOnly, limit: 25 }),
        ]);
        if (cancelled) return;
        setSummary(summaryOut);
        setEvents(listOut.events);
        setTotal(listOut.total);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [siteId, edgeDeviceId, openOnly]);

  // Polling lives in the interval callback (not the effect body), so a shared
  // refresh callback is safe here.
  useEffect(() => {
    const timer = setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Episode detail + a live (unpersisted) verification for the selection.
  useEffect(() => {
    if (!selectedId) return undefined;
    let cancelled = false;
    void (async () => {
      try {
        const [detailOut, verificationOut] = await Promise.all([
          getFailoverEvent(selectedId),
          getFailoverVerification(selectedId),
        ]);
        if (cancelled) return;
        setDetail(detailOut);
        setVerification(verificationOut.current);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const openEpisode = useCallback((eventId: string) => {
    setDetail(null);
    setVerification(null);
    setNotice(null);
    setSelectedId(eventId);
  }, []);

  const closeEpisode = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setVerification(null);
    setNotice(null);
  }, []);

  const verifyNow = useCallback(async () => {
    if (!selectedId) return;
    setVerifying(true);
    setNotice(null);
    try {
      const result = await postFailoverVerify(
        selectedId,
        detail?.event.incident_id ?? null,
      );
      setVerification(result.verification);
      setNotice(
        `Manual verification recorded: ${result.verification.status}. ` +
          result.verification.summary,
      );
      await fetchDetail(selectedId);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setNotice(
        /403|forbidden|permission/i.test(message)
          ? "Manual verification requires the incidents.write permission."
          : `Verification could not be run: ${message}`,
      );
    } finally {
      setVerifying(false);
    }
  }, [selectedId, detail, fetchDetail, refresh]);

  const replay: FailoverReplayOut | null = detail?.replay ?? null;
  const blast: BlastRadiusOut | undefined = detail?.event.blast_radius;
  const evidence: EvidenceItemOut[] = detail?.event.evidence ?? [];

  const byStatus = useMemo(
    () => (summary?.by_status ? Object.entries(summary.by_status) : []),
    [summary],
  );

  return (
    <div style={cardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 12,
        }}
      >
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
          <div style={{ fontSize: 12, opacity: 0.75 }}>
            Observed HA/SD-WAN path transitions. HSDEEP records, explains and
            verifies them — the network&apos;s own SLA/HA mechanism decides the
            failover.
          </div>
        </div>
        <label style={{ fontSize: 12, whiteSpace: "nowrap" }}>
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(event) => setOpenOnly(event.target.checked)}
          />{" "}
          open only
        </label>
      </div>

      {error ? (
        <div
          style={{
            marginTop: 10,
            fontSize: 12,
            color: "#e0714a",
            border: "1px solid rgba(224, 113, 74, 0.4)",
            borderRadius: 8,
            padding: "6px 10px",
          }}
        >
          Could not load failover episodes: {error}
        </div>
      ) : null}

      <div
        style={{
          marginTop: 12,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
          gap: 10,
          fontSize: 12,
        }}
      >
        <Kpi label="episodes (tenant)" value={summary?.total ?? "—"} />
        <Kpi label="open (tenant)" value={summary?.open ?? "—"} />
        <Kpi label="matching filter" value={total} />
        {byStatus.map(([status, count]) => (
          <Kpi key={status} label={status} value={count} />
        ))}
      </div>

      <div style={{ marginTop: 14 }}>
        {events.length ? (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>#</th>
                <th style={thStyle}>status</th>
                <th style={thStyle}>path change</th>
                <th style={thStyle}>detected</th>
                <th style={thStyle}>failover</th>
                <th style={thStyle}>recovered</th>
                <th style={thStyle}>impact</th>
                <th style={thStyle}>captured</th>
                <th style={thStyle} />
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td style={tdStyle}>{event.sequence_no}</td>
                  <td style={tdStyle}>
                    <StatusBadge status={event.status} />
                  </td>
                  <td style={tdStyle}>
                    {event.link_from_name ?? "—"}
                    {" → "}
                    {event.link_to_name ?? "—"}
                  </td>
                  <td style={tdStyle}>{when(event.detected_at)}</td>
                  <td style={tdStyle}>{when(event.failed_over_at)}</td>
                  <td style={tdStyle}>{when(event.recovered_at)}</td>
                  <td style={tdStyle}>
                    {event.blast_radius_total
                      ? `${event.blast_radius_total} (${event.blast_radius_critical} critical)`
                      : "none recorded"}
                  </td>
                  <td style={tdStyle}>
                    routes {event.route_transition_count} · bgp{" "}
                    {event.bgp_transition_count}
                    {event.verification_status
                      ? ` · verify ${event.verification_status}`
                      : ""}
                  </td>
                  <td style={tdStyle}>
                    <button
                      type="button"
                      onClick={() => openEpisode(event.id)}
                      style={secondaryButtonStyle}
                    >
                      replay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <NotAvailable
            detail={
              "No failover episodes have been recorded for this scope yet. " +
              "Episodes open only on an observable condition: a candidate WAN " +
              "path becoming unusable, or a confirmed critical operational SLA " +
              "breach. A path that is merely degraded opens nothing."
            }
          />
        )}
      </div>

      {selectedId ? (
        <div
          style={{
            marginTop: 16,
            borderTop: "1px solid rgba(90, 140, 200, 0.25)",
            paddingTop: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              alignItems: "baseline",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              Time machine · episode #{detail?.event.sequence_no ?? "…"}
              {detail ? (
                <>
                  {" "}
                  <StatusBadge status={detail.event.status} />
                </>
              ) : null}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={() => void verifyNow()}
                disabled={verifying}
                style={buttonStyle}
              >
                {verifying ? "verifying…" : "verify recovery now"}
              </button>
              <button
                type="button"
                onClick={closeEpisode}
                style={secondaryButtonStyle}
              >
                close
              </button>
            </div>
          </div>
          {notice ? (
            <div style={{ marginTop: 8, fontSize: 12, opacity: 0.9 }}>{notice}</div>
          ) : null}

          {detail ? (
            <div style={{ marginTop: 12, fontSize: 12, display: "grid", gap: 12 }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: 10,
                }}
              >
                <div>
                  <div style={{ fontWeight: 600 }}>Path decision (engine output)</div>
                  <div style={{ opacity: 0.85 }}>
                    selected: {detail.event.decision.selected_path ?? "none"} · score{" "}
                    {detail.event.decision.score ?? "—"} ·{" "}
                    {detail.event.decision.decision ?? "—"}
                  </div>
                  <div style={{ opacity: 0.7 }}>
                    {detail.event.decision.reason ?? "no reason recorded"}
                  </div>
                  <div style={{ opacity: 0.7 }}>
                    path change: {detail.event.link_from_name ?? "—"} →{" "}
                    {detail.event.link_to_name ?? "—"}
                  </div>
                </div>
                <div>
                  <div style={{ fontWeight: 600 }}>Trigger</div>
                  <div style={{ opacity: 0.85 }}>
                    {String(detail.event.trigger.reason ?? "not recorded")}
                  </div>
                  <div style={{ opacity: 0.7 }}>
                    path failure: {String(Boolean(detail.event.trigger.path_failure))}{" "}
                    · critical breach: {String(Boolean(detail.event.trigger.critical))}
                  </div>
                  <div style={{ opacity: 0.7 }}>
                    engine failover flag:{" "}
                    {String(Boolean(detail.event.trigger.engine_failover_flag))} (also
                    true for merely degraded paths)
                  </div>
                </div>
                <div>
                  <div style={{ fontWeight: 600 }}>Coverage</div>
                  <div style={{ opacity: 0.85 }}>
                    duration {seconds(replay?.coverage.duration_seconds)} ·{" "}
                    {replay?.coverage.still_open ? "still open" : "closed"}
                  </div>
                  <div style={{ opacity: 0.7 }}>
                    provenance: captured {replay?.provenance_summary.captured ?? 0} ·
                    derived {replay?.provenance_summary.derived ?? 0} · context{" "}
                    {replay?.provenance_summary.context ?? 0}
                  </div>
                  {replay?.coverage.is_partial ? (
                    <div style={{ color: "#d9a441" }}>
                      partial record — see gaps below
                    </div>
                  ) : null}
                </div>
              </div>

              {replay?.gaps.length ? (
                <div
                  style={{
                    border: "1px solid rgba(217, 164, 65, 0.4)",
                    borderRadius: 8,
                    padding: "6px 10px",
                    color: "#d9a441",
                  }}
                >
                  <div style={{ fontWeight: 600 }}>What is NOT in this record</div>
                  <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                    {replay.gaps.map((gap) => (
                      <li key={gap}>{gap}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {replay?.phases.length ? (
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Phases</div>
                  {replay.phases.map((phase) => (
                    <div
                      key={`${phase.started_at}-${phase.to}`}
                      style={{ opacity: 0.85 }}
                    >
                      {phase.from ?? "start"} → {phase.to ?? "?"}:{" "}
                      {seconds(phase.seconds)}
                    </div>
                  ))}
                </div>
              ) : null}

              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  Timeline ({replay?.timeline.length ?? 0} entries)
                </div>
                {replay?.timeline.length ? (
                  replay.timeline.map((entry, index) => (
                    <div
                      key={`${entry.at}-${entry.kind}-${index}`}
                      style={{
                        borderLeft: "2px solid rgba(90, 140, 200, 0.35)",
                        paddingLeft: 10,
                        marginBottom: 6,
                      }}
                    >
                      <div>
                        <ProvenanceBadge provenance={entry.provenance} />
                        <span style={{ opacity: 0.7 }}>{when(entry.at)}</span>{" "}
                        {entry.title}
                      </div>
                      <div style={{ opacity: 0.6 }}>
                        {entry.kind} · {entry.source}
                      </div>
                    </div>
                  ))
                ) : (
                  <NotAvailable detail="No timeline entries were recorded." />
                )}
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  Recovery verification
                  {verification?.trigger ? ` (${verification.trigger})` : ""}
                </div>
                {verification ? (
                  <>
                    <div style={{ marginBottom: 4 }}>
                      <span
                        style={{
                          ...chipStyle,
                          borderColor:
                            verification.status === "pass"
                              ? "#4fae7a"
                              : verification.status === "fail"
                                ? "#c9534f"
                                : "#d9a441",
                          color:
                            verification.status === "pass"
                              ? "#4fae7a"
                              : verification.status === "fail"
                                ? "#c9534f"
                                : "#d9a441",
                        }}
                      >
                        {verification.status}
                      </span>
                      {verification.summary}
                    </div>
                    <table style={tableStyle}>
                      <thead>
                        <tr>
                          <th style={thStyle}>check</th>
                          <th style={thStyle}>result</th>
                          <th style={thStyle}>detail</th>
                          <th style={thStyle}>source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {verification.checks.map((check) => (
                          <tr key={check.name}>
                            <td style={tdStyle}>{check.name}</td>
                            <td
                              style={{
                                ...tdStyle,
                                color:
                                  check.status === "pass"
                                    ? "#4fae7a"
                                    : check.status === "fail"
                                      ? "#c9534f"
                                      : "#d9a441",
                              }}
                            >
                              {check.status}
                            </td>
                            <td style={tdStyle}>{check.detail}</td>
                            <td style={{ ...tdStyle, opacity: 0.65 }}>
                              {check.source}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <NotAvailable detail="No verification has been recorded yet." />
                )}
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  Blast radius ({blast?.total ?? 0} affected)
                </div>
                {blast?.total ? (
                  <>
                    <div style={{ opacity: 0.75, marginBottom: 4 }}>
                      critical {blast.by_severity.critical ?? 0} · warning{" "}
                      {blast.by_severity.warning ?? 0} · info{" "}
                      {blast.by_severity.info ?? 0}
                      {blast.truncated
                        ? ` · TRUNCATED (${blast.truncated_reason ?? "bound reached"})`
                        : ""}
                      {blast.source ? ` · source: ${blast.source}` : ""}
                    </div>
                    {blast.nodes.slice(0, 20).map((node) => (
                      <div key={node.resource_id} style={{ opacity: 0.9 }}>
                        <span
                          style={{
                            ...chipStyle,
                            borderColor:
                              node.severity === "critical"
                                ? "#c9534f"
                                : node.severity === "warning"
                                  ? "#d9a441"
                                  : "#8a94a6",
                            color:
                              node.severity === "critical"
                                ? "#c9534f"
                                : node.severity === "warning"
                                  ? "#d9a441"
                                  : "#8a94a6",
                          }}
                        >
                          {node.severity}
                        </span>
                        {node.name ?? node.resource_id}
                        {node.resource_type ? ` (${node.resource_type})` : ""} ·{" "}
                        {node.hops} hop(s) via {node.via.join(" → ")}
                      </div>
                    ))}
                  </>
                ) : (
                  <NotAvailable detail="No dependents were found in the topology graph for this path." />
                )}
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  Evidence ({evidence.length} items)
                </div>
                {evidence.length ? (
                  evidence.map((item, index) => (
                    <div key={`${item.kind}-${index}`} style={{ marginBottom: 4 }}>
                      <span
                        style={{
                          ...chipStyle,
                          borderColor: FINDING_COLOR[item.finding] ?? "#8a94a6",
                          color: FINDING_COLOR[item.finding] ?? "#8a94a6",
                        }}
                      >
                        {item.finding}
                      </span>
                      <span style={{ opacity: 0.6 }}>
                        conf {item.confidence.toFixed(2)}
                      </span>{" "}
                      {item.claim}
                      <div style={{ opacity: 0.55 }}>source: {item.source}</div>
                    </div>
                  ))
                ) : (
                  <NotAvailable detail="No evidence items were recorded for this episode." />
                )}
              </div>
            </div>
          ) : (
            <NotAvailable detail="Loading the recorded episode…" />
          )}
        </div>
      ) : null}

      {/* Thresholds live with the episodes they explain: editing them is the one
          configuration action this surface owns, and every write is validated
          and audited by the backend. */}
      <OperationalSlaPolicyPanel siteId={siteId} />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <div
      style={{
        border: "1px solid rgba(90, 140, 200, 0.22)",
        borderRadius: 8,
        padding: "6px 10px",
      }}
    >
      <div style={{ opacity: 0.7, textTransform: "uppercase", fontSize: 10 }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const buttonStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "3px 10px",
  borderRadius: 6,
  border: "1px solid rgba(90, 140, 200, 0.5)",
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
};

const secondaryButtonStyle: React.CSSProperties = {
  ...buttonStyle,
  borderColor: "rgba(90, 140, 200, 0.25)",
  opacity: 0.85,
};
