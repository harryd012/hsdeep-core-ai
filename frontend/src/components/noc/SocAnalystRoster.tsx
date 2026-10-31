"use client";

import { useSocAnalysts, useSocAnalystSummary } from "@/hooks/useNocQueries";
import type { SocAnalyst } from "@/lib/noc";
import { DataTable, Panel, Pill, StatStrip, StatTile, ago, statusTone, titleCaseWords, type Column } from "@/components/ui/PageKit";

/** Success rate only means something once an analyst has finished a task;
 *  the backend reports 0 for "no tasks", which would read as "always fails". */
export function analystSuccess(a: Pick<SocAnalyst, "tasks_completed" | "tasks_failed" | "success_rate">): string {
  return a.tasks_completed + a.tasks_failed === 0 ? "—" : `${Math.round(a.success_rate)}%`;
}

export const ANALYST_COLUMNS: Column<SocAnalyst>[] = [
  {
    key: "name",
    header: "Analyst",
    sortValue: (a) => a.name.toLowerCase(),
    render: (a) => (
      <>
        <span className="pk-cell-main">{a.name}</span>
        <span className="pk-cell-sub">{a.agent_id} · {titleCaseWords(a.level)}</span>
      </>
    ),
  },
  { key: "availability", header: "Availability", sortValue: (a) => a.availability, render: (a) => <Pill tone={statusTone(a.availability)}>{titleCaseWords(a.availability)}</Pill> },
  {
    key: "workload",
    header: "Workload",
    numeric: true,
    sortValue: (a) => (a.max_workload ? a.current_workload / a.max_workload : 0),
    render: (a) => `${a.current_workload} / ${a.max_workload}`,
  },
  { key: "active", header: "Active events", numeric: true, sortValue: (a) => a.open_noc_events + a.acknowledged_noc_events, render: (a) => a.open_noc_events + a.acknowledged_noc_events },
  { key: "resolved", header: "Resolved today", numeric: true, sortValue: (a) => a.resolved_today, render: (a) => a.resolved_today },
  { key: "total", header: "Assigned (all time)", numeric: true, sortValue: (a) => a.total_assigned_events, render: (a) => a.total_assigned_events },
  {
    key: "success",
    header: "Success rate",
    numeric: true,
    sortValue: (a) => (a.tasks_completed + a.tasks_failed ? a.success_rate : null),
    render: (a) => <span title={`${a.tasks_completed} completed · ${a.tasks_failed} failed`}>{analystSuccess(a)}</span>,
  },
  { key: "last", header: "Last assigned", sortValue: (a) => (a.last_assigned_at ? new Date(a.last_assigned_at).getTime() : null), render: (a) => <span className="pk-muted">{a.last_assigned_at ? ago(a.last_assigned_at) : "Never"}</span> },
];

/** SOC analyst roster: summary strip + sortable roster table (polled every 5 s). */
export default function SocAnalystRoster() {
  const analysts = useSocAnalysts();
  const summary = useSocAnalystSummary();
  const s = summary.data;
  const items = analysts.data ?? [];
  const v = (x: number | undefined) => (s && x !== undefined ? x.toLocaleString("en-US") : "—");
  const anySample = items.some((a) => a.tasks_completed + a.tasks_failed > 0);

  return (
    <>
      <StatStrip>
        <StatTile label="Analysts" value={v(s?.total_analysts)} info="SOC analysts registered for this tenant." loading={summary.isLoading} />
        <StatTile label="Available" value={v(s?.available_analysts)} info="Analysts available to take new events." loading={summary.isLoading} />
        <StatTile label="Busy" value={v(s?.busy_analysts)} info="Analysts currently working events." loading={summary.isLoading} />
        <StatTile label="Offline" value={v(s?.offline_analysts)} info="Analysts not on shift." loading={summary.isLoading} />
        <StatTile label="Unassigned events" value={v(s?.unassigned_open_events)} alarm={!!s?.unassigned_open_events} info="Open events with no analyst assigned." loading={summary.isLoading} />
        <StatTile
          label="Avg success rate"
          value={s && anySample ? `${s.avg_success_rate}%` : "—"}
          info="Average task success rate across analysts. Not measured until an analyst completes or fails a task."
          loading={summary.isLoading}
        />
      </StatStrip>

      <Panel
        title="Roster"
        info="Every SOC analyst with their live workload. Select a row for the analyst's assigned events."
        aside={analysts.data ? `${items.length} analysts · refreshes every 5 s` : undefined}
        state={analysts.data ? "ready" : analysts.isError ? "error" : "loading"}
        errorText="The SOC roster could not be loaded."
        onRetry={() => analysts.refetch()}
        skeletonHeight={240}
      >
        <DataTable
          caption="SOC analyst roster"
          columns={ANALYST_COLUMNS}
          rows={items}
          rowKey={(a) => a.id}
          rowHref={(a) => `/soc-analyst-roster/${a.id}`}
          emptyText="No SOC analysts registered."
          defaultSort={{ key: "active", dir: "desc" }}
        />
      </Panel>
    </>
  );
}
