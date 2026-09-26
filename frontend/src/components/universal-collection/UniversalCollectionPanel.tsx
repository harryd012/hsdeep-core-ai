"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  PlugZap,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import {
  MonitoringSourceOut,
  ReadinessReportOut,
  ReadinessResultOut,
  getReadinessReport,
  listMonitoringSources,
  probeSourceReadiness,
  syncMonitoringSource,
} from "@/lib/api";
import SnmpSourceForm from "./SnmpSourceForm";

/** Shared styles so rows stay visually consistent with the other HUD panels. */
const warningStyle: React.CSSProperties = {
  display: "flex",
  gap: 6,
  alignItems: "center",
  color: "var(--amber)",
  fontSize: 12,
};

const rowStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "150px 130px 1fr auto",
  gap: 10,
  alignItems: "center",
  padding: "8px 10px",
  border: "1px solid var(--line)",
  borderRadius: 6,
};

function buttonStyle(color: string): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "4px 8px",
    fontSize: 11,
    color,
    background: "transparent",
    border: `1px solid ${color}`,
    borderRadius: 4,
    cursor: "pointer",
  };
}

/**
 * Map a readiness status to a colour and icon. Kept as data (not scattered
 * ternaries) so every row renders a given status identically.
 */
function tone(status: string): { color: string; Icon: typeof CheckCircle2 } {
  if (status === "ready") return { color: "var(--green)", Icon: CheckCircle2 };
  if (status === "configuration_error" || status === "credential_error") {
    return { color: "var(--amber)", Icon: AlertTriangle };
  }
  if (status === "unreachable" || status === "unsupported") {
    return { color: "var(--muted)", Icon: AlertTriangle };
  }
  return { color: "var(--red)", Icon: XCircle };
}

function CoverageRows({
  report,
  loaded,
}: {
  report: ReadinessReportOut | null;
  loaded: boolean;
}) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {!loaded && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>Checking readiness…</p>
      )}
      {loaded &&
        (report?.coverage ?? []).map((row) => {
          const { color, Icon } = tone(row.status);
          return (
            <div
              key={`${row.protocol}-${row.target}-${row.status}`}
              style={rowStyle}
            >
              <strong style={{ fontSize: 13 }}>{row.protocol}</strong>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color, fontSize: 12 }}>
                <Icon size={13} /> {row.status}
              </span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>
                {row.message}
                <span style={{ opacity: 0.7 }}> · target {row.target}</span>
              </span>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>
                stage {row.stage}
              </span>
            </div>
          );
        })}
    </div>
  );
}

