"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Network } from "lucide-react";
import { ApiError } from "@/lib/api";
import { formatBps, getInterfaceHistory, getNetworkInterface, type InterfaceHistory } from "@/lib/network";
import { Banner, LineChart, PageShell, Panel, Pill, StatStrip, StatTile, Tabs, ago, titleCaseWords } from "@/components/ui/PageKit";

/**
 * /network/interfaces/[id] — one interface: state, speed and current load,
 * and its recorded history (inbound and outbound traffic, utilization,
 * errors) over a chosen window. History exists from the first poll pair the
 * platform measured; older periods are honestly empty.
 */

const WINDOWS = [
  { hours: 1, label: "1 h" },
  { hours: 24, label: "24 h" },
  { hours: 168, label: "7 d" },
  { hours: 720, label: "30 d" },
];

type Channel = keyof InterfaceHistory["series"];

function points(h: InterfaceHistory | undefined, channel: Channel) {
  return (h?.series[channel] ?? []).map((p) => ({ t: Date.parse(p.t), value: p.avg, max: p.max }));
}

export default function InterfacePage() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const [hours, setHours] = useState(24);
  const iface = useQuery({ queryKey: ["network", "interface", id], queryFn: () => getNetworkInterface(id), enabled: !!id, retry: false, refetchInterval: 60_000 });
  const history = useQuery({ queryKey: ["network", "interface", id, "history", hours], queryFn: () => getInterfaceHistory(id, hours), enabled: !!iface.data, refetchInterval: 60_000 });
  const i = iface.data;
  const crumbs = (last: string) => [{ label: "Network Monitoring", href: "/network" }, { label: last }];

  if (iface.isError) {
    const notFound = iface.error instanceof ApiError && iface.error.status === 404;
    return (
      <PageShell title="Interface" icon={Network} crumbs={crumbs(notFound ? "Not found" : "Unavailable")}>
        <Banner tone="warn">{notFound ? "This interface does not exist or is not part of your organization." : `The interface could not be loaded${iface.error?.message ? `: ${iface.error.message}` : "."}`}</Banner>
        <p><Link className="ecc-link" href="/network">Back to Network Monitoring</Link></p>
      </PageShell>
    );
  }

  const recorded = i ? Object.values(i.history).reduce((n, c) => n + c.samples, 0) : 0;
  const gap = history.data ? history.data.bucket_seconds * 1000 * 3 : undefined;
  const state = !i ? null : i.admin_status === "down" ? { tone: "neutral" as const, label: "Disabled" }
    : i.oper_status === "up" ? { tone: "good" as const, label: "Up" }
    : i.oper_status === "down" ? { tone: "bad" as const, label: "Down" } : { tone: "warn" as const, label: titleCaseWords(i.oper_status) };

  return (
    <PageShell
      title={i ? `${i.device_name} · ${i.name}` : "Interface"}
      icon={Network}
      crumbs={crumbs(i?.name ?? "Interface")}
      subtitle={i ? [i.description, i.interface_type && i.interface_type !== "unknown" && titleCaseWords(i.interface_type), i.speed_mbps !== null ? formatBps(i.speed_mbps * 1e6) : null].filter(Boolean).join(" · ") : undefined}
      meta={state ? <Pill tone={state.tone}>{state.label}</Pill> : undefined}
      actions={<Tabs<string> value={String(hours)} onChange={(h) => setHours(Number(h))} label="Time window" tabs={WINDOWS.map((w) => ({ key: String(w.hours), label: w.label }))} />}
    >
      {i && !i.fresh && (
        <Banner tone="warn">{i.last_telemetry_at ? `The last counter sample is from ${ago(i.last_telemetry_at)}, so no current traffic is shown.` : "No counter sample has been collected for this interface."}</Banner>
      )}
      {i && i.admin_status === "up" && i.oper_status === "down" && (
        <Banner tone="bad">Enabled but down{i.last_status_change_at ? ` since ${ago(i.last_status_change_at)}` : ""}: check the cable, the peer port or the transceiver.</Banner>
      )}
      <StatStrip>
        <StatTile label="In" value={i?.fresh ? formatBps(i.rx_bps) : "—"} info="Inbound traffic from the last two counter samples." loading={iface.isLoading} naText="Not measured" />
        <StatTile label="Out" value={i?.fresh ? formatBps(i.tx_bps) : "—"} info="Outbound traffic from the last two counter samples." loading={iface.isLoading} naText="Not measured" />
        <StatTile label="Utilization" value={i?.fresh && i.utilization_pct !== null ? `${i.utilization_pct.toFixed(1)} %` : "—"} info="Busiest direction against the interface speed." loading={iface.isLoading} />
        <StatTile label="Errors (counters)" value={i ? `${(i.in_errors ?? 0).toLocaleString("en-US")} / ${(i.out_errors ?? 0).toLocaleString("en-US")}` : "—"} sub="in / out since the counters started" info="Cumulative error counters as the device reports them." loading={iface.isLoading} />
      </StatStrip>
      {i && recorded === 0 && (
        <Banner tone="info">No history recorded for this interface yet. History starts with the first two polls after this release that both carry counters.</Banner>
      )}
      <div className="pk-grid">
        {([
          ["rx_bps", "Inbound traffic", (v: number) => formatBps(v)],
          ["tx_bps", "Outbound traffic", (v: number) => formatBps(v)],
          ["interface_utilization_pct", "Utilization", (v: number) => `${v.toFixed(1)} %`],
          ["errors_in", "Inbound errors", (v: number) => `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })} /s`],
        ] as [Channel, string, (v: number) => string][]).map(([channel, title, format]) => (
          <Panel key={channel} title={title} info="Average per time bucket; the shaded band reaches the bucket's peak." state={history.isLoading ? "loading" : "ready"} className="pk-col-6">
            <LineChart label={`${title} history`} points={points(history.data, channel)} format={format} gapMs={gap} height={180} />
          </Panel>
        ))}
        <Panel title="Details" state="ready" className="pk-col-12">
          {i && (
            <dl className="hd-props">
              {([
                ["Device", <Link key="d" className="ecc-link" href={`/infrastructure/hosts/${i.device_id}`}>{i.device_name}</Link>],
                ["Admin / oper status", `${titleCaseWords(i.admin_status)} / ${titleCaseWords(i.oper_status)}`],
                ["Speed", i.speed_mbps !== null ? formatBps(i.speed_mbps * 1e6) : null],
                ["MAC address", i.mac_address],
                ["VLAN", i.vlan_id !== null ? String(i.vlan_id) : null],
                ["Last status change", i.last_status_change_at ? ago(i.last_status_change_at) : null],
                ["Last counter sample", i.last_telemetry_at ? ago(i.last_telemetry_at) : null],
              ] as [string, React.ReactNode][]).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v ?? <span className="pk-muted">—</span>}</dd></div>
              ))}
            </dl>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}
