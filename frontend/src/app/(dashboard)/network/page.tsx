"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Network } from "lucide-react";
import {
  formatBps,
  formatBytes,
  getNetworkInterfaces,
  getNetworkOverview,
  type InterfaceSort,
  type InterfaceStatusFilter,
  type NetworkInterface,
  type VpnTunnel,
} from "@/lib/network";
import { Banner, BarList, DataTable, PageShell, Panel, Pill, SearchBox, StatStrip, StatTile, Tabs, ago, titleCaseWords, type Column, type Tone } from "@/components/ui/PageKit";

/**
 * /network — Network Monitoring Center: interface health, the busiest and
 * error-prone links, interfaces that are enabled but down, VPN tunnels, and
 * every interface with filters. Rates come from two real counter samples; a
 * link whose last sample is older than 30 minutes is shown as stale, never as
 * its current load.
 */

const PAGE = 100;

function statusTone(i: NetworkInterface): Tone {
  if (i.admin_status === "down") return "neutral";
  if (i.oper_status === "up") return "good";
  if (i.oper_status === "down") return "bad";
  return "warn";
}

function statusLabel(i: NetworkInterface): string {
  if (i.admin_status === "down") return "Disabled";
  if (i.oper_status === "up") return "Up";
  if (i.oper_status === "down") return "Down";
  return titleCaseWords(i.oper_status);
}

function load(i: NetworkInterface): React.ReactNode {
  if (i.utilization_pct === null && i.throughput_bps === null) return <span className="pk-muted">Not measured</span>;
  const text = i.utilization_pct !== null ? `${i.utilization_pct.toFixed(1)} %` : formatBps(i.throughput_bps);
  return i.fresh ? text : <span className="pk-muted" title="Last sample older than 30 minutes">{text} (stale)</span>;
}

const ifaceLink = (i: NetworkInterface) => `/network/interfaces/${i.id}`;

const interfaceColumns: Column<NetworkInterface>[] = [
  { key: "device", header: "Device", sortValue: (i) => i.device_name, render: (i) => <Link className="ecc-link" href={`/infrastructure/hosts/${i.device_id}`} onClick={(e) => e.stopPropagation()}>{i.device_name}</Link> },
  { key: "name", header: "Interface", sortValue: (i) => i.name, render: (i) => <><strong>{i.name}</strong>{i.description && <div className="pk-muted">{i.description}</div>}</> },
  { key: "status", header: "Status", sortValue: (i) => statusLabel(i), render: (i) => <Pill tone={statusTone(i)}>{statusLabel(i)}</Pill> },
  { key: "speed", header: "Speed", numeric: true, sortValue: (i) => i.speed_mbps, render: (i) => (i.speed_mbps !== null ? formatBps(i.speed_mbps * 1e6) : "—") },
  { key: "in", header: "In", numeric: true, sortValue: (i) => i.rx_bps, render: (i) => (i.fresh ? formatBps(i.rx_bps) : <span className="pk-muted">—</span>) },
  { key: "out", header: "Out", numeric: true, sortValue: (i) => i.tx_bps, render: (i) => (i.fresh ? formatBps(i.tx_bps) : <span className="pk-muted">—</span>) },
  { key: "load", header: "Utilization", numeric: true, sortValue: (i) => i.utilization_pct, render: load },
  { key: "seen", header: "Last sample", sortValue: (i) => i.last_telemetry_at, render: (i) => <span className="pk-muted">{i.last_telemetry_at ? ago(i.last_telemetry_at) : "never"}</span> },
];

const tunnelColumns: Column<VpnTunnel>[] = [
  { key: "device", header: "Firewall", sortValue: (t) => t.device_name, render: (t) => <Link className="ecc-link" href={`/infrastructure/hosts/${t.device_id}`}>{t.device_name}</Link> },
  { key: "name", header: "Tunnel", sortValue: (t) => t.name, render: (t) => <><strong>{t.name}</strong><div className="pk-muted">{t.tunnel_type.toUpperCase()}{t.remote_gateway ? ` · ${t.remote_gateway}` : ""}</div></> },
  { key: "status", header: "Status", sortValue: (t) => t.status, render: (t) => <Pill tone={t.status === "up" ? "good" : "bad"}>{titleCaseWords(t.status)}</Pill> },
  { key: "p2", header: "Phase 2", render: (t) => (t.phase2_total ? `${t.phase2_up ?? 0} / ${t.phase2_total}` : "—") },
  { key: "traffic", header: "Traffic in / out", render: (t) => `${formatBytes(t.bytes_in)} / ${formatBytes(t.bytes_out)}` },
  { key: "change", header: "Last change", sortValue: (t) => t.last_change_at, render: (t) => <span className="pk-muted">{t.last_change_at ? ago(t.last_change_at) : "—"}</span> },
];

