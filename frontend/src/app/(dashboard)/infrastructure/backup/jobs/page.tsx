"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import { getBackupJob, getBackupOverview, listBackupJobs, type BackupFilters, type BackupJob } from "@/lib/api";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, Pill, SearchBox, StatStrip, StatTile, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { PanelHeading, Props, SidePanel, duration, jobTone } from "../ui";

/**
 * /infrastructure/backup/jobs — Job Controller: every backup, restore and
 * other provider job in the chosen window (default 7 days, up to the 90 days
 * of job history kept), filtered on the server, with a details drawer. All
 * filters live in the URL. Read-only: retry and cancel come with the write phase.
 */

const SERVER_KEYS = ["provider", "subscription_id", "resource_group", "location", "storage_id", "workload_type"] as const;
const RANGES = [1, 7, 30, 90];
const LIMIT = 1000;
const STATUSES = ["completed", "completed_with_warnings", "failed", "in_progress", "cancelled", "other"];

const columns: Column<BackupJob>[] = [
  { key: "started", header: "Started", sortValue: (j) => j.started_at, render: (j) => <span title={j.started_at ?? undefined}>{j.started_at ? new Date(j.started_at).toLocaleString() : "—"}</span> },
  { key: "operation", header: "Operation", sortValue: (j) => j.operation, render: (j) => j.operation ?? <span className="pk-muted">—</span> },
  { key: "item", header: "Resource", sortValue: (j) => j.item_name, render: (j) => <><span className="pk-cell-main">{j.item_name ?? "—"}</span><span className="pk-cell-sub">{j.vault_name}</span></> },
  { key: "status", header: "Status", sortValue: (j) => j.status, render: (j) => <Pill tone={jobTone(j.status)}>{titleCaseWords(j.status)}</Pill> },
  { key: "duration", header: "Duration", numeric: true, sortValue: (j) => j.duration_seconds, render: (j) => duration(j.duration_seconds) },
  {
    key: "error", header: "Error",
    sortValue: (j) => j.error_code,
    render: (j) => (j.error_code || j.error_message
      ? <span className="pk-truncate" style={{ display: "block", maxWidth: 380, color: "var(--ecc-bad)" }} title={j.error_message ?? undefined}>{[j.error_code, j.error_message].filter(Boolean).join(" · ")}</span>
      : <span className="pk-muted">—</span>),
  },
];

const CSV: CsvColumn<BackupJob>[] = [
  { header: "Job ID", value: (j) => j.id },
  { header: "Started (UTC)", value: (j) => j.started_at },
  { header: "Ended (UTC)", value: (j) => j.ended_at },
  { header: "Operation", value: (j) => j.operation },
  { header: "Resource", value: (j) => j.item_name },
  { header: "Storage", value: (j) => j.vault_name },
  { header: "Status", value: (j) => j.status },
  { header: "Duration (s)", value: (j) => j.duration_seconds },
  { header: "Error code", value: (j) => j.error_code },
  { header: "Error message", value: (j) => j.error_message },
];

