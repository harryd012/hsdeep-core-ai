"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Server } from "lucide-react";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, Tabs, ago, type Column, type Tone } from "@/components/ui/PageKit";
import { fmt } from "@/lib/hostHealth";
import { getServerFleet, type ResourceReading, type ServerPlatform, type ServerResources } from "@/lib/serverFleet";

/**
 * /infrastructure/servers — the server fleet (observability plan P5).
 *
 * Every Windows, Linux and VMware server that reports host resources, worst
 * first: CPU, memory, its fullest volume, load and uptime, each coloured by its
 * own sensor's status. Values are the collectors' readings as reported (VMware
 * MHz / MB are not turned into percentages), and a server with no reading for
 * an hour is marked stale rather than shown as current.
 */

const PLATFORM_TABS: { key: "all" | ServerPlatform; label: string }[] = [
  { key: "all", label: "All" },
  { key: "windows", label: "Windows" },
  { key: "linux", label: "Linux" },
  { key: "vmware", label: "VMware" },
  { key: "other", label: "Other" },
];

const PLATFORM_LABEL: Record<ServerPlatform, string> = { windows: "Windows", linux: "Linux", vmware: "VMware", other: "Other" };

function gaugeState(status: string): "ok" | "warning" | "critical" | "unknown" {
  if (status === "down") return "critical";
  if (status === "warning") return "warning";
  if (status === "up") return "ok";
  return "unknown";
}

function Gauge({ r }: { r: ResourceReading | null }) {
  if (!r) return <span className="pk-muted">Not measured</span>;
  const st = gaugeState(r.status);
  const pct = r.value !== null && r.unit === "%" ? Math.min(100, Math.max(0, r.value)) : null;
  return (
    <span className="hd-gauge" title={`${r.name} · ${r.status}${r.recorded_at ? ` · ${ago(r.recorded_at)}` : ""}`}>
      <span className="hd-gauge__value">
        {r.value === null ? "—" : fmt(r.value)}
        {r.value !== null && r.unit ? <small> {r.unit}</small> : null}
      </span>
      <span className="hd-gauge__track" aria-hidden="true">
        {pct !== null && <span className={`hd-gauge__fill hd-gauge__fill--${st}`} style={{ width: `${Math.max(3, pct)}%` }} />}
      </span>
    </span>
  );
}

function uptime(r: ResourceReading | null): string {
  if (!r || r.value === null) return "—";
  const s = r.unit === "s" || !r.unit ? r.value : null;
  if (s === null) return `${fmt(r.value)} ${r.unit}`;
  const d = Math.floor(s / 86_400), h = Math.floor((s % 86_400) / 3600);
  return d > 0 ? `${d} d ${h} h` : `${h} h ${Math.floor((s % 3600) / 60)} min`;
}

function rowTone(s: ServerResources): Tone {
  if (s.critical > 0) return "bad";
  if (s.warning > 0) return "warn";
  return s.stale ? "neutral" : "good";
}

