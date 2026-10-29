"use client";

// Opt out of static prerendering: the client component below reads
// useSearchParams(), which Next.js forbids during prerender without a
// Suspense boundary. We wrap it in a <Suspense> boundary (per App Router
// convention) so a loading fallback is shown while search params resolve.
export const dynamic = "force-dynamic";

import Link from "next/link";
import { Suspense, useEffect, useState, useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Brain, Zap } from "lucide-react";
import {
  Incident, AlertIntelligenceMetrics, CollectorHealthList, IncidentSeverity,
  listIncidents, getAlertIntelligenceMetrics, getCollectorHealth,
  acknowledgeIncident, investigateIncident, monitorIncident,
  recoverIncident, closeIncident, archiveIncident,
} from "@/lib/api";
import { formatAge } from "@/lib/commandCenter/format";
import {
  Banner,
  DataTable,
  PageShell,
  Panel,
  Pill,
  SearchBox,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/** A metric the backend did not report renders as "—", never as a measured 0. */
function nullOr(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

type Action = "acknowledge" | "investigate" | "monitor" | "recover" | "close" | "archive";

/** The single next lifecycle step offered for each status. */
const NEXT_ACTION: Record<string, { action: Action; label: string } | undefined> = {
  open: { action: "acknowledge", label: "Acknowledge" },
  acknowledged: { action: "investigate", label: "Investigate" },
  investigating: { action: "monitor", label: "Monitor" },
  monitoring: { action: "recover", label: "Recover" },
  recovered: { action: "close", label: "Close" },
  resolved: { action: "close", label: "Close" },
  closed: { action: "archive", label: "Archive" },
};

const SEVERITY_TONE = { critical: "bad", warning: "warn", info: "info" } as const;
type SeverityTab = "all" | IncidentSeverity;

function IncidentIntelligenceContent() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [metrics, setMetrics] = useState<AlertIntelligenceMetrics | null>(null);
  const [collectorHealth, setCollectorHealth] = useState<CollectorHealthList | null>(null);
  const [errors, setErrors] = useState<{ incidents?: string; metrics?: string; collectors?: string }>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/incidents";
  const severity = (searchParams?.get("severity") as IncidentSeverity | null) ?? null;

  // Each read settles on its own: the intelligence aggregate can take far
  // longer than the incident list on a large estate, and must not hold the
  // list (or each other) hostage.
  const load = useCallback(async () => {
    const msg = (r: PromiseRejectedResult) => (r.reason instanceof Error ? r.reason.message : "Request failed");
    const [inc, met, col] = await Promise.allSettled([
      listIncidents({ severity: severity ?? undefined, limit: 50 }),
      getAlertIntelligenceMetrics(),
      getCollectorHealth(),
    ]);
    if (inc.status === "fulfilled") setIncidents(inc.value);
    if (met.status === "fulfilled") setMetrics(met.value);
    if (col.status === "fulfilled") setCollectorHealth(col.value);
    setErrors({
      incidents: inc.status === "rejected" ? msg(inc) : undefined,
      metrics: met.status === "rejected" ? msg(met) : undefined,
      collectors: col.status === "rejected" ? msg(col) : undefined,
    });
    setUpdatedAt(new Date().toISOString());
  }, [severity]);

  useEffect(() => {
    // First load is scheduled, not called inline, so no state update happens
    // synchronously inside the effect body.
    const first = window.setTimeout(load, 0);
    const timer = window.setInterval(load, 15000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [load]);

  const handleAction = async (incidentId: string, action: Action) => {
    setActionLoading(`${incidentId}:${action}`);
    setActionError(null);
    try {
      if (action === "acknowledge") await acknowledgeIncident(incidentId, "00000000-0000-0000-0000-000000000000");
      else if (action === "investigate") await investigateIncident(incidentId);
      else if (action === "monitor") await monitorIncident(incidentId);
      else if (action === "recover") await recoverIncident(incidentId);
      else if (action === "close") await closeIncident(incidentId);
      else if (action === "archive") await archiveIncident(incidentId);
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Failed to ${action} incident`);
    } finally {
      setActionLoading(null);
    }
  };

  const setSeverity = (tab: SeverityTab) => {
    const next = new URLSearchParams(searchParams?.toString() ?? "");
    if (tab === "all") next.delete("severity");
    else next.set("severity", tab);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const list = incidents ?? [];
  const q = search.trim().toLowerCase();
  const visible = list.filter(
    (i) => !q || [i.title, i.device_name, i.site_name, i.correlation_type].some((v) => (v ?? "").toLowerCase().includes(q)),
  );

  const columns: Column<Incident>[] = [
    {
      key: "severity",
      header: "Severity",
      width: 96,
      sortValue: (i) => ({ critical: 0, warning: 1, info: 2 })[i.severity] ?? 3,
      render: (i) => <Pill tone={SEVERITY_TONE[i.severity] ?? "neutral"}>{titleCaseWords(i.severity)}</Pill>,
    },
    {
      key: "incident",
      header: "Incident",
      sortValue: (i) => i.title.toLowerCase(),
      render: (i) => (
        <>
          <span className="pk-cell-main" style={{ whiteSpace: "normal" }}>{i.title}</span>
          <span className="pk-cell-sub" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <span>{i.alert_count} alerts · {i.total_occurrences.toLocaleString("en-US")} occurrences</span>
            {(i.device_name || i.site_name) && <span>{[i.device_name, i.site_name].filter(Boolean).join(" · ")}</span>}
            {i.correlation_type && (
              <span style={{ display: "inline-flex", gap: 3, alignItems: "center", color: "var(--ecc-accent)" }}>
                <Brain size={11} aria-hidden="true" /> {titleCaseWords(i.correlation_type)}
              </span>
            )}
            {i.automation_available && (
              <span style={{ display: "inline-flex", gap: 3, alignItems: "center", color: "var(--ecc-good)" }}>
                <Zap size={11} aria-hidden="true" /> SOP available
              </span>
            )}
          </span>
        </>
      ),
    },
    { key: "status", header: "Status", sortValue: (i) => i.status, render: (i) => <Pill tone={statusTone(i.status)}>{titleCaseWords(i.status)}</Pill> },
    { key: "age", header: "Open for", sortValue: (i) => -new Date(i.opened_at).getTime(), render: (i) => formatAge(i.opened_at) },
    { key: "last", header: "Last alert", sortValue: (i) => new Date(i.last_alert_at).getTime(), render: (i) => <span className="pk-muted">{ago(i.last_alert_at)}</span> },
    { key: "assigned", header: "Assigned", sortValue: (i) => i.assigned_team ?? "", render: (i) => (i.assigned_team ? titleCaseWords(i.assigned_team) : <span className="pk-muted">Unassigned</span>) },
    {
      key: "action",
      header: "",
      render: (i) => {
        const next = NEXT_ACTION[i.status];
        return (
          <span style={{ display: "inline-flex", gap: 6 }} onClick={(e) => e.stopPropagation()}>
            {next && (
              <button
                type="button"
                className="pk-button"
                style={{ height: 26, fontSize: 12 }}
                disabled={actionLoading !== null}
                onClick={() => handleAction(i.id, next.action)}
              >
                {actionLoading === `${i.id}:${next.action}` ? "Working…" : next.label}
              </button>
            )}
            {i.automation_available && (
              <Link className="pk-button" style={{ height: 26, fontSize: 12 }} href="/sop-engine">Run SOP</Link>
            )}
          </span>
        );
      },
    },
  ];

  const metricsLoading = !metrics && !errors.metrics;
  const tabs: { key: SeverityTab; label: string }[] = [
    { key: "all", label: "All" },
    { key: "critical", label: "Critical" },
    { key: "warning", label: "Warning" },
    { key: "info", label: "Info" },
  ];

  return (
    <PageShell
      title="Incidents"
      subtitle="Correlated incidents built from related alerts, with the next response step for each."
      meta={updatedAt ? `Updated ${ago(updatedAt)} · refreshes every 15 s` : undefined}
    >
      {actionError && <Banner tone="bad">{actionError}</Banner>}
      {errors.metrics && <Banner tone="warn">Alert-intelligence metrics are unavailable: {errors.metrics}</Banner>}

      <StatStrip>
        <StatTile label="Active incidents" value={nullOr(metrics?.active_incidents)} info="Incidents open, acknowledged or investigating." loading={metricsLoading} naText={errors.metrics ? "Unavailable" : undefined} />
        <StatTile label="Correlated" value={nullOr(metrics?.correlated_incidents)} info="Incidents that grouped more than one alert." loading={metricsLoading} naText={errors.metrics ? "Unavailable" : undefined} />
        <StatTile label="Suppressed alerts" value={nullOr(metrics?.suppressed_alerts)} info="Alerts suppressed as symptoms of an existing incident." loading={metricsLoading} naText={errors.metrics ? "Unavailable" : undefined} />
        <StatTile label="Duplicates removed" value={nullOr(metrics?.duplicate_alerts_removed)} info="Repeat alerts folded into an existing alert." loading={metricsLoading} naText={errors.metrics ? "Unavailable" : undefined} />
        <StatTile label="Noise reduction" value={metrics ? `${metrics.noise_reduction_pct}%` : "—"} info="Share of raw alert volume that did not become a separate incident." loading={metricsLoading} naText={errors.metrics ? "Unavailable" : undefined} />
      </StatStrip>

      <div className="pk-grid">
        <Panel
          title="Incidents"
          info="The 50 most recent incidents. Select a row to open its investigation workspace."
          aside={incidents ? `${visible.length} of ${list.length}` : undefined}
          state={incidents ? "ready" : errors.incidents ? "error" : "loading"}
          errorText={errors.incidents ? `Incidents could not be loaded: ${errors.incidents}` : undefined}
          onRetry={load}
          skeletonHeight={360}
          className="pk-col-8"
        >
          <div className="pk-toolbar" style={{ marginBottom: 10 }}>
            <SearchBox value={search} onChange={setSearch} placeholder="Search title, device, site" label="Search incidents" />
            <Tabs label="Severity" value={(severity ?? "all") as SeverityTab} onChange={setSeverity} tabs={tabs} />
          </div>
          <DataTable
            caption="Incidents"
            columns={columns}
            rows={visible}
            rowKey={(i) => i.id}
            rowHref={(i) => `/incidents/${i.id}`}
            defaultSort={{ key: "last", dir: "desc" }}
            emptyText={list.length === 0 ? "No incidents. All systems operational." : "No incidents match the current filter."}
            maxHeight={620}
          />
        </Panel>

        <Panel
          title="Collector health"
          info="Run-based health of each collection source feeding these incidents."
          aside={<Link className="ecc-link" href="/monitoring">Monitoring →</Link>}
          state={collectorHealth ? (collectorHealth.collectors.length ? "ready" : "empty") : errors.collectors ? "error" : "loading"}
          emptyText="No collectors configured."
          errorText={errors.collectors ? `Collector health is unavailable: ${errors.collectors}` : undefined}
          onRetry={load}
          className="pk-col-4"
        >
          {collectorHealth && (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                <Pill tone="good">{collectorHealth.healthy_count} healthy</Pill>
                <Pill tone="warn">{collectorHealth.degraded_count} degraded</Pill>
                <Pill tone="bad">{collectorHealth.failing_count} failing</Pill>
                <Pill tone="neutral">{collectorHealth.unknown_count} unknown</Pill>
              </div>
              <ul className="pk-list">
                {collectorHealth.collectors.map((c) => (
                  <li key={c.source_id}>
                    <div className="pk-list__main">
                      <span className="pk-cell-main">{c.source_name}</span>
                      <span className="pk-cell-sub">{c.success_rate_pct}% success · {c.total_runs} runs</span>
                      {c.last_error && <span className="pk-cell-sub pk-truncate" style={{ color: "var(--ecc-bad)" }} title={c.last_error}>{c.last_error}</span>}
                    </div>
                    <div className="pk-list__aside">
                      <Pill tone={c.current_state === "failing" ? "bad" : statusTone(c.current_state)}>{titleCaseWords(c.current_state)}</Pill>
                      <div style={{ marginTop: 4 }}>{c.last_success_at ? `OK ${ago(c.last_success_at)}` : "No success yet"}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}

export default function IncidentIntelligencePage() {
  return (
    <Suspense fallback={<PageShell title="Incidents"><Panel title="Incidents" state="loading" skeletonHeight={360} /></PageShell>}>
      <IncidentIntelligenceContent />
    </Suspense>
  );
}