function OverviewTab({ onShow }: { onShow: (status: InterfaceStatusFilter) => void }) {
  const o = useQuery({ queryKey: ["network", "overview"], queryFn: () => getNetworkOverview(), refetchInterval: 60_000 });
  const d = o.data;
  const i = d?.interfaces;
  const noRates = !!i && i.total > 0 && i.measured === 0;
  return (
    <>
      {o.isError && <Banner tone="bad">Network data could not be loaded{o.error?.message ? `: ${o.error.message}` : "."}</Banner>}
      {i && i.total === 0 && <Banner tone="info">No interfaces collected yet. Interfaces come from SNMP and vendor API sources (switches, routers, firewalls).</Banner>}
      {noRates && (
        <Banner tone="warn">
          No interface has a current traffic rate{i.last_telemetry_at ? `: the last counter sample is from ${ago(i.last_telemetry_at)}` : ""}.
          Rates need two polls within the collection interval; check that the network sources are collecting.
        </Banner>
      )}
      <StatStrip>
        <StatTile label="Interfaces" value={i ? i.total.toLocaleString("en-US") : "—"} sub={i ? `on ${i.devices} device${i.devices === 1 ? "" : "s"}` : undefined} info="Interfaces the collectors report." loading={o.isLoading} />
        <StatTile label="Up" value={i ? i.up.toLocaleString("en-US") : "—"} tone="good" info="Operationally up." loading={o.isLoading} />
        <StatTile label="Down while enabled" value={i ? i.unexpected_down.toLocaleString("en-US") : "—"} tone={i && i.unexpected_down > 0 ? "bad" : "good"} info="Administratively enabled but operationally down: a cable, peer or fault worth checking." loading={o.isLoading} />
        <StatTile label="Disabled" value={i ? i.admin_down.toLocaleString("en-US") : "—"} tone="neutral" info="Administratively shut down." loading={o.isLoading} />
        <StatTile label="With current traffic" value={i ? `${i.measured} / ${i.total}` : "—"} info="Interfaces with a traffic rate from a sample in the last 30 minutes." loading={o.isLoading} />
        <StatTile label="VPN tunnels down" value={d ? `${d.tunnels.down} / ${d.tunnels.total}` : "—"} tone={d && d.tunnels.down > 0 ? "bad" : "good"} info="IPsec / SSL tunnels the firewalls report down." loading={o.isLoading} />
      </StatStrip>
      <div className="pk-grid">
        <Panel title="Busiest links" info="Highest utilization (busiest direction against the interface speed), current samples only." state={o.isLoading ? "loading" : "ready"} className="pk-col-6">
          <BarList
            rows={(d?.top_utilization ?? []).map((x) => ({ key: x.id, label: `${x.device_name} · ${x.name}`, value: x.utilization_pct ?? 0, href: ifaceLink(x), title: `${formatBps(x.rx_bps)} in, ${formatBps(x.tx_bps)} out` }))}
            valueLabel={(v) => `${v.toFixed(1)} %`}
            emptyText="No current utilization: interfaces need a speed and two recent samples."
          />
        </Panel>
        <Panel title="Top throughput" info="Inbound plus outbound traffic, current samples only." state={o.isLoading ? "loading" : "ready"} className="pk-col-6">
          <BarList
            rows={(d?.top_throughput ?? []).map((x) => ({ key: x.id, label: `${x.device_name} · ${x.name}`, value: x.throughput_bps ?? 0, href: ifaceLink(x) }))}
            valueLabel={(v) => formatBps(v)}
            emptyText="No current traffic rates."
          />
        </Panel>
        <Panel title={`Down while enabled (${i?.unexpected_down ?? 0})`} info="Enabled interfaces that are down, most recent change first."
          aside={i && i.unexpected_down > 0 ? <button type="button" className="pk-button" onClick={() => onShow("unexpected_down")}>Show all</button> : undefined}
          state={o.isLoading ? "loading" : "ready"} className="pk-col-6">
          <DataTable caption="Enabled interfaces that are down" columns={interfaceColumns.filter((c) => ["device", "name", "speed", "seen"].includes(c.key))}
            rows={d?.unexpected_down ?? []} rowKey={(x) => x.id} rowHref={ifaceLink} maxHeight={320} emptyText="Every enabled interface is up." />
        </Panel>
        <Panel title="Interface errors (last hour)" info="Inbound plus outbound errors per second, from recorded counter history." state={o.isLoading ? "loading" : "ready"} className="pk-col-6">
          <BarList
            rows={(d?.top_errors ?? []).map((x) => ({ key: x.id, label: `${x.device_name} · ${x.name}`, value: x.errors_per_s_1h, href: ifaceLink(x) }))}
            valueLabel={(v) => `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })} /s`}
            emptyText="No interface errors recorded in the last hour."
          />
        </Panel>
        <Panel title={`VPN tunnels (${d?.tunnels.total ?? 0})`} info="Tunnels reported by FortiGate and Palo Alto firewalls; down tunnels first." state={o.isLoading ? "loading" : "ready"} className="pk-col-12">
          <DataTable caption="VPN tunnels" columns={tunnelColumns} rows={d?.tunnels.items ?? []} rowKey={(t) => t.id} maxHeight={360}
            emptyText="No VPN tunnels reported. Tunnels come from FortiGate and Palo Alto sources." />
        </Panel>
      </div>
    </>
  );
}

