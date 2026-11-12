"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Ticket } from "lucide-react";

import {
  STATE_LABEL,
  STATE_TONE,
  formatDuration,
  getIntegrationITSM,
  type ITSMSyncJobRow,
} from "@/lib/integrationCenter";
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
 * /monitoring/integrations/itsm/[id] — one ITSM connection (ServiceNow or
 * another ITSM provider) in the Integration Center: its derived state and the
 * last 50 sync jobs (pushes, pulls, webhooks) with retries and dead letters.
 * Read-only; retrying a dead-letter job stays on the ITSM pages.
 */

const when = (iso: string | null) => (iso ? <span title={new Date(iso).toLocaleString()}>{ago(iso)}</span> : "—");

const JOB_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  succeeded: "good",
  failed: "warn",
  dead_letter: "bad",
  pending: "neutral",
  processing: "neutral",
};

const JOB_COLUMNS: Column<ITSMSyncJobRow>[] = [
  { key: "created", header: "Queued", sortValue: (r) => r.created_at, render: (r) => when(r.created_at) },
  {
    key: "status", header: "Result", sortValue: (r) => r.status,
    render: (r) => <Pill tone={JOB_TONE[r.status] ?? statusTone(r.status)}>{titleCaseWords(r.status)}</Pill>,
  },
  { key: "type", header: "Job", sortValue: (r) => r.job_type, render: (r) => titleCaseWords(r.job_type) },
  { key: "retries", header: "Retries", numeric: true, sortValue: (r) => r.retry_count, render: (r) => `${r.retry_count} / ${r.max_retries}` },
  { key: "duration", header: "Duration", numeric: true, sortValue: (r) => r.duration_ms, render: (r) => formatDuration(r.duration_ms) },
  {
    key: "next", header: "Next attempt", sortValue: (r) => r.next_attempt_at,
    render: (r) => (r.status === "pending" || r.status === "failed" ? when(r.next_attempt_at) : "—"),
  },
  {
    key: "error", header: "Error", sortValue: (r) => r.error,
    render: (r) => (r.error ? <span className="pk-truncate" title={r.error} style={{ color: "var(--ecc-bad)" }}>{r.error}</span> : "—"),
  },
];

export default function IntegrationITSMPage() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const q = useQuery({ queryKey: ["integration-center", "itsm", id], queryFn: () => getIntegrationITSM(id), enabled: !!id, refetchInterval: 30_000 });
  const d = q.data;
  const c = d?.connection;
  const loading = q.isLoading;

  return (
    <PageShell
      title={c?.name ?? "ITSM connection"}
      icon={Ticket}
      crumbs={[{ label: "Monitoring", href: "/monitoring" }, { label: "Integration Center", href: "/monitoring/integrations" }, { label: c?.name ?? "ITSM connection" }]}
      subtitle={c ? `${titleCaseWords(c.type)} · ITSM${c.endpoint ? ` · ${c.endpoint}` : ""}` : undefined}
    >
      {q.isError && <Banner tone="bad">The connection could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      {c?.last_error && c.state !== "healthy" && <Banner tone="warn">Last error: {c.last_error}</Banner>}

      <StatStrip>
        <StatTile label="State" value={c ? STATE_LABEL[c.state] : "—"} tone={c ? STATE_TONE[c.state] : undefined}
          sub={c ? `Status ${titleCaseWords(c.status)}${c.enabled ? "" : " · switched off"}` : undefined}
          info="Failing: the connection reports an error, its health check failed, or its latest finished job went to the dead-letter queue. Degraded: jobs of the last 24 hours are being retried or were dead-lettered." loading={loading} />
        <StatTile label="Last success" value={c?.last_success_at ? ago(c.last_success_at) : c ? "Never" : "—"}
          sub={c?.last_attempt_at ? `Last attempt ${ago(c.last_attempt_at)}` : undefined}
          info="When a sync job last completed successfully (or the connection last synced)." loading={loading} />
        <StatTile label="Jobs 24 h" value={c?.runs_24h != null ? String(c.runs_24h) : "—"}
          sub={c?.success_rate_24h != null ? `${c.success_rate_24h} % succeeded · avg ${formatDuration(c.avg_run_ms)}` : undefined}
          tone={c && (c.failed_runs_24h ?? 0) > 0 ? "warn" : undefined}
          info="Sync jobs queued in the last 24 hours and the share that succeeded." loading={loading} />
        <StatTile label="Dead letter 24 h" value={c?.dead_letter_24h != null ? String(c.dead_letter_24h) : "—"}
          alarm={(c?.dead_letter_24h ?? 0) > 0}
          info="Jobs that used up their retries in the last 24 hours. They stay in the queue until retried from the ITSM pages." loading={loading} />
        <StatTile label="Open jobs" value={c?.open_jobs != null ? String(c.open_jobs) : "—"}
          sub={c ? (c.credential_configured ? "Credential configured" : "No credential stored") : undefined}
          info="Jobs pending, processing or waiting to be retried." loading={loading} />
      </StatStrip>

      <Panel title="Sync history" info="The last 50 sync jobs, newest first." aside={d ? `${d.jobs.length} jobs` : undefined}
        state={loading ? "loading" : "ready"} skeletonHeight={280}>
        <DataTable caption="ITSM sync jobs" columns={JOB_COLUMNS} rows={d?.jobs ?? []} rowKey={(r) => r.id}
          emptyText="No sync job recorded for this connection." pageSize={25} />
      </Panel>
    </PageShell>
  );
}
