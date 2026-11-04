"use client";

/**
 * Renderers for the dashboard widget registry (observability plan P6 slice 2).
 * Each widget reads one existing API with the viewer's own permissions; a 403
 * or 404 is shown as such in the widget, never as empty data.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { ApiError, getSensor, getSensorHistory } from "@/lib/api";
import type { DashboardWidget } from "@/lib/dashboards";
import { formatBps, getInterfaceHistory, getNetworkInterface, getNetworkOverview } from "@/lib/network";
import { getServerFleet } from "@/lib/serverFleet";
import { getSensorLibrary, getSensorRecommendations } from "@/lib/sensorLibrary";
import { BarList, LineChart, Pill, ago } from "@/components/ui/PageKit";

export const WIDGET_LABELS: Record<DashboardWidget["type"], string> = {
  network_summary: "Network summary",
  busiest_links: "Busiest links",
  interfaces_down: "Interfaces down while enabled",
  vpn_tunnels: "VPN tunnels",
  sensor_chart: "Sensor history",
  interface_chart: "Interface history",
  coverage_gaps: "Monitoring coverage gaps",
  server_fleet: "Server fleet",
  sensor_status: "Sensors by template",
};

const INTERFACE_CHANNEL_LABELS: Record<string, string> = {
  rx_bps: "Inbound traffic",
  tx_bps: "Outbound traffic",
  interface_utilization_pct: "Utilization",
  errors_in: "Inbound errors",
  errors_out: "Outbound errors",
};

function Note({ children }: { children: React.ReactNode }) {
  return <p className="pk-muted pk-empty-note">{children}</p>;
}

function failure(error: unknown): React.ReactNode {
  if (error instanceof ApiError && error.status === 403) return <Note>You do not have permission to see this data.</Note>;
  if (error instanceof ApiError && error.status === 404) return <Note>This item no longer exists.</Note>;
  return <Note>Could not load this widget{error instanceof Error && error.message ? `: ${error.message}` : "."}</Note>;
}

function Kpis({ items }: { items: [string, string | number, ("good" | "bad" | "warn" | "neutral" | null)?][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 10 }}>
      {items.map(([label, value, tone]) => (
        <div key={label}>
          <div className="pk-muted" style={{ fontSize: 12 }}>{label}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: tone === "bad" ? "var(--ecc-bad)" : tone === "warn" ? "var(--ecc-warn)" : undefined }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

function NetworkWidget({ widget }: { widget: DashboardWidget }) {
  const q = useQuery({ queryKey: ["network", "overview"], queryFn: () => getNetworkOverview(), refetchInterval: 60_000, retry: false });
  if (q.isLoading) return <Note>Loading…</Note>;
  if (q.isError || !q.data) return failure(q.error);
  const d = q.data;
  const i = d.interfaces;
  switch (widget.type) {
    case "network_summary":
      return (
        <Kpis items={[
          ["Interfaces", i.total],
          ["Up", i.up],
          ["Down while enabled", i.unexpected_down, i.unexpected_down > 0 ? "bad" : null],
          ["Current traffic", `${i.measured} / ${i.total}`],
          ["VPN down", `${d.tunnels.down} / ${d.tunnels.total}`, d.tunnels.down > 0 ? "bad" : null],
        ]} />
      );
    case "busiest_links": {
      const limit = Number(widget.config.limit ?? 5);
      return (
        <BarList
          rows={d.top_utilization.slice(0, limit).map((x) => ({ key: x.id, label: `${x.device_name} · ${x.name}`, value: x.utilization_pct ?? 0, href: `/network/interfaces/${x.id}` }))}
          valueLabel={(v) => `${v.toFixed(1)} %`}
          emptyText="No current utilization measured."
        />
      );
    }
    case "interfaces_down":
      return d.unexpected_down.length === 0 ? <Note>Every enabled interface is up.</Note> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {d.unexpected_down.slice(0, 8).map((x) => (
            <li key={x.id} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <Link className="ecc-link" href={`/network/interfaces/${x.id}`}>{x.device_name} · {x.name}</Link>
              <span className="pk-muted">{x.last_status_change_at ? ago(x.last_status_change_at) : ""}</span>
            </li>
          ))}
        </ul>
      );
    case "vpn_tunnels":
      return d.tunnels.total === 0 ? <Note>No VPN tunnels reported.</Note> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {d.tunnels.items.slice(0, 8).map((t) => (
            <li key={t.id} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span>{t.device_name} · {t.name}</span>
              <Pill tone={t.status === "up" ? "good" : "bad"}>{t.status}</Pill>
            </li>
          ))}
        </ul>
      );
    default:
      return null;
  }
}

function SensorChartWidget({ widget }: { widget: DashboardWidget }) {
  const id = String(widget.config.sensor_id ?? "");
  const channel = String(widget.config.channel ?? "primary");
  const hours = Number(widget.config.hours ?? 24);
  const sensor = useQuery({ queryKey: ["sensor", id], queryFn: () => getSensor(id), retry: false });
  const history = useQuery({ queryKey: ["sensor", id, "history", channel, hours], queryFn: () => getSensorHistory(id, channel, hours), retry: false, refetchInterval: 60_000 });
  if (sensor.isError) return failure(sensor.error);
  if (history.isError) return failure(history.error);
  const unit = history.data?.unit ?? sensor.data?.unit ?? null;
  return (
    <>
      {sensor.data && (
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <Link className="ecc-link" href={`/monitoring/sensors/${id}`}>{sensor.data.name}{channel !== "primary" ? ` · ${channel}` : ""}</Link>
          <span className="pk-muted">{sensor.data.device?.name ?? ""}</span>
        </div>
      )}
      {history.data ? (
        <LineChart
          label={`${sensor.data?.name ?? "Sensor"} history`}
          points={history.data.points.filter((p) => p.avg !== null).map((p) => ({ t: Date.parse(p.t), value: p.avg as number, min: p.min, max: p.max }))}
          format={(v) => `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`}
          gapMs={history.data.bucket_seconds * 3000}
          height={160}
        />
      ) : <Note>Loading…</Note>}
    </>
  );
}

function InterfaceChartWidget({ widget }: { widget: DashboardWidget }) {
  const id = String(widget.config.interface_id ?? "");
  const channel = String(widget.config.channel ?? "rx_bps") as keyof typeof INTERFACE_CHANNEL_LABELS;
  const hours = Number(widget.config.hours ?? 24);
  const iface = useQuery({ queryKey: ["network", "interface", id], queryFn: () => getNetworkInterface(id), retry: false });
  const history = useQuery({ queryKey: ["network", "interface", id, "history", hours], queryFn: () => getInterfaceHistory(id, hours), retry: false, refetchInterval: 60_000 });
  if (iface.isError) return failure(iface.error);
  if (history.isError) return failure(history.error);
  const format = channel === "rx_bps" || channel === "tx_bps" ? (v: number) => formatBps(v)
    : channel === "interface_utilization_pct" ? (v: number) => `${v.toFixed(1)} %` : (v: number) => `${v.toFixed(2)} /s`;
  const series = history.data?.series[channel as "rx_bps"] ?? [];
  return (
    <>
      {iface.data && (
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <Link className="ecc-link" href={`/network/interfaces/${id}`}>{iface.data.device_name} · {iface.data.name}</Link>
          <span className="pk-muted">{INTERFACE_CHANNEL_LABELS[channel]}</span>
        </div>
      )}
      {history.data ? (
        <LineChart
          label={`${iface.data?.name ?? "Interface"} ${INTERFACE_CHANNEL_LABELS[channel]}`}
          points={series.map((p) => ({ t: Date.parse(p.t), value: p.avg, max: p.max }))}
          format={format}
          gapMs={history.data.bucket_seconds * 3000}
          height={160}
        />
      ) : <Note>Loading…</Note>}
    </>
  );
}

function CoverageWidget() {
  const q = useQuery({ queryKey: ["sensor-recommendations", null, 0], queryFn: () => getSensorRecommendations(null, 1, 0), retry: false });
  if (q.isLoading) return <Note>Loading…</Note>;
  if (q.isError || !q.data) return failure(q.error);
  return (
    <>
      <BarList rows={q.data.rules.map((r) => ({ key: r.key, label: r.title, value: r.hosts, href: "/monitoring/library" }))} valueLabel={(v) => `${v} hosts`} emptyText="No coverage data." />
    </>
  );
}

function FleetWidget() {
  const q = useQuery({ queryKey: ["server-fleet", "summary"], queryFn: () => getServerFleet(), retry: false, refetchInterval: 60_000 });
  if (q.isLoading) return <Note>Loading…</Note>;
  if (q.isError || !q.data) return failure(q.error);
  const s = q.data.summary;
  if (s.servers === 0) return <Note>No servers report host resources.</Note>;
  return (
    <Kpis items={[
      ["Servers", s.servers],
      ["Critical", s.critical, s.critical > 0 ? "bad" : null],
      ["Warning", s.warning, s.warning > 0 ? "warn" : null],
      ["Stale", s.stale, s.stale > 0 ? "warn" : null],
      ["Disk over 90 %", s.disk_over_90, s.disk_over_90 > 0 ? "bad" : null],
      ["CPU over 90 %", s.cpu_over_90, s.cpu_over_90 > 0 ? "bad" : null],
    ]} />
  );
}

function SensorStatusWidget() {
  const q = useQuery({ queryKey: ["sensor-library"], queryFn: () => getSensorLibrary(), retry: false });
  if (q.isLoading) return <Note>Loading…</Note>;
  if (q.isError || !q.data) return failure(q.error);
  const rows = q.data.templates.filter((t) => t.sensors > 0).map((t) => ({
    key: t.key,
    label: `${t.name}${t.by_status.down ? ` · ${t.by_status.down} down` : ""}`,
    value: t.sensors,
    href: "/monitoring/library",
  }));
  return <BarList rows={rows} valueLabel={(v) => `${v}`} emptyText="No sensors reported yet." />;
}

export function WidgetBody({ widget }: { widget: DashboardWidget }) {
  switch (widget.type) {
    case "network_summary":
    case "busiest_links":
    case "interfaces_down":
    case "vpn_tunnels":
      return <NetworkWidget widget={widget} />;
    case "sensor_chart":
      return <SensorChartWidget widget={widget} />;
    case "interface_chart":
      return <InterfaceChartWidget widget={widget} />;
    case "coverage_gaps":
      return <CoverageWidget />;
    case "server_fleet":
      return <FleetWidget />;
    case "sensor_status":
      return <SensorStatusWidget />;
    default:
      return <Note>This widget type is not available.</Note>;
  }
}