function JobDrawer({ job, onClose }: { job: BackupJob; onClose: () => void }) {
  const detail = useQuery({ queryKey: ["backup", "job", job.id], queryFn: () => getBackupJob(job.id), retry: false });
  const d = detail.data;
  return (
    <SidePanel title={job.item_name ?? "Backup job"} subtitle={[job.operation, job.vault_name].filter(Boolean).join(" · ")} onClose={onClose}>
      <p style={{ margin: "12px 0" }}><Pill tone={jobTone(job.status)}>{titleCaseWords(job.status)}</Pill></p>
      {detail.isError && <Banner tone="warn">Job details could not be loaded.</Banner>}
      <PanelHeading>Timeline</PanelHeading>
      <Props rows={[
        ["Started", job.started_at ? new Date(job.started_at).toLocaleString() : null],
        ["Ended", job.ended_at ? new Date(job.ended_at).toLocaleString() : job.status === "in_progress" ? "Still running" : null],
        ["Duration", job.duration_seconds !== null ? duration(job.duration_seconds) : null],
      ]} />
      {(job.error_code || job.error_message) && (
        <>
          <PanelHeading>Error</PanelHeading>
          <Props rows={[["Error code", job.error_code ? <code>{job.error_code}</code> : null], ["Message", job.error_message]]} />
        </>
      )}
      <PanelHeading>Job</PanelHeading>
      <Props rows={[
        ["Provider", d ? titleCaseWords(d.provider) : null],
        ["Storage", <Link key="v" className="ecc-link" href={`/infrastructure/backup/vaults/${job.vault_id}`}>{job.vault_name}</Link>],
        ["Job ID", <code key="id" style={{ wordBreak: "break-all" }}>{job.id}</code>],
      ]} />
      {d && d.item_jobs.length > 0 && (
        <>
          <PanelHeading>This resource&apos;s recent jobs</PanelHeading>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
            {d.item_jobs.map((j) => (
              <li key={j.id} style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
                <span className="pk-muted">{j.started_at ? new Date(j.started_at).toLocaleString() : "—"} · {j.operation ?? "—"}</span>
                <Pill tone={jobTone(j.status)}>{titleCaseWords(j.status)}</Pill>
              </li>
            ))}
          </ul>
        </>
      )}
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
        {d?.item_id && <Link className="pk-button pk-button--primary" href={`/infrastructure/backup/items/${d.item_id}`}>View backup instance</Link>}
        <Link className="pk-button" href={`/infrastructure/backup/vaults/${job.vault_id}`}>View storage</Link>
      </p>
      <p className="pk-help">Retry and cancel are not available yet: this platform reads backups with read-only roles.</p>
    </SidePanel>
  );
}

