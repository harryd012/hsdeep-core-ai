"use client";

import { useMemo, useState } from "react";
import { Activity, AlertOctagon, Bot, CheckCircle2, Clock, Cpu, ListChecks, Users, XCircle } from "lucide-react";
import {
  CCAgent,
  CCActivityEvent,
  CCMetrics,
  CCTask,
  CCWorkload,
  listMonitoringSources,
  listSourceTypes,
} from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { gatewayEntries, groupByCategory, type GatewayState } from "@/lib/gateway";
import { useCommandCenterOverview } from "@/hooks/useAiQueries";
import { ctoState } from "@/lib/health";
import CommandCenterTopology from "@/components/dashboard/CommandCenterTopology";
import {
  DataTable,
  PageShell,
  Panel,
  Pill,
  SearchBox,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  titleCaseWords,
  type Column,
  type Tone,
} from "@/components/ui/PageKit";

/**
 * /ai-copilot — AI Operations: the AI CTO's state, the agent workforce by
 * department, the tasks they are working and the live activity trail. All
 * data comes from the Command Center overview read (useCommandCenterOverview);
 * nothing here is estimated.
 */

function taskTone(status: string): Tone {
  if (["verified", "recorded", "completed"].includes(status)) return "good";
  if (["failed", "escalated", "cancelled"].includes(status)) return "bad";
  if (["investigating", "executing", "assigned", "accepted", "retrying", "reassigned", "verifying"].includes(status)) return "warn";
  if (status === "created") return "info";
  return "neutral";
}

function availabilityTone(availability: string): Tone {
  if (availability === "available") return "good";
  if (availability === "busy") return "warn";
  if (availability === "offline" || availability === "unavailable") return "neutral";
  return "info";
}

const CTO_TONE: Record<string, Tone> = { ok: "good", degraded: "warn", down: "bad", pending: "neutral" };