function InterfacesTab({ status, setStatus }: { status: InterfaceStatusFilter | null; setStatus: (s: InterfaceStatusFilter | null) => void }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<InterfaceSort>("status");
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ["network", "interfaces", status, q, sort, offset],
    queryFn: () => getNetworkInterfaces({ status, q, sort, limit: PAGE, offset }),
  });
  const total = list.data?.total ?? 0;
  return (
    <Panel title={`Interfaces (${total.toLocaleString("en-US")})`} info="Every interface the collectors report. Select one for its traffic history."
      aside={<SearchBox value={q} onChange={(v) => { setQ(v); setOffset(0); }} placeholder="Interface, description or device" label="Search interfaces" />}
      state={list.isLoading ? "loading" : "ready"}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        <Tabs<string> value={status ?? "all"} label="Interface status" onChange={(k) => { setStatus(k === "all" ? null : (k as InterfaceStatusFilter)); setOffset(0); }}
          tabs={[{ key: "all", label: "All" }, { key: "unexpected_down", label: "Down while enabled" }, { key: "down", label: "Down" }, { key: "up", label: "Up" }, { key: "admin_down", label: "Disabled" }]} />
        <Tabs<InterfaceSort> value={sort} label="Sort interfaces" onChange={(k) => { setSort(k); setOffset(0); }}
          tabs={[{ key: "status", label: "Problems first" }, { key: "utilization", label: "Utilization" }, { key: "throughput", label: "Throughput" }, { key: "name", label: "Name" }]} />
      </div>
      <DataTable caption="Network interfaces" columns={interfaceColumns} rows={list.data?.items ?? []} rowKey={(x) => x.id} rowHref={ifaceLink} maxHeight={620}
        emptyText={q || status ? "No interface matches." : "No interfaces collected yet."} />
      {total > PAGE && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
          <button type="button" className="pk-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span className="pk-muted">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <button type="button" className="pk-button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}
    </Panel>
  );
}

export default function NetworkPage() {
  const [tab, setTab] = useState<"overview" | "interfaces">("overview");
  const [status, setStatus] = useState<InterfaceStatusFilter | null>(null);
  return (
    <PageShell
      title="Network Monitoring"
      icon={Network}
      crumbs={[{ label: "Network Monitoring" }]}
      subtitle="Interface health and load, errors and VPN tunnels across your network devices"
      actions={<Tabs<"overview" | "interfaces"> value={tab} onChange={setTab} label="Network view" tabs={[{ key: "overview", label: "Overview" }, { key: "interfaces", label: "Interfaces" }]} />}
    >
      {tab === "overview"
        ? <OverviewTab onShow={(s) => { setStatus(s); setTab("interfaces"); }} />
        : <InterfacesTab status={status} setStatus={setStatus} />}
    </PageShell>
  );
}
