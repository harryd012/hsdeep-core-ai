"use client";

import Link from "next/link";
import { AlertTriangle, Users } from "lucide-react";
import { useSocAnalysts, useSocAnalystSummary, useNocEventSummary } from "@/hooks/useNocQueries";
import SocEventsPanel from "@/components/soc/SocEventsPanel";
import { ANALYST_COLUMNS } from "@/components/noc/SocAnalystRoster";
import { formatMinutes } from "@/lib/commandCenter/format";
import { DataTable, PageShell, Panel, StatStrip, StatTile } from "@/components/ui/PageKit";

/**
 * SOC Analysis — the /soc-analysis landing page: event and team KPIs, the
 * team's live allocation, and the open-event analysis queue. All data is real
 * backend state polled every 5 s via React Query. Full roster → /soc-analyst-roster
 */
export default function SocAnalysisPage() {
  const analysts = useSocAnalysts({ limit: 50 });
  const { data: analystSummary, isLoading: summaryLoading } = useSocAnalystSummary();
  const { data: events, isLoading: eventsLoading } = useNocEventSummary();
  const v = (x: number | undefined | null) => (x === undefined || x === null ? "—" : x.toLocaleString("en-US"));
  const allocation = ANALYST_COLUMNS.filter((c) => ["name", "availability", "workload", "active", "resolved"].includes(c.key));

  return (
    <PageShell
      title="SOC Analysis"
      subtitle="Security events awaiting analysis and the team working them."
      actions={
        <>
          <Link className="pk-button" href="/soc-analyst-roster"><Users size={14} /> Roster</Link>
          <Link className="pk-button" href="/alerts"><AlertTriangle size={14} /> Critical alerts</Link>
        </>
      }
    >
      <StatStrip>
        <StatTile label="Open events" value={v(events?.by_status.open)} info="Security events not yet acknowledged." loading={eventsLoading} />
        <StatTile label="Critical" value={v(events?.by_severity.critical)} alarm={!!events?.by_severity.critical} info="Critical security events (all statuses)." loading={eventsLoading} />
        <StatTile label="Unassigned" value={v(events?.unassigned_open)} alarm={!!events?.unassigned_open} info="Open events with no analyst assigned." loading={eventsLoading} />
        <StatTile label="Avg resolution" value={events ? formatMinutes(events.avg_resolution_minutes) : "—"} info="Mean time to resolve security events. Not measured until an event is resolved." loading={eventsLoading} />
        <StatTile
          label="Analysts available"
          value={analystSummary ? `${analystSummary.available_analysts} / ${analystSummary.total_analysts}` : "—"}
          info="Analysts available to take new events, out of all analysts."
          href="/soc-analyst-roster"
          loading={summaryLoading}
        />
      </StatStrip>

      <div className="pk-grid">
        <Panel
          title="Team allocation"
          info="Each analyst's live workload."
          aside={<Link className="ecc-link" href="/soc-analyst-roster">Full roster →</Link>}
          state={analysts.data ? "ready" : analysts.isError ? "error" : "loading"}
          errorText="SOC analysts could not be loaded."
          onRetry={() => analysts.refetch()}
          className="pk-col-6"
          skeletonHeight={220}
        >
          <DataTable
            caption="SOC team allocation"
            columns={allocation}
            rows={analysts.data ?? []}
            rowKey={(a) => a.id}
            rowHref={(a) => `/soc-analyst-roster/${a.id}`}
            emptyText="No SOC analysts registered."
            defaultSort={{ key: "active", dir: "desc" }}
            maxHeight={420}
          />
        </Panel>

        <Panel title="Analysis queue" info="Open security events, most urgent first." state="ready" className="pk-col-6">
          <SocEventsPanel summary={events} />
        </Panel>
      </div>
    </PageShell>
  );
}