function SourceRows({
  report,
  loaded,
  busy,
  onProbe,
  onCollect,
}: {
  report: ReadinessReportOut | null;
  loaded: boolean;
  busy: string | null;
  onProbe: (sourceId: string) => void;
  onCollect: (sourceId: string) => void;
}) {
  const results: ReadinessResultOut[] = report?.results ?? [];
  return (
    <div style={{ marginTop: 16 }}>
      <h3 style={{ fontSize: 13, color: "var(--cyan)" }}>
        CONFIGURED SOURCES ({report?.sources_checked ?? 0})
      </h3>
      {loaded && results.length === 0 && (
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          No monitoring sources are configured for this tenant.
        </p>
      )}
      <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
        {results.map((row) => {
          const { color, Icon } = tone(row.status);
          const sourceId = String(row.safe_details?.source_id ?? "");
          const name = String(row.safe_details?.name ?? row.target);
          const sourceType = String(row.safe_details?.source_type ?? "");
          const noTarget = row.target === "not configured";
          return (
            <div
              key={sourceId || `${name}-${row.target}`}
              style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 6 }}
            >
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 13 }}>{name}</strong>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>{sourceType}</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color, fontSize: 12 }}>
                  <Icon size={13} /> {row.status}
                </span>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  {row.protocol} · {row.target} · stage {row.stage}
                </span>
                <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    onClick={() => onProbe(sourceId)}
                    disabled={!sourceId || busy !== null}
                    title="Read-only reachability check. Neither collects nor persists anything."
                    style={buttonStyle("var(--cyan)")}
                  >
                    <PlugZap size={13} /> {busy === `probe:${sourceId}` ? "Probing…" : "Probe"}
                  </button>
                  <button
                    type="button"
                    onClick={() => onCollect(sourceId)}
                    disabled={!sourceId || busy !== null}
                    title="Run a real collection cycle. This persists inventory and may create relationships."
                    style={buttonStyle("var(--green)")}
                  >
                    <RefreshCw size={13} /> {busy === `collect:${sourceId}` ? "Collecting…" : "Collect"}
                  </button>
                </span>
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
                {row.message}
                {noTarget ? " — no target configured" : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HonestyNote() {
  return (
    <p
      style={{
        marginTop: 12,
        fontSize: 11,
        color: "var(--muted)",
        display: "flex",
        gap: 6,
        alignItems: "flex-start",
      }}
    >
      <ShieldAlert size={13} style={{ flexShrink: 0, marginTop: 2 }} />
      <span>
        <strong>ready</strong> means the transport answered and credentials were
        accepted — it does <em>not</em> mean interfaces, VLANs, STP, LAG or
        neighbours were collected. Relationships and topology are derived only from
        collected evidence, so a ready source with no collection produces no edges.
        Probe is read-only and creates no devices, interfaces or relationships.
      </span>
    </p>
  );
}

/**
 * Universal Collection control plane (Phase 5).
 *
 * Makes the existing Universal Collection pipeline visible and actionable:
 *   source -> readiness -> collect -> canonical inventory -> relationships -> topology
 *
 * It adds NO new backend capability. It surfaces the readiness diagnostics that
 * already exist and triggers the existing collection path.
 *
 * HONESTY RULES ENFORCED HERE
 * --------------------------
 * 1. "ready" means the transport answered and credentials were accepted. It does
 *    NOT mean anything was discovered. The panel says so, so a green chip is
 *    never read as "topology was built".
 * 2. "not configured" is shown explicitly for a protocol with no source, instead
 *    of the protocol silently vanishing.
 * 3. A configuration error is never rendered as a network failure, and vice
 *    versa - the stage the backend reported is shown verbatim.
 * 4. No credential material is requested, received or rendered here.
 */
export default function UniversalCollectionPanel() {
  const [report, setReport] = useState<ReadinessReportOut | null>(null);
  const [sources, setSources] = useState<MonitoringSourceOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    // The loader is declared INSIDE the effect (as the existing infrastructure
    // page does): calling an outer useCallback that sets state synchronously
    // from an effect body is flagged by react-hooks/set-state-in-effect.
    let cancelled = false;
    async function refresh() {
      // allSettled, not all: a slow or failing readiness report must never hide
      // the configured-source list (and vice versa) - the operator needs both
      // facts independently, and one timed-out call is not the other's fault.
      const [reportResult, sourcesResult] = await Promise.allSettled([
        getReadinessReport(),
        listMonitoringSources(),
      ]);
      if (cancelled) return;
      if (reportResult.status === "fulfilled") setReport(reportResult.value);
      if (sourcesResult.status === "fulfilled") setSources(sourcesResult.value);
      const failure = reportResult.status === "rejected" ? reportResult.reason
        : sourcesResult.status === "rejected" ? sourcesResult.reason
        : null;
      setError(failure instanceof Error ? failure.message : failure ? "Failed to load monitoring data" : null);
      if (!cancelled) setLoaded(true);
    }
    refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  /** Manual refresh after an action. Errors here are surfaced by the caller. */
  const load = useCallback(async () => {
    const [reportResult, sourcesResult] = await Promise.allSettled([
      getReadinessReport(),
      listMonitoringSources(),
    ]);
    if (reportResult.status === "fulfilled") setReport(reportResult.value);
    if (sourcesResult.status === "fulfilled") setSources(sourcesResult.value);
    const failure = reportResult.status === "rejected" ? reportResult.reason
      : sourcesResult.status === "rejected" ? sourcesResult.reason
      : null;
    setError(failure instanceof Error ? failure.message : failure ? "Failed to load monitoring data" : null);
    setLoaded(true);
  }, []);

  const onProbe = useCallback(
    async (sourceId: string) => {
      setBusy(`probe:${sourceId}`);
      setNotice(null);
      try {
        const result = await probeSourceReadiness(sourceId);
        setNotice(`${result.protocol} probe: ${result.status} — ${result.message}`);
        await load();
      } catch (err) {
        setNotice(err instanceof Error ? err.message : "Probe failed");
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  const onCollect = useCallback(
    async (sourceId: string) => {
      setBusy(`collect:${sourceId}`);
      setNotice(null);
      try {
        const result = await syncMonitoringSource(sourceId);
        setNotice(
          `Collection ${result.status}: ${result.sensors_created} created, ` +
            `${result.sensors_updated} updated`,
        );
        await load();
      } catch (err) {
        setNotice(err instanceof Error ? err.message : "Collection failed");
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  return (
    <section className="hud-panel">
      <h2>UNIVERSAL COLLECTION</h2>
      {error && (
        <p style={warningStyle}>
          <AlertTriangle size={14} /> {error}
        </p>
      )}
      <CoverageRows report={report} loaded={loaded} />
      <SourceRows
        report={report}
        loaded={loaded}
        busy={busy}
        onProbe={onProbe}
        onCollect={onCollect}
      />
      <SnmpSourceForm sources={sources} onSaved={load} />
      {notice && (
        <p style={{ marginTop: 10, fontSize: 12, color: "var(--cyan)" }}>{notice}</p>
      )}
      <HonestyNote />
    </section>
  );
}
