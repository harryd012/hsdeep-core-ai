"use client";

import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

import { ApmActions, useApmRange } from "@/components/apm/ApmRange";
import ServiceMap from "@/components/apm/ServiceMap";
import ServiceTrend from "@/components/apm/ServiceTrend";
import { formatMs, getServiceMap, servicePath, type ServiceStats } from "@/lib/traces";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, ago, type Column } from "@/components/ui/PageKit";

/**
 * /apm — services overview: every service that sent spans in the window, its
 * request trend, error rate and entry-span latency, and the service map.
 * Selecting a service opens its page.
 */

const TREND_ROWS = 25;

export default function ApmServicesPage() {
  const apm = useApmRange();
  const router = useRouter();
  const map = useQuery({ queryKey: ["traces", "map", apm.window], queryFn: () => getServiceMap(apm.window) });
  const services = map.data?.services ?? [];
  const trendFor = new Set(services.slice(0, TREND_ROWS).map((s) => s.service));
  const open = (s: string) => router.push(apm.withRange(`/apm/services/${servicePath(s)}`));
  const requests = services.reduce((n, s) => n + s.entry_spans, 0);
  const errors = services.reduce((n, s) => n + s.error_spans, 0);
  const failing = services.filter((s) => s.error_spans > 0).length;

  const columns: Column<ServiceStats>[] = [
    { key: "service", header: "Service", sortValue: (s) => s.service, render: (s) => <strong>{s.service}</strong> },
    { key: "trend", header: "Requests", render: (s) => (trendFor.has(s.service) ? <ServiceTrend service={s.service} window={apm.window} /> : <span className="pk-muted">—</span>) },
    { key: "entry", header: "Entry spans", numeric: true, sortValue: (s) => s.entry_spans, render: (s) => s.entry_spans.toLocaleString("en-US") },
    { key: "errors", header: "Error rate", numeric: true, sortValue: (s) => s.error_rate_pct,
      render: (s) => (s.error_spans ? <Pill tone="bad">{s.error_rate_pct} %</Pill> : <span className="pk-muted">0 %</span>) },
    { key: "p50", header: "p50", numeric: true, sortValue: (s) => s.p50_ms, render: (s) => formatMs(s.p50_ms) },
    { key: "p95", header: "p95", numeric: true, sortValue: (s) => s.p95_ms, render: (s) => formatMs(s.p95_ms) },
    { key: "seen", header: "Last span", sortValue: (s) => s.last_seen, render: (s) => <span className="pk-muted">{ago(s.last_seen)}</span> },
  ];

  return (
    <PageShell
      title="APM"
      icon={Gauge}
      crumbs={[{ label: "APM" }]}
      subtitle="Application performance from OpenTelemetry traces: request rate, errors and latency per service"
      actions={<ApmActions apm={apm} />}
    >
      {map.isError && <Banner tone="bad">Services could not be loaded{map.error?.message ? `: ${map.error.message}` : "."}</Banner>}
      {map.data && services.length === 0 && (
        <Banner tone="info">
          No spans in this window. Point an OpenTelemetry SDK or collector at <code>{"<platform>/api/v1/ingest/otlp"}</code> with
          protocol <code>http/json</code> and the header <code>X-HSDEEP-Collector-Credential</code>.
        </Banner>
      )}
      <StatStrip>
        <StatTile label="Services" value={map.data ? String(services.length) : "—"} info="Services that sent spans in this window." loading={map.isLoading} />
        <StatTile label="Requests" value={map.data ? requests.toLocaleString("en-US") : "—"} info="Entry spans: server or consumer spans, or spans without a parent." loading={map.isLoading} />
        <StatTile label="Error spans" value={map.data ? errors.toLocaleString("en-US") : "—"} tone={errors > 0 ? "bad" : "good"} info="Spans whose status is error." loading={map.isLoading} />
        <StatTile label="Services with errors" value={map.data ? String(failing) : "—"} tone={failing > 0 ? "bad" : "good"} info="Services with at least one failed span in this window." loading={map.isLoading} />
      </StatStrip>
      <div className="pk-grid">
        <Panel title="Services" info={`Latency is over each service's entry spans. The request trend is shown for the first ${TREND_ROWS} services.`}
          state={map.isLoading ? "loading" : "ready"} className="pk-col-12">
          <DataTable caption="Services" columns={columns} rows={services} rowKey={(s) => s.service} onRowClick={(s) => open(s.service)} emptyText="No services in this window." />
        </Panel>
        <Panel title="Service map" info="Callers on the left, callees to the right; a red edge or box had errors. Select a service to open it."
          state={map.isLoading ? "loading" : "ready"} className="pk-col-12">
          {services.length > 0 ? <ServiceMap services={services} edges={map.data?.edges ?? []} onPick={open} /> : <p className="pk-muted pk-empty-note">No services in this window.</p>}
        </Panel>
      </div>
    </PageShell>
  );
}