function JobsContent() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const get = (k: string) => params.get(k) ?? "";
  const setParam = (updates: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const q = next.toString();
    router.replace(`${pathname}${q ? `?${q}` : ""}`, { scroll: false });
  };
  const filters: BackupFilters = useMemo(() => Object.fromEntries(SERVER_KEYS.map((k) => [k, params.get(k) || undefined])), [params]);
  const days = RANGES.includes(Number(get("days"))) ? Number(get("days")) : 7;
  const status = get("status"), operation = get("operation"), q = get("q");

  const overview = useQuery({ queryKey: ["backup", "overview", filters, 14], queryFn: () => getBackupOverview(filters), refetchInterval: 120_000 });
  const configured = !!overview.data?.configured;
  const jobs = useQuery({
    queryKey: ["backup", "jobs", filters, days, status, operation, q],
    queryFn: () => listBackupJobs({ days, status: status || undefined, operation: operation || undefined, q: q || undefined, limit: LIMIT }, filters),
    enabled: configured,
    refetchInterval: 60_000,
  });
  const [search, setSearch] = useState(q);
  const [selected, setSelected] = useState<BackupJob | null>(null);
  useEffect(() => {
    const t = setTimeout(() => { if (search !== get("q")) setParam({ q: search }); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const rows = jobs.data ?? [];
  const count = (s: string) => rows.filter((j) => j.status === s).length;
  const operations = [...new Set(rows.map((j) => j.operation).filter((o): o is string => !!o)), ...(operation ? [operation] : [])];
  const fo = overview.data?.filter_options;
  const anyFilter = ["status", "operation", "q", ...SERVER_KEYS].some((k) => params.get(k));

  return (
    <PageShell
      title="Backup Jobs"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center", href: "/infrastructure/backup" }, { label: "Backup Jobs" }]}
      subtitle="Every job the backup providers reported: backups, restores and configuration jobs, with their outcome, duration and error."
      meta={overview.data?.observed_at ? `Collected ${ago(overview.data.observed_at)}` : undefined}
    >
      {overview.data && !configured && (
        <Banner tone="info">No backup source is connected yet. <Link className="ecc-link" href="/infrastructure/backup">Connect Azure Backup or Veeam</Link> to see jobs.</Banner>
      )}
      {jobs.error && <Banner tone="bad">Jobs could not be loaded: {jobs.error instanceof Error ? jobs.error.message : "request failed"}.</Banner>}
      {configured && (
        <>
          <StatStrip>
            <StatTile label="Jobs" value={jobs.data ? rows.length.toLocaleString("en-US") : "—"} sub={rows.length >= LIMIT ? `newest ${LIMIT} shown` : undefined} info="Jobs matching the filters in the selected window." loading={jobs.isLoading} />
            <StatTile label="Completed" tone="good" value={jobs.data ? String(count("completed") + count("completed_with_warnings")) : "—"} sub={jobs.data ? `${count("completed_with_warnings")} with warnings` : undefined} info="Jobs that completed, with or without warnings." loading={jobs.isLoading} />
            <StatTile label="Failed" info="Jobs the provider reported as failed. Select one for its error." value={jobs.data ? String(count("failed")) : "—"} alarm={count("failed") > 0} loading={jobs.isLoading} />
            <StatTile label="In progress" info="Jobs still running at the last collection." value={jobs.data ? String(count("in_progress")) : "—"} loading={jobs.isLoading} />
          </StatStrip>
          <Panel title="Jobs" info="Select a job for its timeline, error and the resource's other recent jobs. Filters are kept in the address bar." state={jobs.isLoading ? "loading" : "ready"} className="pk-col-12">
            <div className="pk-toolbar">
              <SearchBox value={search} onChange={setSearch} placeholder="Search resource, error code or message" label="Search jobs" />
              <select className="pk-input" aria-label="Time range" value={days} onChange={(e) => setParam({ days: e.target.value === "7" ? "" : e.target.value })} style={{ width: "auto" }}>
                {RANGES.map((d) => <option key={d} value={d}>{d === 1 ? "Last 24 hours" : `Last ${d} days`}</option>)}
              </select>
              <select className="pk-input" aria-label="Filter by status" value={status} onChange={(e) => setParam({ status: e.target.value })} style={{ width: "auto" }}>
                <option value="">Any status</option>
                {STATUSES.map((s) => <option key={s} value={s}>{titleCaseWords(s)}</option>)}
              </select>
              <select className="pk-input" aria-label="Filter by operation" value={operation} onChange={(e) => setParam({ operation: e.target.value })} style={{ width: "auto" }}>
                <option value="">Any operation</option>
                {[...new Set(operations)].sort().map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              {fo && fo.vaults.length > 1 && (
                <select className="pk-input" aria-label="Filter by storage" value={get("storage_id")} onChange={(e) => setParam({ storage_id: e.target.value })} style={{ width: "auto", maxWidth: 240 }}>
                  <option value="">Any storage</option>
                  {fo.vaults.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                </select>
              )}
              {fo && fo.workload_types.length > 1 && (
                <select className="pk-input" aria-label="Filter by workload" value={get("workload_type")} onChange={(e) => setParam({ workload_type: e.target.value })} style={{ width: "auto" }}>
                  <option value="">Any workload</option>
                  {fo.workload_types.map((w) => <option key={w} value={w}>{w}</option>)}
                </select>
              )}
              {fo && fo.locations.length > 1 && (
                <select className="pk-input" aria-label="Filter by region" value={get("location")} onChange={(e) => setParam({ location: e.target.value })} style={{ width: "auto" }}>
                  <option value="">Any region</option>
                  {fo.locations.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              )}
              {anyFilter && <button type="button" className="pk-button" onClick={() => { setSearch(""); router.replace(days === 7 ? pathname : `${pathname}?days=${days}`, { scroll: false }); }}>Clear filters</button>}
              <button type="button" className="pk-button" disabled={rows.length === 0} onClick={() => downloadCsv(`backup-jobs-${new Date().toISOString().slice(0, 10)}.csv`, CSV, rows)}>Export CSV</button>
            </div>
            <DataTable
              caption="Backup jobs"
              columns={columns}
              rows={rows}
              rowKey={(j) => j.id}
              onRowClick={(j) => setSelected(j)}
              pageSize={50}
              defaultSort={{ key: "started", dir: "desc" }}
              maxHeight="max(420px, calc(100vh - 460px))"
              emptyText={anyFilter ? "No job matches these filters." : "No backup job in this window."}
            />
          </Panel>
        </>
      )}
      {selected && <JobDrawer job={selected} onClose={() => setSelected(null)} />}
    </PageShell>
  );
}

export default function BackupJobsPage() {
  return (
    <Suspense>
      <JobsContent />
    </Suspense>
  );
}
