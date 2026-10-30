"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { getCollectorHealth, getCollectorRuns, listMonitoringSources, type CollectorHealth, type CollectorRun } from "@/lib/api";
import {
  Banner,
  BarList,
  DataTable,
  DayColumns,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  dayBuckets,
  titleCaseWords,
  type Column,
  type Tone,
} from "@/components/ui/PageKit";

const WINDOWS = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 } as const;
type WindowKey = keyof typeof WINDOWS;
/** Recent runs loaded for the timeline and the run log. */
const RUN_SAMPLE = 500;

const STATE_TONE: Record<CollectorHealth["current_state"], Tone> = { healthy: "good", degraded: "warn", failing: "bad", unknown: "neutral" };
const REASON_LABEL: Record<string, string> = {
  credentials: "Credentials rejected",
  unreachable: "Host unreachable",
  no_collector_registered: "No collector registered",
  other: "Other error",
};
const RUN_SERIES = [
  { key: "success", label: "Success", color: "var(--ecc-good)" },
  { key: "partial", label: "Partial", color: "var(--ecc-warn)" },
  { key: "error", label: "Error", color: "var(--ecc-bad)" },
  { key: "skipped", label: "Skipped", color: "var(--ecc-unknown)" },
];

export default function CollectorPerformancePage() {
  const [win, setWin] = useState<WindowKey>("7d");
  const health = useQuery({ queryKey: ["reports", "collector-health", win], queryFn: () => getCollectorHealth(undefined, WINDOWS[win]), refetchInterval: 60_000 });
  const runs = useQuery({ queryKey: ["reports", "collector-runs"], queryFn: () => getCollectorRuns({ limit: RUN_SAMPLE }), refetchInterval: 60_000 });
  const sources = useQuery({ queryKey: ["reports", "monitoring-sources"], queryFn: () => listMonitoringSources(), refetchInterval: 60_000 });

  const collectors = useMemo(() => health.data?.collectors ?? [], [health.data]);
  const runList = useMemo(() => runs.data ?? [], [runs.data]);
  const sourceName = useMemo(() => new Map((sources.data ?? []).map((s) => [s.id, s.name])), [sources.data]);

  const totalRuns = collectors.reduce((sum, c) => sum + c.total_runs, 0);
  const okRuns = collectors.reduce((sum, c) => sum + c.successful_runs, 0);
  const successRate = totalRuns > 0 ? `${Math.round((okRuns / totalRuns) * 100)}%` : "—";
  const lastSuccess = collectors.reduce<string | null>((m, c) => (c.last_success_at && (!m || c.last_success_at > m) ? c.last_success_at : m), null);
  const healthState = health.isLoading ? "loading" : health.error ? "error" : collectors.length === 0 ? "empty" : "ready";
  const runState = runs.isLoading ? "loading" : runs.error ? "error" : runList.length === 0 ? "empty" : "ready";

  // Collectors poll every few minutes, so the recent sample often spans hours,
  // not days: bucket hourly when it covers under two days.
  const oldestRun = runList.reduce<string | null>((m, r) => (!m || r.started_at < m ? r.started_at : m), null);
  const sampleHours = oldestRun && runs.dataUpdatedAt ? (runs.dataUpdatedAt - new Date(oldestRun).getTime()) / 3_600_000 : null;
  const hourly = sampleHours !== null && sampleHours < 48;
  const timeline = useMemo(() => {
    if (!hourly || !runs.dataUpdatedAt) return dayBuckets(runList, (r) => r.started_at, (r) => r.status, 14);
    const hours = Math.max(6, Math.ceil(sampleHours ?? 0) + 1);
    const end = new Date(runs.dataUpdatedAt);
    end.setMinutes(0, 0, 0);
    const start = end.getTime() - (hours - 1) * 3_600_000;
    const buckets = Array.from({ length: hours }, (_, i) => {
      const t = new Date(start + i * 3_600_000);
      return { key: t.toISOString(), label: t.toLocaleTimeString("en-US", { hour: "numeric" }), values: {} as Record<string, number> };
    });
    for (const r of runList) {
      const idx = Math.floor((new Date(r.started_at).getTime() - start) / 3_600_000);
      if (idx >= 0 && idx < hours) buckets[idx].values[r.status] = (buckets[idx].values[r.status] ?? 0) + 1;
    }
    return buckets;
  }, [runList, hourly, sampleHours, runs.dataUpdatedAt]);
  const reasons = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of collectors) if (c.failure_category) counts.set(c.failure_category, (counts.get(c.failure_category) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [collectors]);
  const errorMessages = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of runList) if (r.error) {
      const k = r.error.split("\n")[0].slice(0, 120);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [runList]);

  const collectorColumns: Column<CollectorHealth>[] = [
    {
      key: "source",
      header: "Source",
      sortValue: (c) => c.source_name,
      render: (c) => (
        <>
          <span className="pk-cell-main">{c.source_name}</span>
          {c.last_error && <span className="pk-cell-sub pk-truncate" style={{ color: "var(--ecc-bad)" }} title={c.last_error}>{c.last_error}</span>}
        </>
      ),
    },
    { key: "state", header: "State", sortValue: (c) => c.current_state, render: (c) => <Pill tone={STATE_TONE[c.current_state]}>{c.current_state}</Pill> },
    {
      key: "rate",
      header: "Success rate",
      sortValue: (c) => (c.total_runs ? c.success_rate_pct : null),
      render: (c) =>
        c.total_runs ? (
          <span className="pk-rate">
            <span className="pk-rate__track"><span style={{ width: `${c.success_rate_pct}%`, background: c.success_rate_pct >= 95 ? "var(--ecc-good)" : c.success_rate_pct >= 50 ? "var(--ecc-warn)" : "var(--ecc-bad)" }} /></span>
            {Math.round(c.success_rate_pct)}%
          </span>
        ) : <span className="pk-muted">No runs</span>,
    },
    { key: "runs", header: "Runs", numeric: true, sortValue: (c) => c.total_runs, render: (c) => c.total_runs.toLocaleString("en-US") },
    { key: "failed", header: "Failed", numeric: true, sortValue: (c) => c.failed_runs, render: (c) => (c.failed_runs ? <span style={{ color: "var(--ecc-bad)" }}>{c.failed_runs.toLocaleString("en-US")}</span> : "0") },
    { key: "ok", header: "Last success", sortValue: (c) => c.last_success_at, render: (c) => <span className="pk-muted">{c.last_success_at ? ago(c.last_success_at) : "Never in window"}</span> },
    { key: "reason", header: "Failure reason", sortValue: (c) => c.failure_category, render: (c) => (c.failure_category ? REASON_LABEL[c.failure_category] ?? titleCaseWords(c.failure_category) : <span className="pk-muted">—</span>) },
  ];

  const runColumns: Column<CollectorRun>[] = [
    { key: "started", header: "Started", sortValue: (r) => r.started_at, render: (r) => <span className="pk-muted" title={r.started_at}>{new Date(r.started_at).toLocaleString()}</span> },
    { key: "source", header: "Source", sortValue: (r) => sourceName.get(r.monitoring_source_id) ?? r.monitoring_source_id, render: (r) => sourceName.get(r.monitoring_source_id) ?? <span className="pk-muted">{r.monitoring_source_id.slice(0, 8)}</span> },
    { key: "status", header: "Outcome", sortValue: (r) => r.status, render: (r) => <Pill tone={r.status === "success" ? "good" : r.status === "partial" ? "warn" : r.status === "running" ? "info" : r.status === "skipped" ? "neutral" : "bad"}>{r.status}</Pill> },
    { key: "trigger", header: "Trigger", sortValue: (r) => r.trigger, render: (r) => <span className="pk-muted">{r.trigger}</span> },
    {
      key: "duration",
      header: "Duration",
      numeric: true,
      sortValue: (r) => (r.finished_at ? new Date(r.finished_at).getTime() - new Date(r.started_at).getTime() : null),
      render: (r) => (r.finished_at ? `${((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000).toFixed(1)} s` : "—"),
    },
    { key: "collected", header: "Sensors / metrics", numeric: true, sortValue: (r) => r.metrics_recorded, render: (r) => `${r.sensors_upserted} / ${r.metrics_recorded}` },
    { key: "error", header: "Error", render: (r) => (r.error ? <span className="pk-truncate" style={{ color: "var(--ecc-bad)", display: "block", maxWidth: 420 }} title={r.error}>{r.error}</span> : <span className="pk-muted">—</span>) },
  ];

  return (
    <PageShell
      title="Collector Performance"
      icon={Activity}
      crumbs={[{ label: "Reports", href: "/reports" }, { label: "Collector Performance" }]}
      subtitle="Whether each monitoring source is actually delivering data: success rate, failure reasons and the run history behind them."
      actions={<Tabs<WindowKey> value={win} onChange={setWin} tabs={[{ key: "24h", label: "24 h" }, { key: "7d", label: "7 days" }, { key: "30d", label: "30 days" }]} label="Health window" />}
    >
      {health.data && health.data.failing_count > 0 && health.data.healthy_count === 0 && (
        <Banner tone="bad">
          No collector is healthy in this window ({health.data.failing_count} failing). Every health number elsewhere in the product is frozen at
          the last successful collection{lastSuccess ? `, ${ago(lastSuccess)}` : ""}.
        </Banner>
      )}

      <StatStrip>
        <StatTile label="Sources" value={health.data ? String(collectors.length) : "—"} info="Monitoring sources with collector runs (test sources excluded)." loading={health.isLoading} />
        <StatTile label="Healthy" value={health.data ? String(health.data.healthy_count) : "—"} tone="good" info="Sources whose runs are succeeding in this window." loading={health.isLoading} />
        <StatTile label="Degraded" value={health.data ? String(health.data.degraded_count) : "—"} tone="warn" info="Sources with some failed runs." loading={health.isLoading} />
        <StatTile label="Failing" value={health.data ? String(health.data.failing_count) : "—"} alarm={(health.data?.failing_count ?? 0) > 0} info="Sources whose runs are failing." loading={health.isLoading} />
        <StatTile label="Success rate" value={successRate} alarm={totalRuns > 0 && okRuns / totalRuns < 0.5} sub={totalRuns ? `${okRuns.toLocaleString("en-US")} of ${totalRuns.toLocaleString("en-US")} runs` : undefined} badge={win} info="Successful runs over all runs in the window, across every source." loading={health.isLoading} />
        <StatTile label="Last successful run" value={lastSuccess ? ago(lastSuccess) : "—"} naText="None in window" info="Most recent successful collection by any source." loading={health.isLoading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel title="Run outcomes" info={`Runs per ${hourly ? "hour" : "day"} by outcome, for the ${RUN_SAMPLE} most recent runs.`} aside={`latest ${runList.length} runs · per ${hourly ? "hour" : "day"}`} state={runState} className="pk-col-8">
          <DayColumns label={`Collector runs per ${hourly ? "hour" : "day"}`} buckets={timeline} series={RUN_SERIES} height={170} />
        </Panel>
        <Panel title="Why runs fail" info="Last failure reason per source, and the most frequent error messages in recent runs." state={healthState === "ready" || runState === "ready" ? "ready" : healthState} className="pk-col-4">
          <BarList rows={reasons.map(([k, v]) => ({ key: k, label: REASON_LABEL[k] ?? titleCaseWords(k), value: v, color: "var(--ecc-bad)" }))} valueLabel={(v) => `${v} source${v === 1 ? "" : "s"}`} emptyText="No source is failing." />
          {errorMessages.length > 0 && (
            <>
              <h3 className="pk-subhead">Most frequent errors</h3>
              <BarList rows={errorMessages.map(([k, v]) => ({ key: k, label: k, title: k, value: v, color: "var(--ecc-warn)" }))} />
            </>
          )}
        </Panel>

        <Panel title="Sources" info="Per-source reliability in the selected window." aside={win} state={healthState} className="pk-col-12" emptyText="No collector has run in this window.">
          <DataTable caption="Collector health by source" columns={collectorColumns} rows={collectors} rowKey={(c) => c.source_id} rowHref={() => "/monitoring"} defaultSort={{ key: "rate", dir: "asc" }} maxHeight={420} />
        </Panel>

        <Panel title="Run log" info="Every recent collector run, newest first." aside={`latest ${runList.length}`} state={runState} className="pk-col-12">
          <DataTable caption="Collector runs" columns={runColumns} rows={runList} rowKey={(r) => r.id} defaultSort={{ key: "started", dir: "desc" }} maxHeight={480} />
        </Panel>
      </div>
    </PageShell>
  );
}
