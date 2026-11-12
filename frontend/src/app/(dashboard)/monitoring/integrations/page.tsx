"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plug } from "lucide-react";

import {
  STATE_LABEL,
  STATE_TONE,
  formatDuration,
  getIntegrationCenter,
  integrationHref,
  type IntegrationRow,
  type IntegrationState,
} from "@/lib/integrationCenter";
import {
  Banner,
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
} from "@/components/ui/PageKit";

/**
 * /monitoring/integrations — Integration Center (observability plan P12):
 * every monitoring source and integration with one state derived from what
 * was recorded, last success, 24 h collector runs and what each one feeds.
 */

type Filter = "all" | "attention" | IntegrationState;
const ATTENTION: IntegrationState[] = ["failing", "degraded", "stale"];

const COLUMNS: Column<IntegrationRow>[] = [
  {
    key: "name", header: "Integration", sortValue: (r) => r.name,
    render: (r) => (
      <div>
        <span className="pk-cell-main">{r.name}</span>
        <span className="pk-cell-sub">
          {titleCaseWords(r.type)}{r.kind === "integration" ? " · integration" : r.kind === "itsm" ? " · ITSM" : ""}{r.endpoint ? ` · ${r.endpoint}` : ""}
        </span>
        {r.last_error && r.state !== "healthy" && (
          <span className="pk-cell-sub" style={{ color: "var(--ecc-bad)" }} title={r.last_error}>
            <span className="pk-truncate">{r.last_error}</span>
          </span>
        )}
      </div>
    ),
  },
  { key: "state", header: "State", sortValue: (r) => r.state, render: (r) => <Pill tone={STATE_TONE[r.state]}>{STATE_LABEL[r.state]}</Pill> },
  {
    key: "success", header: "Last success", sortValue: (r) => r.last_success_at,
    render: (r) => (r.last_success_at ? <span title={new Date(r.last_success_at).toLocaleString()}>{ago(r.last_success_at)}</span> : "Never"),
  },
  {
    key: "runs", header: "Runs 24 h", numeric: true, sortValue: (r) => r.runs_24h,
    render: (r) => (r.runs_24h === null ? "—" : r.failed_runs_24h ? `${r.runs_24h} (${r.failed_runs_24h} failed)` : String(r.runs_24h)),
  },
  { key: "rate", header: "Success 24 h", numeric: true, sortValue: (r) => r.success_rate_24h, render: (r) => (r.success_rate_24h === null ? "—" : `${r.success_rate_24h} %`) },
  { key: "avg", header: "Avg run", numeric: true, sortValue: (r) => r.avg_run_ms, render: (r) => formatDuration(r.avg_run_ms) },
  { key: "sensors", header: "Sensors", numeric: true, sortValue: (r) => r.sensors, render: (r) => (r.sensors === null ? "—" : r.sensors.toLocaleString("en-US")) },
  { key: "devices", header: "Devices", numeric: true, sortValue: (r) => r.devices, render: (r) => (r.devices === null ? "—" : r.devices.toLocaleString("en-US")) },
  {
    key: "cred", header: "Credential", sortValue: (r) => (r.credential_configured ? 1 : 0),
    render: (r) => (r.credential_configured ? "Configured" : <span className="pk-muted">None</span>),
  },
];

export default function IntegrationCenterPage() {
  const q = useQuery({ queryKey: ["integration-center"], queryFn: getIntegrationCenter, refetchInterval: 30_000 });
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const d = q.data;
  const s = d?.summary;
  const items = useMemo(() => d?.items ?? [], [d]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter((r) => {
      if (filter === "attention" && !ATTENTION.includes(r.state)) return false;
      if (filter !== "all" && filter !== "attention" && r.state !== filter) return false;
      if (!needle) return true;
      return [r.name, r.type, r.vendor, r.endpoint, r.last_error].some((v) => v?.toLowerCase().includes(needle));
    });
  }, [items, filter, search]);

  const count = (k: IntegrationState) => s?.[k] ?? 0;
  const attention = ATTENTION.reduce((n, k) => n + count(k), 0);
  const loading = q.isLoading;

  return (
    <PageShell
      title="Integration Center"
      icon={Plug}
      crumbs={[{ label: "Monitoring", href: "/monitoring" }, { label: "Integration Center" }]}
      subtitle="Every monitoring source and integration feeding the platform, with its health and collection history"
      meta={d ? `Updated ${ago(d.generated_at)} · refreshes every 30 s` : undefined}
    >
      {q.isError && <Banner tone="bad">The Integration Center could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}

      <StatStrip>
        <StatTile label="Integrations" value={s ? String(s.total) : "—"} sub={s ? `${s.sources} sources · ${s.itsm} ITSM · ${s.integrations} integrations` : undefined}
          info="Monitoring sources, ITSM connections (ServiceNow and others) and generic integrations for this tenant." loading={loading} />
        <StatTile label="Need attention" value={s ? String(attention) : "—"} alarm={attention > 0} tone={s ? (attention > 0 ? "bad" : "good") : undefined}
          sub={s ? `${count("failing")} failing · ${count("degraded")} degraded · ${count("stale")} stale` : undefined}
          info="Failing: last collection failed. Degraded: the health state machine marked it degraded. Stale: no success for three poll intervals (at least 15 minutes)." loading={loading} />
        <StatTile label="Healthy" value={s ? String(count("healthy")) : "—"} tone="good" info="Collected successfully within its expected interval." loading={loading} />
        <StatTile label="Never synced" value={s ? String(count("never_synced")) : "—"} sub={s ? `${count("disabled")} disabled` : undefined}
          info="Configured, but nothing has been collected successfully yet." loading={loading} />
        <StatTile label="Collector runs 24 h" value={s ? s.runs_24h.toLocaleString("en-US") : "—"}
          sub={s ? `${s.failed_runs_24h} failed${s.itsm_jobs_24h ? ` · ITSM ${s.itsm_jobs_24h} jobs, ${s.itsm_failed_jobs_24h} failed` : ""}` : undefined}
          tone={s && s.failed_runs_24h + s.itsm_failed_jobs_24h > 0 ? "warn" : undefined}
          info="Recorded collector runs of all sources in the last 24 hours." loading={loading} />
      </StatStrip>

      <Panel title="Integrations" info="Worst state first. Open a source for its sync history and to sync it now."
        aside={d ? `${visible.length} of ${items.length}` : undefined} state={loading ? "loading" : "ready"} skeletonHeight={320}>
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, type, endpoint, error" label="Search integrations" />
          <Tabs<Filter>
            label="Integration state"
            value={filter}
            onChange={setFilter}
            tabs={[
              { key: "all", label: "All", count: items.length },
              { key: "attention", label: "Need attention", count: attention },
              { key: "never_synced", label: "Never synced", count: count("never_synced") },
              { key: "disabled", label: "Disabled", count: count("disabled") },
              { key: "healthy", label: "Healthy", count: count("healthy") },
            ]}
          />
        </div>
        <DataTable
          caption="Integrations"
          columns={COLUMNS}
          rows={visible}
          rowKey={(r) => `${r.kind}:${r.id}`}
          rowHref={integrationHref}
          emptyText={items.length === 0 ? "No monitoring source or integration is configured for this tenant." : "Nothing matches the current filter."}
          maxHeight="max(480px, calc(100vh - 320px))"
        />
      </Panel>
    </PageShell>
  );
}
