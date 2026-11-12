"use client";

import Link from "next/link";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

import { ApmActions, useApmRange } from "@/components/apm/ApmRange";
import {
  formatMs,
  getServiceDependencies,
  getServiceOperations,
  getServiceTimeseries,
  getTraceErrors,
  getTraceServices,
  listTraces,
  servicePath,
  type ErrorGroup,
  type OperationStats,
  type ServiceEdge,
  type TraceSummary,
} from "@/lib/traces";
import { Banner, DataTable, LineChart, PageShell, Panel, Pill, StatStrip, StatTile, ago, type Column } from "@/components/ui/PageKit";

/**
 * /apm/services/{name} — one service: request rate, error rate and latency
 * (p50 / p95 / p99 of its entry spans) over the window, its operations, the
 * services that call it and that it calls, its errors, and recent failed and
 * slow traces (each opens the trace waterfall).
 */

export default function ApmServicePage({ params }: { params: Promise<{ name: string[] }> }) {
  const { name } = use(params);
  const service = name.map(decodeURIComponent).join("/");
  const apm = useApmRange();
  const w = apm.window;

  const ts = useQuery({ queryKey: ["apm", "timeseries", service, w], queryFn: () => getServiceTimeseries(service, w) });
  const ops = useQuery({ queryKey: ["apm", "operations", service, w], queryFn: () => getServiceOperations(service, w) });
  const deps = useQuery({ queryKey: ["apm", "dependencies", service, w], queryFn: () => getServiceDependencies(service, w) });
  const errs = useQuery({ queryKey: ["apm", "errors", service, w], queryFn: () => getTraceErrors(w, service) });
  const stats = useQuery({ queryKey: ["traces", "services", w], queryFn: () => getTraceServices(w) });
  const summary = stats.data?.find((s) => s.service === service);
  const failed = useQuery({
    queryKey: ["traces", "list", w, service, "failed"],
    queryFn: () => listTraces(w, { service, errorsOnly: true, limit: 10 }),
  });
  const slowFloor = summary?.p95_ms ?? null;
  const slow = useQuery({
    queryKey: ["traces", "list", w, service, "slow", slowFloor],
    queryFn: () => listTraces(w, { service, minDurationMs: slowFloor, limit: 10 }),
    enabled: slowFloor !== null,
  });

  const points = ts.data?.points ?? [];
  const bucketMs = (ts.data?.bucket_seconds ?? 60) * 1000;
  const requests = points.reduce((n, p) => n + p.requests, 0);
  const errors = points.reduce((n, p) => n + p.errors, 0);
  const perMinute = requests && points.length ? requests / ((points.length * bucketMs) / 60_000) : 0;
  const latency = points.filter((p) => p.p95_ms !== null).map((p) => ({ t: Date.parse(p.t), value: p.p95_ms!, min: p.p50_ms, max: p.p99_ms }));

  const opColumns: Column<OperationStats>[] = [
    { key: "name", header: "Operation", sortValue: (o) => o.name, render: (o) => <><strong>{o.name}</strong>{o.entry && <span className="pk-muted"> · entry</span>}</> },
    { key: "calls", header: "Calls", numeric: true, sortValue: (o) => o.calls, render: (o) => o.calls.toLocaleString("en-US") },
    { key: "err", header: "Error rate", numeric: true, sortValue: (o) => o.error_rate_pct,
      render: (o) => (o.errors ? <Pill tone="bad">{o.error_rate_pct} %</Pill> : <span className="pk-muted">0 %</span>) },
    { key: "p50", header: "p50", numeric: true, sortValue: (o) => o.p50_ms, render: (o) => formatMs(o.p50_ms) },
    { key: "p95", header: "p95", numeric: true, sortValue: (o) => o.p95_ms, render: (o) => formatMs(o.p95_ms) },
    { key: "share", header: "Time share", numeric: true, sortValue: (o) => o.time_share_pct, render: (o) => `${o.time_share_pct} %` },
  ];
  const edgeColumns = (other: "source" | "target"): Column<ServiceEdge>[] => [
    { key: "svc", header: "Service", render: (e) => <Link href={apm.withRange(`/apm/services/${servicePath(e[other])}`)}>{e[other]}</Link> },
    { key: "calls", header: "Calls", numeric: true, sortValue: (e) => e.calls, render: (e) => e.calls.toLocaleString("en-US") },
    { key: "errors", header: "Errors", numeric: true, sortValue: (e) => e.errors, render: (e) => (e.errors ? <Pill tone="bad">{e.errors}</Pill> : <span className="pk-muted">0</span>) },
    { key: "avg", header: "Avg", numeric: true, sortValue: (e) => e.avg_ms, render: (e) => formatMs(e.avg_ms) },
    { key: "p95", header: "p95", numeric: true, sortValue: (e) => e.p95_ms, render: (e) => formatMs(e.p95_ms) },
  ];
  const errorColumns: Column<ErrorGroup>[] = [
    { key: "what", header: "Error", render: (g) => <><strong>{g.exception_type ?? "Error status"}</strong><div className="pk-muted">{g.message ?? "No message"}</div></> },
    { key: "op", header: "Operation", render: (g) => g.operation },
    { key: "count", header: "Count", numeric: true, sortValue: (g) => g.count, render: (g) => g.count.toLocaleString("en-US") },
    { key: "last", header: "Last seen", sortValue: (g) => g.last_seen, render: (g) => <span className="pk-muted">{ago(g.last_seen)}</span> },
  ];
  const traceColumns: Column<TraceSummary>[] = [
    { key: "start", header: "Started", render: (t) => new Date(t.start).toLocaleString() },
    { key: "root", header: "Root", render: (t) => t.root_name ?? "—" },
    { key: "duration", header: "Duration", numeric: true, render: (t) => formatMs(t.duration_ms) },
    { key: "errors", header: "Errors", numeric: true, render: (t) => (t.errors ? <Pill tone="bad">{t.errors}</Pill> : <span className="pk-muted">0</span>) },
  ];
  const anyError = ts.isError || ops.isError || deps.isError || errs.isError;

  return (
    <PageShell
      title={service}
      icon={Gauge}
      crumbs={[{ label: "APM", href: apm.withRange("/apm") }, { label: service }]}
      subtitle="Request rate, errors and latency of this service's entry spans"
      actions={<ApmActions apm={apm} />}
    >
      {anyError && <Banner tone="bad">Part of this service could not be loaded.</Banner>}
      {ts.data && requests === 0 && <Banner tone="info">This service sent no requests (entry spans) in this window.</Banner>}
      <StatStrip>
        <StatTile label="Requests" value={ts.data ? requests.toLocaleString("en-US") : "—"} info="Entry spans in the window." loading={ts.isLoading} />
        <StatTile label="Per minute" value={ts.data ? perMinute.toLocaleString("en-US", { maximumFractionDigits: 1 }) : "—"} info="Average requests per minute over the window." loading={ts.isLoading} />
        <StatTile label="Error rate" value={ts.data ? (requests ? `${Math.round((errors / requests) * 1000) / 10} %` : "—") : "—"} tone={errors > 0 ? "bad" : "good"}
          info="Failed entry spans as a share of all entry spans." loading={ts.isLoading} />
        <StatTile label="p95" value={summary ? formatMs(summary.p95_ms) : "—"} info="95th percentile duration of entry spans over the whole window." loading={stats.isLoading} />
      </StatStrip>
      <div className="pk-grid">
        <Panel title="Requests" info="Entry spans per bucket." state={ts.isLoading ? "loading" : "ready"} className="pk-col-4">
          <LineChart label="Requests per bucket" points={points.map((p) => ({ t: Date.parse(p.t), value: p.requests }))} />
        </Panel>
        <Panel title="Error rate" info="Failed entry spans as a share of requests, per bucket (0 % when there were none)." state={ts.isLoading ? "loading" : "ready"} className="pk-col-4">
          <LineChart label="Error rate per bucket" points={points.map((p) => ({ t: Date.parse(p.t), value: p.error_rate_pct }))} format={(v) => `${v.toFixed(1)} %`} />
        </Panel>
        <Panel title="Latency" info="p95 line, with the p50 to p99 band. Buckets with no requests are gaps, not zeros." state={ts.isLoading ? "loading" : "ready"} className="pk-col-4">
          <LineChart label="Latency p95 with p50 to p99 band" points={latency} format={(v) => formatMs(v)} gapMs={bucketMs * 1.5} />
        </Panel>
        <Panel title="Operations" info="Every span name of this service, by total time spent (top 100)." state={ops.isLoading ? "loading" : "ready"} className="pk-col-12">
          <DataTable caption="Operations" columns={opColumns} rows={ops.data?.items ?? []} rowKey={(o) => o.name} emptyText="No spans from this service in the window." />
        </Panel>
        <Panel title="Called by" info="Services whose spans have a child span in this service." state={deps.isLoading ? "loading" : "ready"} className="pk-col-6">
          <DataTable caption="Callers" columns={edgeColumns("source")} rows={deps.data?.callers ?? []} rowKey={(e) => e.source} emptyText="No calling service seen." />
        </Panel>
        <Panel title="Calls" info="Services this one calls; latency is the callee's." state={deps.isLoading ? "loading" : "ready"} className="pk-col-6">
          <DataTable caption="Callees" columns={edgeColumns("target")} rows={deps.data?.callees ?? []} rowKey={(e) => e.target} emptyText="No downstream service seen." />
        </Panel>
        <Panel title="Errors" info="Failed spans grouped by exception." state={errs.isLoading ? "loading" : "ready"} className="pk-col-12"
          aside={<Link href={apm.withRange("/apm/errors")} className="ecc-link">All errors</Link>}>
          <DataTable caption="Errors" columns={errorColumns} rows={errs.data?.items ?? []} rowKey={(g) => `${g.operation}|${g.exception_type}|${g.message}`}
            rowHref={(g) => `/traces/${g.sample_trace_id}`} emptyText="No failed spans in this window." />
        </Panel>
        <Panel title="Recent failed traces" state={failed.isLoading ? "loading" : "ready"} className="pk-col-6">
          <DataTable caption="Failed traces" columns={traceColumns} rows={failed.data?.items ?? []} rowKey={(t) => t.trace_id} rowHref={(t) => `/traces/${t.trace_id}`} emptyText="No failed traces." />
        </Panel>
        <Panel title="Recent slow traces" info={slowFloor !== null ? `At least the service's p95 (${formatMs(slowFloor)}).` : "Shown once the service has a p95."}
          state={slow.isLoading ? "loading" : "ready"} className="pk-col-6">
          <DataTable caption="Slow traces" columns={traceColumns} rows={slow.data?.items ?? []} rowKey={(t) => t.trace_id} rowHref={(t) => `/traces/${t.trace_id}`} emptyText="No slow traces." />
        </Panel>
      </div>
    </PageShell>
  );
}
