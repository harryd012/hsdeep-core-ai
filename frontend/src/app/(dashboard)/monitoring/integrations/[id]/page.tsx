"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, RefreshCw } from "lucide-react";

import { syncMonitoringSource } from "@/lib/monitoringApi";
import {
  STATE_LABEL,
  STATE_TONE,
  formatDuration,
  getIntegrationSource,
  type CollectorRunRow,
  type HealthEventRow,
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
 * /monitoring/integrations/[id] — one monitoring source in the Integration
 * Center: its state, the last 50 collector runs (sync history) and health
 * state-machine events. "Sync now" runs the real collection path and needs
 * monitoring.update; the API refuses it otherwise.
 */

const when = (iso: string | null) => (iso ? <span title={new Date(iso).toLocaleString()}>{ago(iso)}</span> : "—");

const RUN_COLUMNS: Column<CollectorRunRow>[] = [
  { key: "started", header: "Started", sortValue: (r) => r.started_at, render: (r) => when(r.started_at) },
  { key: "status", header: "Result", sortValue: (r) => r.status, render: (r) => <Pill tone={statusTone(r.status)}>{titleCaseWords(r.status)}</Pill> },
  { key: "trigger", header: "Trigger", sortValue: (r) => r.trigger, render: (r) => titleCaseWords(r.trigger) },
  { key: "duration", header: "Duration", numeric: true, sortValue: (r) => r.duration_ms, render: (r) => formatDuration(r.duration_ms) },
  { key: "devices", header: "Devices", numeric: true, sortValue: (r) => r.devices, render: (r) => r.devices.toLocaleString("en-US") },
  { key: "sensors", header: "Sensors", numeric: true, sortValue: (r) => r.sensors, render: (r) => r.sensors.toLocaleString("en-US") },
  { key: "metrics", header: "Metrics", numeric: true, sortValue: (r) => r.metrics, render: (r) => r.metrics.toLocaleString("en-US") },
  { key: "alerts", header: "Alerts", numeric: true, sortValue: (r) => r.alerts, render: (r) => r.alerts.toLocaleString("en-US") },
  {
    key: "error", header: "Error", sortValue: (r) => r.error,
    render: (r) => (r.error ? <span className="pk-truncate" title={r.error} style={{ color: "var(--ecc-bad)" }}>{r.error}</span> : "—"),
  },
];

const EVENT_COLUMNS: Column<HealthEventRow>[] = [
  { key: "at", header: "When", sortValue: (r) => r.created_at, render: (r) => when(r.created_at) },
  { key: "type", header: "Event", sortValue: (r) => r.event_type, render: (r) => titleCaseWords(r.event_type) },
  {
    key: "change", header: "Change",
    render: (r) => (r.from_health_status || r.to_health_status
      ? `${titleCaseWords(r.from_health_status) || "—"} → ${titleCaseWords(r.to_health_status) || "—"}`
      : r.from_status || r.to_status ? `${titleCaseWords(r.from_status) || "—"} → ${titleCaseWords(r.to_status) || "—"}` : "—"),
  },
  { key: "failures", header: "Consecutive failures", numeric: true, sortValue: (r) => r.consecutive_failures, render: (r) => r.consecutive_failures ?? "—" },
  { key: "reason", header: "Reason", render: (r) => <span className="pk-truncate" title={r.reason ?? undefined}>{r.reason ?? "—"}</span> },
];

export default function IntegrationSourcePage() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["integration-center", "source", id], queryFn: () => getIntegrationSource(id), enabled: !!id, refetchInterval: 30_000 });
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const d = q.data;
  const src = d?.source;
  const loading = q.isLoading;

  async function syncNow() {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await syncMonitoringSource(id);
      setSyncMsg(r.error
        ? { tone: "bad", text: `Sync finished with status ${r.status}: ${r.error}` }
        : { tone: "good", text: `Sync ${r.status}: ${r.sensors_total.toLocaleString("en-US")} sensors collected.` });
    } catch (e) {
      setSyncMsg({ tone: "bad", text: `Sync could not run: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setSyncing(false);
      await qc.invalidateQueries({ queryKey: ["integration-center"] });
    }
  }

  return (
    <PageShell
      title={src?.name ?? "Source"}
      icon={Plug}
      crumbs={[{ label: "Monitoring", href: "/monitoring" }, { label: "Integration Center", href: "/monitoring/integrations" }, { label: src?.name ?? "Source" }]}
      subtitle={src ? `${titleCaseWords(src.type)}${src.endpoint ? ` · ${src.endpoint}` : ""} · polled every ${src.poll_interval_seconds ?? "—"} s` : undefined}
      actions={
        <button type="button" className="pk-button" onClick={syncNow} disabled={syncing || !src || !src.enabled}
          title={src && !src.enabled ? "The source is disabled" : "Run one collection now (needs monitoring.update)"}>
          <RefreshCw size={14} /> {syncing ? "Syncing…" : "Sync now"}
        </button>
      }
    >
      {q.isError && <Banner tone="bad">The source could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      {syncMsg && <Banner tone={syncMsg.tone}>{syncMsg.text}</Banner>}
      {src?.last_error && src.state !== "healthy" && <Banner tone="warn">Last error: {src.last_error}</Banner>}

      <StatStrip>
        <StatTile label="State" value={src ? STATE_LABEL[src.state] : "—"} tone={src ? STATE_TONE[src.state] : undefined}
          sub={src ? `Status ${titleCaseWords(src.status)} · health ${titleCaseWords(src.health_status)}` : undefined}
          info="Derived from the last collection, the health state machine and the time since the last success." loading={loading} />
        <StatTile label="Last success" value={src?.last_success_at ? ago(src.last_success_at) : src ? "Never" : "—"}
          sub={src?.next_run_at ? `Next run ${new Date(src.next_run_at).toLocaleTimeString()}` : undefined}
          info="When the last collection completed successfully." loading={loading} />
        <StatTile label="Runs 24 h" value={src?.runs_24h != null ? String(src.runs_24h) : "—"}
          sub={src?.success_rate_24h != null ? `${src.success_rate_24h} % succeeded · avg ${formatDuration(src.avg_run_ms)}` : undefined}
          tone={src && (src.failed_runs_24h ?? 0) > 0 ? "warn" : undefined}
          info="Collector runs recorded in the last 24 hours and the share that succeeded (success or partial)." loading={loading} />
        <StatTile label="Feeds" value={src ? `${(src.sensors ?? 0).toLocaleString("en-US")} sensors` : "—"}
          sub={src ? `${(src.devices ?? 0).toLocaleString("en-US")} devices` : undefined}
          info="Sensors and devices this source currently maintains." loading={loading} />
        <StatTile label="Consecutive failures" value={src?.consecutive_failures != null ? String(src.consecutive_failures) : "—"}
          alarm={(src?.consecutive_failures ?? 0) > 0} sub={src ? (src.credential_configured ? "Credential configured" : "No credential stored") : undefined}
          info="Failed collections in a row; the source is marked degraded after repeated failures." loading={loading} />
      </StatStrip>

      <Panel title="Sync history" info="The last 50 collector runs, newest first." aside={d ? `${d.runs.length} runs` : undefined}
        state={loading ? "loading" : "ready"} skeletonHeight={280}>
        <DataTable caption="Collector runs" columns={RUN_COLUMNS} rows={d?.runs ?? []} rowKey={(r) => r.id}
          emptyText="This source has not run yet." pageSize={25} />
      </Panel>

      <Panel title="Health events" info="Transitions and notable events recorded by the source health state machine (last 50)."
        aside={d ? `${d.health_events.length} events` : undefined} state={loading ? "loading" : "ready"}>
        <DataTable caption="Health events" columns={EVENT_COLUMNS} rows={d?.health_events ?? []} rowKey={(r) => r.id}
          emptyText="No health event recorded for this source." />
      </Panel>
    </PageShell>
  );
}
