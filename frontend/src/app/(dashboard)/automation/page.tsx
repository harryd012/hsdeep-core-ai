"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BookOpenCheck, CheckCircle2, Cpu, ShieldCheck, Workflow, XCircle } from "lucide-react";
import { AutomationStatus, SOP, getAutomationStatus, listSops, type SourceRunHealth } from "@/lib/api";
import { pct } from "@/lib/commandCenter/format";
import {
  Banner,
  DataTable,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/**
 * /automation — what the platform runs on its own: scheduled collector runs
 * and the SOP library that remediation draws from. Every number comes from
 * /api/automation/status and /api/sops; nothing is estimated.
 */
export default function AutomationPage() {
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [sops, setSops] = useState<SOP[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sopError, setSopError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [st, sp] = await Promise.allSettled([getAutomationStatus(), listSops({ limit: 200 })]);
      if (cancelled) return;
      if (st.status === "fulfilled") { setStatus(st.value); setError(null); }
      else setError(st.reason instanceof Error ? st.reason.message : "Failed to load automation status");
      if (sp.status === "fulfilled") { setSops(sp.value); setSopError(null); }
      else setSopError(sp.reason instanceof Error ? sp.reason.message : "Failed to load SOPs");
      setUpdatedAt(new Date().toISOString());
    }
    const first = window.setTimeout(load, 0);
    const timer = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearTimeout(first); window.clearInterval(timer); };
  }, []);

  const loading = !status && !error;
  // No runs means no success rate — never 0%.
  const success = status ? pct(status.runs_succeeded_last_24h, status.runs_last_24h) : null;
  const failed = status ? status.runs_last_24h - status.runs_succeeded_last_24h : null;
  const activeSops = sops?.filter((s) => s.is_active) ?? [];
  const approvalSops = activeSops.filter((s) => s.requires_approval).length;
  const v = (x: number | null | undefined) => (x === null || x === undefined ? "—" : x.toLocaleString("en-US"));

  const runColumns: Column<SourceRunHealth>[] = [
    { key: "source", header: "Collector", sortValue: (r) => r.source_name.toLowerCase(), render: (r) => <span className="pk-cell-main">{r.source_name}</span> },
    { key: "status", header: "Source status", sortValue: (r) => r.status, render: (r) => <Pill tone={statusTone(r.status)}>{titleCaseWords(r.status)}</Pill> },
    {
      key: "last",
      header: "Last run",
      sortValue: (r) => r.last_run_status ?? "",
      render: (r) => (r.last_run_status ? <Pill tone={statusTone(r.last_run_status)}>{titleCaseWords(r.last_run_status)}</Pill> : <span className="pk-muted">Never run</span>),
    },
    { key: "at", header: "Ran", sortValue: (r) => (r.last_run_at ? new Date(r.last_run_at).getTime() : null), render: (r) => <span className="pk-muted">{r.last_run_at ? ago(r.last_run_at) : "—"}</span> },
    {
      key: "next",
      header: "Next run",
      sortValue: (r) => (r.next_run_at ? new Date(r.next_run_at).getTime() : null),
      render: (r) => <span className="pk-muted">{r.next_run_at ? new Date(r.next_run_at).toLocaleString() : "Not scheduled"}</span>,
    },
  ];

  const sopColumns: Column<SOP>[] = [
    {
      key: "name",
      header: "Procedure",
      sortValue: (s) => s.name.toLowerCase(),
      render: (s) => (
        <>
          <span className="pk-cell-main">{s.name}</span>
          <span className="pk-cell-sub">{s.sop_number}</span>
        </>
      ),
    },
    { key: "category", header: "Category", sortValue: (s) => s.category, render: (s) => titleCaseWords(s.category) },
    { key: "scope", header: "Applies to", render: (s) => <span className="pk-muted">{[s.vendor, s.platform, s.device_type].filter(Boolean).map((x) => titleCaseWords(x)).join(" · ") || "Any"}</span> },
    { key: "approval", header: "Approval", sortValue: (s) => (s.requires_approval ? 1 : 0), render: (s) => (s.requires_approval ? <Pill tone="warn">Required</Pill> : <Pill tone="neutral">Not required</Pill>) },
    { key: "eta", header: "Est. time", numeric: true, sortValue: (s) => s.estimated_resolution_minutes, render: (s) => (s.estimated_resolution_minutes ? `${s.estimated_resolution_minutes} min` : "—") },
  ];

  return (
    <PageShell
      title="Automation"
      icon={Workflow}
      subtitle="Scheduled collection and the standard operating procedures available for automated remediation."
      meta={updatedAt ? `Updated ${ago(updatedAt)} · refreshes every 30 s` : undefined}
      actions={<Link className="pk-button" href="/sop-engine"><BookOpenCheck size={14} /> SOP Engine</Link>}
    >
      {error && <Banner tone="warn">Automation status could not be loaded: {error}</Banner>}
      {status && status.runs_last_24h > 0 && success === 0 && (
        <Banner tone="bad">Every collector run in the last 24 hours failed. Check the collector errors on the Monitoring page.</Banner>
      )}

      <StatStrip>
        <StatTile label="Sources connected" value={status ? `${status.sources_connected} / ${status.sources_total}` : "—"} icon={Workflow} tone={status && status.sources_connected < status.sources_total ? "warn" : "good"} info="Monitoring sources that are connected, out of all configured." href="/monitoring" loading={loading} />
        <StatTile label="Runs (24h)" value={v(status?.runs_last_24h)} icon={Cpu} info="Collector runs started in the last 24 hours." loading={loading} />
        <StatTile label="Succeeded" value={v(status?.runs_succeeded_last_24h)} icon={CheckCircle2} tone="good" info="Collector runs that completed successfully in the last 24 hours." loading={loading} />
        <StatTile label="Failed" value={v(failed)} icon={XCircle} alarm={!!failed} info="Collector runs that did not succeed in the last 24 hours." loading={loading} />
        <StatTile
          label="Success rate"
          value={success === null ? "—" : `${success}%`}
          icon={ShieldCheck}
          alarm={success !== null && success < 80}
          tone={success === null ? "neutral" : success >= 95 ? "good" : success >= 80 ? "warn" : "bad"}
          info="Share of collector runs that succeeded in the last 24 hours. Not measured when nothing ran."
          loading={loading}
        />
        <StatTile label="Active SOPs" value={sops ? v(activeSops.length) : "—"} icon={BookOpenCheck} sub={sops ? `${approvalSops} need approval` : undefined} info="Active procedures available to remediation." href="/sop-engine" loading={!sops && !sopError} />
      </StatStrip>

      <div className="pk-grid">
        <Panel
          title="Collector runs"
          info="The latest run of each scheduled collector."
          aside={status ? `${status.sources.length} collectors` : undefined}
          state={status ? (status.sources.length ? "ready" : "empty") : error ? "error" : "loading"}
          emptyText="No collector activity yet. Add a monitoring source to start scheduled collection."
          className="pk-col-6"
        >
          {status && <DataTable caption="Collector runs" columns={runColumns} rows={status.sources} rowKey={(r) => r.source_id} defaultSort={{ key: "at", dir: "desc" }} />}
        </Panel>

        <Panel
          title="SOP library"
          info="Active procedures, and whether running them needs an approval."
          aside={<Link className="ecc-link" href="/sop-engine">Manage →</Link>}
          state={sops ? (activeSops.length ? "ready" : "empty") : sopError ? "error" : "loading"}
          emptyText="No active SOPs yet. Create or import procedures in the SOP Engine."
          errorText={sopError ? `SOPs could not be loaded: ${sopError}` : undefined}
          className="pk-col-6"
        >
          <DataTable caption="Active SOPs" columns={sopColumns} rows={activeSops} rowKey={(s) => s.id} defaultSort={{ key: "name", dir: "asc" }} maxHeight={440} />
        </Panel>
      </div>
    </PageShell>
  );
}
