"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpenCheck } from "lucide-react";
import { listRunbookHistory, listRunbooks, listSops, type RunbookExecution, type SOP } from "@/lib/api";
import { DonutRing, Legend } from "@/components/command-center/primitives";
import {
  BarList,
  DataTable,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  ago,
  titleCaseWords,
  type Column,
  type Tone,
} from "@/components/ui/PageKit";

const EXEC_STATUS: { key: RunbookExecution["status"]; label: string; color: string; tone: Tone }[] = [
  { key: "succeeded", label: "Succeeded", color: "var(--ecc-good)", tone: "good" },
  { key: "running", label: "Running", color: "var(--ecc-accent)", tone: "info" },
  { key: "pending_approval", label: "Awaiting approval", color: "var(--ecc-warn)", tone: "warn" },
  { key: "approved", label: "Approved", color: "var(--ecc-accent)", tone: "info" },
  { key: "failed", label: "Failed", color: "var(--ecc-bad)", tone: "bad" },
  { key: "rolled_back", label: "Rolled back", color: "var(--ecc-down)", tone: "bad" },
  { key: "rejected", label: "Rejected", color: "var(--ecc-unknown)", tone: "neutral" },
];
const statusMeta = (s: RunbookExecution["status"]) => EXEC_STATUS.find((x) => x.key === s) ?? EXEC_STATUS[0];

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export default function SOPExecutionLogPage() {
  const sops = useQuery({ queryKey: ["reports", "sops"], queryFn: () => listSops({ limit: 500 }), refetchInterval: 120_000 });
  const runbooks = useQuery({ queryKey: ["reports", "runbooks"], queryFn: () => listRunbooks(500), refetchInterval: 120_000 });
  const history = useQuery({ queryKey: ["reports", "runbook-history"], queryFn: () => listRunbookHistory(500), refetchInterval: 60_000 });

  const sopList = useMemo(() => sops.data ?? [], [sops.data]);
  const execs = useMemo(() => history.data ?? [], [history.data]);
  const runbookName = useMemo(() => new Map((runbooks.data ?? []).map((r) => [r.id, r.name])), [runbooks.data]);

  const approvedSops = sopList.filter((s) => s.current_version_id).length;
  const activeSops = sopList.filter((s) => s.is_active).length;
  const finished = execs.filter((e) => e.status === "succeeded" || e.status === "failed" || e.status === "rolled_back");
  const succeeded = execs.filter((e) => e.status === "succeeded").length;
  const successRate = finished.length ? `${Math.round((succeeded / finished.length) * 100)}%` : "—";
  const durations = execs.map((e) => e.duration_seconds).filter((d): d is number => d !== null).sort((a, b) => a - b);
  const medianDuration = durations.length ? duration(durations[Math.floor(durations.length / 2)]) : "—";
  const awaiting = execs.filter((e) => e.status === "pending_approval").length;

  const statusCounts = EXEC_STATUS.map((s) => ({ ...s, value: execs.filter((e) => e.status === s.key).length })).filter((s) => s.value > 0);
  const byCategory = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sopList) m.set(s.category, (m.get(s.category) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [sopList]);
  const topRunbooks = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of execs) m.set(e.runbook_id, (m.get(e.runbook_id) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [execs]);

  const execState = history.isLoading ? "loading" : history.error ? "error" : execs.length === 0 ? "empty" : "ready";
  const sopState = sops.isLoading ? "loading" : sops.error ? "error" : sopList.length === 0 ? "empty" : "ready";

  const execColumns: Column<RunbookExecution>[] = [
    { key: "when", header: "Requested", sortValue: (e) => e.created_at, render: (e) => <span className="pk-muted" title={e.created_at}>{ago(e.created_at)}</span> },
    { key: "runbook", header: "Runbook", sortValue: (e) => runbookName.get(e.runbook_id) ?? e.runbook_id, render: (e) => <span className="pk-cell-main">{runbookName.get(e.runbook_id) ?? e.runbook_id.slice(0, 8)}</span> },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={statusMeta(e.status).tone}>{statusMeta(e.status).label}</Pill> },
    { key: "incident", header: "Incident", render: (e) => (e.incident_id ? <a href={`/incidents/${e.incident_id}`}>{e.incident_id.slice(0, 8)}</a> : <span className="pk-muted">—</span>) },
    { key: "approved", header: "Approved", sortValue: (e) => e.approved_at, render: (e) => <span className="pk-muted">{e.approved_at ? ago(e.approved_at) : "—"}</span> },
    { key: "duration", header: "Duration", numeric: true, sortValue: (e) => e.duration_seconds, render: (e) => duration(e.duration_seconds) },
    { key: "notes", header: "Result", render: (e) => <span className="pk-truncate" style={{ display: "block", maxWidth: 360 }} title={e.result_notes ?? e.rejection_reason ?? undefined}>{e.result_notes ?? e.rejection_reason ?? (e.rollback_performed ? "Rolled back" : "—")}</span> },
  ];

  const sopColumns: Column<SOP>[] = [
    { key: "number", header: "SOP", sortValue: (s) => s.sop_number, render: (s) => <span className="pk-mono">{s.sop_number}</span> },
    {
      key: "name",
      header: "Name",
      sortValue: (s) => s.name,
      render: (s) => (
        <>
          <span className="pk-cell-main">{s.name}</span>
          {s.tags.length > 0 && <span className="pk-cell-sub pk-truncate">{s.tags.slice(0, 6).join(" · ")}</span>}
        </>
      ),
    },
    { key: "category", header: "Category", sortValue: (s) => s.category, render: (s) => titleCaseWords(s.category) },
    { key: "vendor", header: "Vendor / platform", sortValue: (s) => s.vendor, render: (s) => [s.vendor, s.platform].filter(Boolean).join(" · ") || <span className="pk-muted">Any</span> },
    { key: "version", header: "Approved version", sortValue: (s) => (s.current_version_id ? 1 : 0), render: (s) => (s.current_version_id ? <Pill tone="good">Approved</Pill> : <Pill tone="warn">Draft only</Pill>) },
    { key: "approval", header: "Needs approval", sortValue: (s) => (s.requires_approval ? 1 : 0), render: (s) => (s.requires_approval ? "Yes" : "No") },
    { key: "eta", header: "Est. time", numeric: true, sortValue: (s) => s.estimated_resolution_minutes, render: (s) => (s.estimated_resolution_minutes ? `${s.estimated_resolution_minutes} min` : "—") },
    { key: "updated", header: "Updated", sortValue: (s) => s.updated_at, render: (s) => <span className="pk-muted">{ago(s.updated_at)}</span> },
  ];

  return (
    <PageShell
      title="SOP Execution Log"
      icon={BookOpenCheck}
      crumbs={[{ label: "Reports", href: "/reports" }, { label: "SOP Execution Log" }]}
      subtitle="Every runbook run against an incident, its approval and outcome, and the SOP catalogue the runbooks are built from."
    >
      <StatStrip>
        <StatTile label="SOPs" value={sops.data ? String(sopList.length) : "—"} sub={sops.data ? `${activeSops} active` : undefined} info="Standard operating procedures in the catalogue." href="/sop-engine" loading={sops.isLoading} />
        <StatTile label="Approved SOPs" value={sops.data ? String(approvedSops) : "—"} tone="good" sub={sopList.length ? `${Math.round((approvedSops / sopList.length) * 100)}% of SOPs` : undefined} info="SOPs with an approved current version, so they can be used for runbooks." loading={sops.isLoading} />
        <StatTile label="Runbooks" value={runbooks.data ? String(runbooks.data.length) : "—"} info="Executable runbooks built from approved SOP versions." loading={runbooks.isLoading} />
        <StatTile label="Executions" value={history.data ? String(execs.length) : "—"} info="Runbook executions recorded for this tenant." loading={history.isLoading} />
        <StatTile label="Success rate" value={successRate} sub={finished.length ? `${succeeded} of ${finished.length} finished` : undefined} info="Succeeded over all finished executions (failed and rolled back count against it)." loading={history.isLoading} />
        <StatTile label="Median duration" value={medianDuration} info="Median wall time of executions that recorded a duration." loading={history.isLoading} />
        <StatTile label="Awaiting approval" value={history.data ? String(awaiting) : "—"} alarm={awaiting > 0} info="Executions blocked until an approver signs off." loading={history.isLoading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel title="Execution outcomes" info="Runbook executions by status." state={execState} className="pk-col-4" emptyText="No runbook has been executed yet. Executions appear here once a runbook is run against an incident.">
          <div className="pk-donut-row">
            <DonutRing size={132} label="Executions by status" centerValue={String(execs.length)} centerLabel="runs" segments={statusCounts.map((s) => ({ key: s.key, value: s.value, color: s.color }))} />
            <Legend total={execs.length} rows={statusCounts.map((s) => ({ key: s.key, label: s.label, value: s.value, color: s.color }))} />
          </div>
        </Panel>
        <Panel title="Most used runbooks" info="Runbooks by number of executions." state={execState} className="pk-col-4" emptyText="No executions yet.">
          <BarList rows={topRunbooks.map(([id, v]) => ({ key: id, label: runbookName.get(id) ?? id.slice(0, 8), value: v }))} />
        </Panel>
        <Panel title="SOPs by category" info="Coverage of the SOP catalogue by problem category." state={sopState} className="pk-col-4" emptyText="No SOPs in the catalogue.">
          <BarList rows={byCategory.map(([k, v]) => ({ key: k, label: titleCaseWords(k), value: v, color: "var(--ecc-good)", href: "/sop-engine" }))} />
        </Panel>

        <Panel title="Execution log" info="Every runbook execution, newest first." aside={`${execs.length} executions`} state={execState} className="pk-col-12" emptyText="No runbook has been executed yet.">
          <DataTable caption="Runbook executions" columns={execColumns} rows={execs} rowKey={(e) => e.id} defaultSort={{ key: "when", dir: "desc" }} maxHeight={420} />
        </Panel>
        <Panel title="SOP catalogue" info="Every SOP, whether it has an approved version and what it covers." aside={`${sopList.length} SOPs`} state={sopState} className="pk-col-12">
          <DataTable caption="SOP catalogue" columns={sopColumns} rows={sopList} rowKey={(s) => s.id} rowHref={() => "/sop-engine"} defaultSort={{ key: "number", dir: "asc" }} maxHeight={520} />
        </Panel>
      </div>
    </PageShell>
  );
}
