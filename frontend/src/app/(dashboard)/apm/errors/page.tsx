"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

import { ApmActions, useApmRange } from "@/components/apm/ApmRange";
import { getTraceErrors, servicePath, type ErrorGroup } from "@/lib/traces";
import { Banner, DataTable, PageShell, Panel, StatStrip, StatTile, ago, type Column } from "@/components/ui/PageKit";

/**
 * /apm/errors — failed spans across every service, grouped by service,
 * operation and exception (OpenTelemetry's `exception` event, else the span's
 * status message), most frequent first. A row opens its latest trace.
 */

export default function ApmErrorsPage() {
  const apm = useApmRange();
  const q = useQuery({ queryKey: ["apm", "errors", null, apm.window], queryFn: () => getTraceErrors(apm.window) });
  const groups = q.data?.items ?? [];
  const total = groups.reduce((n, g) => n + g.count, 0);
  const services = new Set(groups.map((g) => g.service)).size;

  const columns: Column<ErrorGroup>[] = [
    { key: "what", header: "Error", sortValue: (g) => g.exception_type ?? "",
      render: (g) => <><strong>{g.exception_type ?? "Error status"}</strong><div className="pk-muted">{g.message ?? "No message"}</div></> },
    { key: "service", header: "Service", sortValue: (g) => g.service,
      render: (g) => (
        <Link href={apm.withRange(`/apm/services/${servicePath(g.service)}`)} onClick={(e) => e.stopPropagation()}>{g.service}</Link>
      ) },
    { key: "op", header: "Operation", sortValue: (g) => g.operation, render: (g) => g.operation },
    { key: "count", header: "Count", numeric: true, sortValue: (g) => g.count, render: (g) => g.count.toLocaleString("en-US") },
    { key: "traces", header: "Traces", numeric: true, sortValue: (g) => g.traces, render: (g) => g.traces.toLocaleString("en-US") },
    { key: "first", header: "First seen", sortValue: (g) => g.first_seen, render: (g) => <span className="pk-muted">{ago(g.first_seen)}</span> },
    { key: "last", header: "Last seen", sortValue: (g) => g.last_seen, render: (g) => <span className="pk-muted">{ago(g.last_seen)}</span> },
  ];

  return (
    <PageShell
      title="Errors"
      icon={Gauge}
      crumbs={[{ label: "APM", href: apm.withRange("/apm") }, { label: "Errors" }]}
      subtitle="Failed spans grouped by exception, across every service"
      actions={<ApmActions apm={apm} />}
    >
      {q.isError && <Banner tone="bad">Errors could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      <StatStrip>
        <StatTile label="Error groups" value={q.data ? String(groups.length) : "—"} info="Distinct service, operation and exception combinations (top 100)." loading={q.isLoading} />
        <StatTile label="Failed spans" value={q.data ? total.toLocaleString("en-US") : "—"} tone={total > 0 ? "bad" : "good"} info="Failed spans in the listed groups." loading={q.isLoading} />
        <StatTile label="Services affected" value={q.data ? String(services) : "—"} info="Services with at least one listed group." loading={q.isLoading} />
      </StatStrip>
      <Panel title="Error groups" info="Most frequent first. Select a row to open its most recent trace." state={q.isLoading ? "loading" : "ready"}>
        <DataTable caption="Error groups" columns={columns} rows={groups} rowKey={(g) => `${g.service}|${g.operation}|${g.exception_type}|${g.message}`}
          rowHref={(g) => `/traces/${g.sample_trace_id}`} emptyText="No failed spans in this window." maxHeight={640} />
      </Panel>
    </PageShell>
  );
}