export default function ServersPage() {
  const [platform, setPlatform] = useState<"all" | ServerPlatform>("all");
  const fleet = useQuery({
    queryKey: ["server-fleet", platform],
    queryFn: () => getServerFleet(platform === "all" ? undefined : platform),
    refetchInterval: 60_000,
  });
  const summary = fleet.data?.summary;
  const rows = fleet.data?.servers ?? [];
  const loading = fleet.isLoading;

  const columns: Column<ServerResources>[] = [
    { key: "name", header: "Server", sortValue: (s) => s.name.toLowerCase(), render: (s) => (
      <span>
        <strong>{s.name}</strong>
        <span className="pk-muted"> · {PLATFORM_LABEL[s.platform]}{s.ip_address ? ` · ${s.ip_address}` : ""}</span>
      </span>
    ) },
    { key: "state", header: "State", sortValue: (s) => s.critical * 1000 + s.warning, render: (s) => (
      <Pill tone={rowTone(s)}>
        {s.critical > 0 ? `${s.critical} down` : s.warning > 0 ? `${s.warning} warning` : s.stale ? "Stale" : "OK"}
      </Pill>
    ) },
    { key: "cpu", header: "CPU", sortValue: (s) => s.cpu?.value ?? null, render: (s) => <Gauge r={s.cpu} /> },
    { key: "memory", header: "Memory", sortValue: (s) => s.memory?.value ?? null, render: (s) => <Gauge r={s.memory} /> },
    { key: "disk", header: "Fullest disk", sortValue: (s) => s.disk?.value ?? null, render: (s) => (
      s.disk ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Gauge r={s.disk} />
          <span className="pk-muted pk-truncate" title={`${s.disks} volume(s)`}>{s.disk.name}{s.disks > 1 ? ` (+${s.disks - 1})` : ""}</span>
        </span>
      ) : <span className="pk-muted">Not measured</span>
    ) },
    { key: "load", header: "Load (1 min)", numeric: true, sortValue: (s) => s.load?.value ?? null, render: (s) => (s.load?.value != null ? fmt(s.load.value) : "—") },
    { key: "uptime", header: "Uptime", numeric: true, sortValue: (s) => s.uptime?.value ?? null, render: (s) => uptime(s.uptime) },
    { key: "seen", header: "Last reading", sortValue: (s) => (s.last_reading_at ? new Date(s.last_reading_at).getTime() : 0), render: (s) => (
      <span className={s.stale ? "pk-muted" : undefined} title={s.last_reading_at ? new Date(s.last_reading_at).toLocaleString() : undefined}>
        {s.last_reading_at ? ago(s.last_reading_at) : "Never"}
      </span>
    ) },
  ];

  return (
    <PageShell
      title="Servers"
      icon={Server}
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Servers" }]}
      subtitle="CPU, memory, disk, load and uptime for every Windows, Linux and VMware server, worst first."
    >
      {fleet.isError && <Banner tone="warn">The server fleet could not be loaded{fleet.error instanceof Error ? `: ${fleet.error.message}` : "."}</Banner>}

      <StatStrip>
        <StatTile label="Servers measured" value={summary ? String(summary.servers) : "—"} loading={loading}
          sub={summary ? `${summary.by_platform.windows} Windows · ${summary.by_platform.linux} Linux · ${summary.by_platform.vmware} VMware` : undefined}
          info="Servers with at least one CPU, memory, disk, load or uptime sensor." />
        <StatTile label="With a sensor down" value={summary ? String(summary.critical) : "—"} loading={loading} info="Servers where at least one resource sensor is past its critical threshold or down." />
        <StatTile label="With warnings" value={summary ? String(summary.warning) : "—"} loading={loading} info="Servers with a warning but nothing down." />
        <StatTile label="Disk at 90 % or more" value={summary ? String(summary.disk_over_90) : "—"} loading={loading} info="Servers whose fullest volume reports 90 % or more used." />
        <StatTile label="CPU at 90 % or more" value={summary ? String(summary.cpu_over_90) : "—"} loading={loading} info="Servers whose busiest CPU reading is 90 % or more." />
        <StatTile label="Stale" value={summary ? String(summary.stale) : "—"} loading={loading} info="No reading for over an hour: their values are the last ones received." />
      </StatStrip>

      <Panel
        title="Server resources"
        info="Each value is the worst of its kind on the server (busiest core, fullest volume), coloured by that sensor's own thresholds. Select a server for its full history."
        aside={<Tabs label="Platform" value={platform} onChange={setPlatform} tabs={PLATFORM_TABS} />}
        state={fleet.data ? "ready" : fleet.isError ? "error" : "loading"}
        onRetry={() => fleet.refetch()}
      >
        <DataTable
          caption="Server resources"
          rows={rows}
          rowKey={(s) => s.device_id}
          rowHref={(s) => `/infrastructure/hosts/${s.device_id}`}
          columns={columns}
          pageSize={50}
          maxHeight="max(480px, calc(100vh - 360px))"
          emptyText={
            summary && summary.servers === 0
              ? "No server reports CPU, memory or disk yet. Add a Linux (SSH), Windows (WinRM / WMI) or VMware source to start collecting."
              : "No servers on this platform."
          }
        />
        {summary && summary.servers === 0 && (
          <p className="pk-muted" style={{ marginTop: 8 }}>
            Hosts monitored only through generic checks (ping, HTTP, SSL) appear under <Link className="ecc-link" href="/infrastructure/hosts">Hosts</Link>.
          </p>
        )}
      </Panel>
    </PageShell>
  );
}