export default function AiCopilotPage() {
  const { data: overview, isLoading, isError, error, refetch } = useCommandCenterOverview();
  const [agentSearch, setAgentSearch] = useState("");
  const [department, setDepartment] = useState<string>("all");

  const o = overview;
  // Defensive: metrics defaults every field to 0 today, but a partial payload must not throw.
  const m: CCMetrics | null = o ? o.metrics ?? ({} as CCMetrics) : null;
  const cto = ctoState(o?.health?.status);
  const loading = isLoading && !o;
  const v = (x: number | undefined) => (m && x !== undefined ? x.toLocaleString("en-US") : "—");

  const departments = useMemo(() => (o ? [...o.workload].sort((a, b) => b.agent_count - a.agent_count) : []), [o]);
  const agents = useMemo(() => {
    const q = agentSearch.trim().toLowerCase();
    return (o?.agents ?? []).filter(
      (a) =>
        (department === "all" || a.department.toLowerCase() === department.toLowerCase()) &&
        (!q || [a.name, a.agent_id, a.role, a.department].some((x) => x.toLowerCase().includes(q))),
    );
  }, [o, agentSearch, department]);

  const deptColumns: Column<CCWorkload>[] = [
    {
      key: "dept",
      header: "Department",
      sortValue: (w) => w.department,
      render: (w) => {
        const lead = o?.agents.find((a) => a.department.toLowerCase() === w.department.toLowerCase() && a.level === "manager");
        return (
          <>
            <span className="pk-cell-main">{w.department.toUpperCase()}</span>
            <span className="pk-cell-sub">{lead ? `Lead: ${lead.name}` : "No manager assigned"}</span>
          </>
        );
      },
    },
    { key: "agents", header: "Agents", numeric: true, sortValue: (w) => w.agent_count, render: (w) => `${w.active_agents} / ${w.agent_count}` },
    { key: "active", header: "Active tasks", numeric: true, sortValue: (w) => w.active_tasks, render: (w) => w.active_tasks },
    { key: "queued", header: "Queued", numeric: true, sortValue: (w) => w.queued_tasks, render: (w) => w.queued_tasks },
    { key: "verifying", header: "Verifying", numeric: true, sortValue: (w) => w.verifying_tasks, render: (w) => w.verifying_tasks },
    { key: "done", header: "Completed", numeric: true, sortValue: (w) => w.completed_tasks, render: (w) => w.completed_tasks },
    {
      key: "critical",
      header: "Critical",
      numeric: true,
      sortValue: (w) => w.critical_tasks,
      render: (w) => (w.critical_tasks ? <Pill tone="bad">{w.critical_tasks}</Pill> : <span className="pk-muted">0</span>),
    },
  ];

  const agentColumns: Column<CCAgent>[] = [
    {
      key: "name",
      header: "Agent",
      sortValue: (a) => a.name.toLowerCase(),
      render: (a) => (
        <>
          <span className="pk-cell-main">{a.name}</span>
          <span className="pk-cell-sub">{a.agent_id} · {titleCaseWords(a.role)}</span>
        </>
      ),
    },
    { key: "dept", header: "Department", sortValue: (a) => a.department, render: (a) => a.department.toUpperCase() },
    { key: "level", header: "Level", sortValue: (a) => a.level, render: (a) => titleCaseWords(a.level) },
    { key: "avail", header: "Availability", sortValue: (a) => a.availability, render: (a) => <Pill tone={availabilityTone(a.availability)}>{titleCaseWords(a.availability)}</Pill> },
    {
      key: "load",
      header: "Workload",
      numeric: true,
      sortValue: (a) => (a.max_workload ? a.current_workload / a.max_workload : 0),
      render: (a) => `${a.current_workload} / ${a.max_workload}`,
    },
  ];

  const taskColumns: Column<CCTask>[] = [
    {
      key: "task",
      header: "Task",
      sortValue: (t) => t.task_number,
      render: (t) => (
        <>
          <span className="pk-cell-main" style={{ whiteSpace: "normal" }}>{t.title}</span>
          <span className="pk-cell-sub"><span className="pk-mono">{t.task_number}</span>{t.task_type ? ` · ${titleCaseWords(t.task_type)}` : ""}</span>
        </>
      ),
    },
    { key: "priority", header: "Priority", sortValue: (t) => t.priority, render: (t) => <Pill tone={t.priority === "P1" ? "bad" : t.priority === "P2" ? "warn" : "neutral"} dot={false}>{t.priority}</Pill> },
    { key: "status", header: "Status", sortValue: (t) => t.status, render: (t) => <Pill tone={taskTone(t.status)}>{titleCaseWords(t.status)}</Pill> },
    {
      key: "agent",
      header: "Assigned to",
      sortValue: (t) => t.assigned_agent_name ?? "",
      render: (t) =>
        t.assigned_agent_name ? (
          <>
            <span className="pk-cell-main">{t.assigned_agent_name}</span>
            {t.department && <span className="pk-cell-sub">{t.department.toUpperCase()}</span>}
          </>
        ) : (
          <span className="pk-muted">Unassigned</span>
        ),
    },
    { key: "created", header: "Created", sortValue: (t) => (t.created_at ? new Date(t.created_at).getTime() : null), render: (t) => <span className="pk-muted">{ago(t.created_at)}</span> },
  ];

  return (
    <PageShell
      title="AI Operations"
      icon={Bot}
      subtitle="The AI CTO, its agent workforce, and the tasks they are working — live from the AI control plane."
      meta={o ? `Updated ${ago(o.generated_at)}` : undefined}
      actions={
        o?.health ? (
          <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <Pill tone={CTO_TONE[cto.tone] ?? "neutral"}>AI CTO {titleCaseWords(cto.text.toLowerCase())}</Pill>
            {Object.entries(o.health.dependencies).map(([dep, state]) => (
              <Pill key={dep} tone={state === "up" ? "good" : "bad"} dot={false}>
                {titleCaseWords(dep)} {state}
              </Pill>
            ))}
          </span>
        ) : undefined
      }
    >
      {isError && !o && (
        <Panel title="AI control plane" state="error" errorText={`The AI control plane is unavailable: ${error?.message ?? "request failed"}`} onRetry={() => refetch()} />
      )}

      <StatStrip>
        <StatTile label="Agents active" value={m ? `${v(m.active_agents)} / ${v(m.total_agents)}` : "—"} icon={Users} tone="good" info="Active agents out of all registered agents." loading={loading} />
        <StatTile label="Working now" value={v(m?.working_agents)} icon={Cpu} info="Agents currently executing a task." loading={loading} />
        <StatTile label="Active tasks" value={v(m?.total_tasks)} icon={ListChecks} info="Tasks that are not yet completed or cancelled." loading={loading} />
        <StatTile label="Queued" value={v(m?.queued)} icon={Clock} tone="warn" info="Tasks waiting for an agent." loading={loading} />
        <StatTile label="Verifying" value={v(m?.verifying)} icon={Activity} info="Tasks whose result is being verified." loading={loading} />
        <StatTile label="Critical" value={v(m?.critical)} icon={AlertOctagon} alarm={!!m?.critical} info="Active P1 tasks." loading={loading} />
        <StatTile label="Completed" value={v(m?.completed)} icon={CheckCircle2} tone="good" info="Tasks completed and recorded." loading={loading} />
        <StatTile label="Failed" value={v(m?.failed)} icon={XCircle} alarm={!!m?.failed} info="Tasks that failed." loading={loading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel
          title="Departments"
          info="Each department's agents and task pipeline."
          aside={o ? `${departments.length} departments` : undefined}
          state={o ? (departments.length ? "ready" : "empty") : "loading"}
          emptyText="No AI agents registered yet."
          className="pk-col-7"
        >
          <DataTable caption="AI departments" columns={deptColumns} rows={departments} rowKey={(w) => w.department} />
        </Panel>

        <Panel
          title="Live activity"
          info="The most recent task state changes across all agents."
          state={o ? (o.activity.length ? "ready" : "empty") : "loading"}
          emptyText="No activity recorded yet."
          className="pk-col-5"
        >
          <ul className="pk-list" style={{ maxHeight: 320, overflowY: "auto" }}>
            {(o?.activity ?? []).map((e: CCActivityEvent, i) => (
              <li key={`${e.task_id}-${i}`}>
                <div className="pk-list__main">
                  <span className="pk-cell-main">
                    <span className="pk-mono">{e.task_number}</span> {e.from_status ? `${titleCaseWords(e.from_status)} → ` : ""}{titleCaseWords(e.to_status ?? e.action)}
                  </span>
                  <span className="pk-cell-sub">{e.message ?? (e.actor ? `by ${e.actor}` : "")}</span>
                </div>
                <div className="pk-list__aside">{ago(e.timestamp)}</div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="Active tasks"
          info="Tasks the agents are working, most recent first."
          aside={o ? `${o.tasks.length} tasks` : undefined}
          state={o ? (o.tasks.length ? "ready" : "empty") : "loading"}
          emptyText="No active AI tasks."
          className="pk-col-12"
        >
          <DataTable caption="Active AI tasks" columns={taskColumns} rows={o?.tasks ?? []} rowKey={(t) => t.id} defaultSort={{ key: "created", dir: "desc" }} maxHeight={420} />
        </Panel>

        <Panel
          title="Agent directory"
          info="Every registered agent, its department and current load."
          aside={o ? `${agents.length} of ${o.agents.length}` : undefined}
          state={o ? "ready" : "loading"}
          className="pk-col-8"
        >
          <div className="pk-toolbar" style={{ marginBottom: 10 }}>
            <SearchBox value={agentSearch} onChange={setAgentSearch} placeholder="Search agents, roles, departments" label="Search agents" />
            <Tabs
              label="Department"
              value={department}
              onChange={setDepartment}
              tabs={[{ key: "all", label: "All", count: o?.agents.length ?? 0 }, ...departments.map((w) => ({ key: w.department, label: w.department.toUpperCase(), count: w.agent_count }))]}
            />
          </div>
          <DataTable caption="Agent directory" columns={agentColumns} rows={agents} rowKey={(a) => a.id} defaultSort={{ key: "name", dir: "asc" }} maxHeight={420} emptyText="No agents match the current filter." />
        </Panel>

        <ToolGatewayPanel />

        <Panel title="Agent topology" info="AI CTO, managers and agents with their live tasks." state="ready" className="pk-col-12">
          <CommandCenterTopology />
        </Panel>
      </div>
    </PageShell>
  );
}

const GATEWAY_PILL: Record<GatewayState, { tone: Tone; label: string }> = {
  connected: { tone: "good", label: "Connected" },
  stale: { tone: "warn", label: "No recent data" },
  available: { tone: "neutral", label: "Available" },
};

/**
 * Collectors the backend registers, grouped by category and joined with this
 * tenant's sources. "Connected" means a successful poll in the last 24 h;
 * a configured source with no recent success says so instead.
 */
function ToolGatewayPanel() {
  const types = useQuery({ queryKey: ["source-types"], queryFn: listSourceTypes, staleTime: 5 * 60_000 });
  const sources = useQuery({ queryKey: ["monitoring-sources"], queryFn: () => listMonitoringSources(), refetchInterval: 60_000 });
  const groups = useMemo(
    () => (types.data ? groupByCategory(gatewayEntries(types.data, sources.data ?? [])) : []),
    [types.data, sources.data],
  );
  const entries = groups.flatMap((g) => g.entries);
  const connected = entries.filter((e) => e.state === "connected").length;
  const state = types.isError ? "error" : types.isLoading ? "loading" : entries.length ? "ready" : "empty";

  return (
    <Panel
      title="Tool gateway"
      info="Collectors the platform can use, from the backend registry. Connected means this tenant has a source of that type with a successful poll in the last 24 hours."
      aside={types.data ? `${connected} of ${entries.length} connected` : undefined}
      state={state}
      errorText="Could not load the collector registry."
      onRetry={() => types.refetch()}
      className="pk-col-4"
    >
      {sources.isError && <p className="pk-cell-sub" style={{ marginBottom: 8 }}>Source status unavailable: showing the registry only.</p>}
      {groups.map((g) => (
        <div key={g.category} style={{ marginBottom: 12 }}>
          <div className="pk-cell-sub" style={{ textTransform: "uppercase", letterSpacing: 0.4, margin: "4px 0" }}>{g.label}</div>
          <ul className="pk-list">
            {g.entries.map((e) => {
              const pill = sources.isError ? { tone: "neutral" as Tone, label: "Unknown" } : GATEWAY_PILL[e.state];
              return (
                <li key={e.source_type}>
                  <div className="pk-list__main">
                    <span className="pk-cell-main">{e.display_name}</span>
                    {e.lastSuccessAt && <span className="pk-cell-sub"> · last data {ago(e.lastSuccessAt)}</span>}
                  </div>
                  <div className="pk-list__aside">
                    <Pill tone={pill.tone}>{pill.label}</Pill>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </Panel>
  );
}
