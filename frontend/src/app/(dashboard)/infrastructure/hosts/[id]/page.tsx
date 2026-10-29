"use client";

import Link from "next/link";
import { Suspense, useMemo } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity, AlertOctagon, AlertTriangle, ArrowRight, CheckCircle2, Cpu, Gauge, Info, Network, RefreshCw, Router, Server, Waves,
} from "lucide-react";
import { useHostDetail, useSensorHistory } from "@/hooks/useHostDetail";
import { ApiError, type Alert, type DeviceInterface, type Event, type SensorOut } from "@/lib/api";
import { buildHostVerdict, fmt, sensorFill, sensorState, seriesStats, type HostTab, type SensorState } from "@/lib/hostHealth";
import MetricChart from "@/components/infrastructure/MetricChart";
import {
  Banner, DataTable, PageShell, Panel, Pill, Tabs, ago, statusTone, titleCaseWords, type Column, type Tone,
} from "@/components/ui/PageKit";

/**
 * /infrastructure/hosts/[id] — the Host page: one entity, every signal about
 * it. A rule-based verdict explains why the host is in its state, a metric
 * chart shows any sensor against its limits, and tabs hold sensors, alerts,
 * interfaces, events and properties. Tab, sensor and window live in the URL.
 */

const WINDOWS = [
  { key: "6", label: "6 h" },
  { key: "24", label: "24 h" },
  { key: "168", label: "7 d" },
] as const;
type WindowKey = (typeof WINDOWS)[number]["key"];

const STATE_TONE: Record<SensorState, Tone> = { ok: "good", warning: "warn", critical: "bad", unknown: "neutral" };
const STATE_LABEL: Record<SensorState, string> = { ok: "Within limits", warning: "Warning", critical: "Critical", unknown: "No limits" };

function typeIcon(type: string) {
  const t = type.toLowerCase();
  if (/(router|switch|firewall|network|wlc|ap)/.test(t)) return Router;
  if (/(cpu|workstation|pc|desktop)/.test(t)) return Cpu;
  return Server;
}

function SensorGauge({ s }: { s: SensorOut }) {
  const st = sensorState(s);
  const fill = sensorFill(s);
  return (
    <span className="hd-gauge" title={STATE_LABEL[st]}>
      <span className="hd-gauge__value">
        {s.current_value === null ? "—" : fmt(s.current_value)}
        {s.current_value !== null && s.unit ? <small> {s.unit}</small> : null}
      </span>
      <span className="hd-gauge__track" aria-hidden="true">
        {fill !== null && <span className={`hd-gauge__fill hd-gauge__fill--${st}`} style={{ width: `${Math.max(3, fill * 100)}%` }} />}
      </span>
    </span>
  );
}

function HostContent() {
  const { id } = useParams<{ id: string }>();
  const rawParams = useSearchParams();
  const params = useMemo(() => rawParams ?? new URLSearchParams(), [rawParams]);
  const router = useRouter();
  const pathname = usePathname() ?? `/infrastructure/hosts/${id}`;
  const q = useHostDetail(id);
  const device = q.device.data;
  const sensors = useMemo(() => q.sensors.data ?? [], [q.sensors.data]);
  const alerts = useMemo(() => q.alerts.data ?? [], [q.alerts.data]);
  const interfaces = q.interfaces.data ?? [];
  const events = q.events.data ?? [];

  const tab = (params.get("tab") as HostTab | null) ?? "overview";
  const windowKey = (WINDOWS.find((w) => w.key === params.get("window"))?.key ?? "24") as WindowKey;
  const sensorParam = params.get("sensor");
  // Default chart sensor: the worst one, so the chart opens on what matters.
  const chartSensor = useMemo(() => {
    if (!sensors.length) return null;
    const pick = sensors.find((s) => s.id === sensorParam);
    if (pick) return pick;
    const rank: Record<SensorState, number> = { critical: 0, warning: 1, ok: 2, unknown: 3 };
    return [...sensors].sort((a, b) => rank[sensorState(a)] - rank[sensorState(b)])[0];
  }, [sensors, sensorParam]);
  const history = useSensorHistory(chartSensor?.id ?? null, Number(windowKey));
  const points = useMemo(
    () => (history.data?.points ?? []).map((m) => ({ t: new Date(m.recorded_at).getTime(), v: m.value })).sort((a, b) => a.t - b.t),
    [history.data],
  );
  const stats = seriesStats(points.map((p) => p.v));

  const verdict = useMemo(
    () => buildHostVerdict({ device: device ?? null, sensors, alerts, interfaces: q.interfaces.data ?? [] }),
    [device, sensors, alerts, q.interfaces.data],
  );

  function hrefWith(patch: Record<string, string | null>): string {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    const s = next.toString();
    return s ? `${pathname}?${s}` : pathname;
  }
  const go = (patch: Record<string, string | null>) => router.replace(hrefWith(patch), { scroll: false });

  const openAlerts = alerts.filter((a) => a.status !== "resolved");
  const openCrit = openAlerts.filter((a) => a.severity === "critical").length;
  const breaching = sensors.filter((s) => ["critical", "warning"].includes(sensorState(s))).length;

  if (q.device.isError && !device) {
    const err = q.device.error;
    const status = err instanceof ApiError ? err.status : 0;
    return (
      <PageShell title="Host" icon={Server} crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Hosts", href: "/infrastructure/hosts" }, { label: status === 404 ? "Not found" : "Unavailable" }]}>
        <Banner tone="warn">
          {status === 404
            ? "This host does not exist or is not part of your organization."
            : status === 429
              ? "Your plan's API allowance for this billing period is used up, so host data cannot be loaded. See Subscription to add capacity."
              : `Host could not be loaded${err?.message ? `: ${err.message}` : "."}`}
        </Banner>
        <p><Link className="ecc-link" href="/infrastructure/hosts">Back to all hosts</Link></p>
      </PageShell>
    );
  }

  const Icon = typeIcon(device?.device_type ?? "");
  const sensorColumns: Column<SensorOut>[] = [
    { key: "name", header: "Sensor", render: (s) => <span className="pk-cell-main">{s.name}</span>, sortValue: (s) => s.name },
    { key: "state", header: "State", render: (s) => <Pill tone={STATE_TONE[sensorState(s)]}>{STATE_LABEL[sensorState(s)]}</Pill>, sortValue: (s) => ["critical", "warning", "ok", "unknown"].indexOf(sensorState(s)) },
    { key: "value", header: "Current value", render: (s) => <SensorGauge s={s} />, sortValue: (s) => s.current_value },
    { key: "limits", header: "Warning / critical", render: (s) => <span className="pk-muted">{s.warning_threshold === null ? "—" : fmt(s.warning_threshold)} / {s.critical_threshold === null ? "—" : fmt(s.critical_threshold)}</span> },
    { key: "status", header: "Source status", render: (s) => <Pill tone={statusTone(s.status)}>{titleCaseWords(s.status)}</Pill> },
    { key: "last", header: "Last value", render: (s) => <span className="pk-muted">{ago(s.last_value_at)}</span>, sortValue: (s) => s.last_value_at },
  ];
  const alertColumns: Column<Alert>[] = [
    { key: "sev", header: "Severity", render: (a) => <Pill tone={a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "info"}>{titleCaseWords(a.severity)}</Pill>, sortValue: (a) => ["critical", "warning", "info"].indexOf(a.severity) },
    { key: "title", header: "Alert", render: (a) => <span><span className="pk-cell-main">{a.title}</span>{a.message && <span className="pk-cell-sub">{a.message}</span>}</span> },
    { key: "status", header: "Status", render: (a) => <Pill tone={statusTone(a.status)}>{titleCaseWords(a.status)}</Pill>, sortValue: (a) => a.status },
    { key: "count", header: "Occurrences", numeric: true, render: (a) => a.occurrence_count.toLocaleString("en-US"), sortValue: (a) => a.occurrence_count },
    { key: "first", header: "Triggered", render: (a) => <span className="pk-muted">{ago(a.triggered_at)}</span>, sortValue: (a) => a.triggered_at },
    { key: "last", header: "Last occurred", render: (a) => <span className="pk-muted">{ago(a.last_occurred_at)}</span>, sortValue: (a) => a.last_occurred_at },
  ];
  const ifColumns: Column<DeviceInterface>[] = [
    { key: "name", header: "Interface", render: (i) => <span><span className="pk-cell-main">{i.name}</span><span className="pk-cell-sub">{i.mac_address ?? "—"}</span></span>, sortValue: (i) => i.name },
    { key: "oper", header: "Operational", render: (i) => <Pill tone={statusTone(i.oper_status)}>{titleCaseWords(i.oper_status)}</Pill> },
    { key: "admin", header: "Admin", render: (i) => <span className="pk-muted">{titleCaseWords(i.admin_status)}</span> },
    { key: "speed", header: "Speed", numeric: true, render: (i) => (i.speed_mbps === null ? "—" : i.speed_mbps >= 1000 ? `${fmt(i.speed_mbps / 1000)} Gbps` : `${fmt(i.speed_mbps)} Mbps`), sortValue: (i) => i.speed_mbps },
    { key: "err", header: "Errors in / out", numeric: true, render: (i) => `${i.in_errors === null ? "—" : fmt(i.in_errors)} / ${i.out_errors === null ? "—" : fmt(i.out_errors)}` },
    { key: "chg", header: "Last change", render: (i) => <span className="pk-muted">{ago(i.last_status_change_at)}</span> },
  ];
  const eventColumns: Column<Event>[] = [
    { key: "sev", header: "Severity", render: (e) => <Pill tone={statusTone(e.severity)}>{titleCaseWords(e.severity)}</Pill> },
    { key: "type", header: "Type", render: (e) => titleCaseWords(e.event_type) },
    { key: "msg", header: "Message", render: (e) => e.message },
    { key: "at", header: "Occurred", render: (e) => <span className="pk-muted">{ago(e.occurred_at)}</span>, sortValue: (e) => e.occurred_at },
  ];

  const VerdictIcon = verdict.tone === "bad" ? AlertOctagon : verdict.tone === "warn" ? AlertTriangle : verdict.tone === "good" ? CheckCircle2 : Info;
  const tabs: { key: HostTab; label: string; count?: number }[] = [
    { key: "overview", label: "Overview" },
    { key: "sensors", label: "Sensors", count: q.sensors.data ? sensors.length : undefined },
    { key: "alerts", label: "Alerts", count: q.alerts.data ? openAlerts.length : undefined },
    { key: "interfaces", label: "Interfaces", count: q.interfaces.data ? interfaces.length : undefined },
    { key: "events", label: "Events", count: q.events.data ? events.length : undefined },
    { key: "info", label: "Properties" },
  ];

  return (
    <PageShell
      title={device?.name ?? "Host"}
      icon={Icon}
      subtitle={device ? [device.hostname && device.hostname !== device.name ? device.hostname : null, device.ip_address, titleCaseWords(device.device_type), [device.vendor, device.model].filter(Boolean).join(" ")].filter(Boolean).join(" · ") : "Loading host…"}
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Hosts", href: "/infrastructure/hosts" }, { label: device?.name ?? "…" }]}
      actions={
        <button type="button" className="pk-button" onClick={() => Object.values(q).forEach((x) => x.refetch())}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      }
      meta={device ? `Last seen ${ago(device.last_seen_at)}` : undefined}
    >
      {/* Signal chips — every chip jumps to the tab that explains it. */}
      <div className="hd-chips" role="list">
        <span role="listitem" className="hd-chip">{device ? <Pill tone={statusTone(device.status)}>{titleCaseWords(device.status)}</Pill> : "…"}</span>
        <Link role="listitem" className={`hd-chip${openCrit ? " hd-chip--bad" : ""}`} href={hrefWith({ tab: "alerts" })} scroll={false}>
          <AlertOctagon size={13} aria-hidden="true" /> <strong>{q.alerts.data ? openAlerts.length : "…"}</strong> open alerts{openCrit ? ` · ${openCrit} critical` : ""}
        </Link>
        <Link role="listitem" className={`hd-chip${breaching ? " hd-chip--warn" : ""}`} href={hrefWith({ tab: "sensors" })} scroll={false}>
          <Gauge size={13} aria-hidden="true" /> <strong>{q.sensors.data ? sensors.length : "…"}</strong> sensors{breaching ? ` · ${breaching} outside limits` : ""}
        </Link>
        <Link role="listitem" className="hd-chip" href={hrefWith({ tab: "interfaces" })} scroll={false}>
          <Network size={13} aria-hidden="true" /> <strong>{q.interfaces.data ? interfaces.length : "…"}</strong> interfaces
        </Link>
        <Link role="listitem" className="hd-chip" href={hrefWith({ tab: "events" })} scroll={false}>
          <Activity size={13} aria-hidden="true" /> <strong>{q.events.data ? events.length : "…"}</strong> events
        </Link>
      </div>

      <Tabs label="Host sections" value={tab} onChange={(k) => go({ tab: k === "overview" ? null : k })} tabs={tabs} />

      {tab === "overview" && (
        <div className="hd-grid">
          <div className="hd-col">
            <section className={`ecc-card hd-verdict hd-verdict--${verdict.tone}`} aria-label="Host verdict">
              <div className="hd-verdict__head">
                <span className="hd-verdict__icon" aria-hidden="true"><VerdictIcon size={18} /></span>
                <div>
                  <div className="hd-verdict__eyebrow">Why this host is in this state</div>
                  <h2 className="hd-verdict__title">{q.device.isLoading ? "Assessing…" : verdict.headline}</h2>
                </div>
              </div>
              {verdict.reasons.length > 0 ? (
                <ul className="hd-verdict__list">
                  {verdict.reasons.map((r) => (
                    <li key={r.id} className={`hd-reason hd-reason--${r.tone}`}>
                      <span>{r.text}</span>
                      <Link href={hrefWith({ tab: r.tab })} scroll={false} className="hd-reason__go" aria-label={`Open ${r.tab}`}>
                        {titleCaseWords(r.tab === "info" ? "properties" : r.tab)} <ArrowRight size={12} aria-hidden="true" />
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                !q.device.isLoading && <p className="hd-verdict__clear">{verdict.tone === "good" ? "No open alerts and every sensor is reporting inside its limits." : "This host has no sensors or alerts yet. Attach it to a monitoring source to start collecting."}</p>
              )}
            </section>

            <Panel
              title="Metric history"
              state={!q.sensors.data ? (q.sensors.isError ? "error" : "loading") : "ready"}
              onRetry={() => q.sensors.refetch()}
              skeletonHeight={260}
              aside={
                sensors.length > 0 ? (
                  <span className="hd-chart__controls">
                    <select aria-label="Sensor" value={chartSensor?.id ?? ""} onChange={(e) => go({ sensor: e.target.value })} className="hd-select">
                      {sensors.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                    <Tabs label="Window" value={windowKey} onChange={(k) => go({ window: k === "24" ? null : k })} tabs={WINDOWS.map((w) => ({ key: w.key, label: w.label }))} />
                  </span>
                ) : undefined
              }
            >
              {sensors.length === 0 ? (
                <p className="pk-muted" style={{ margin: 0 }}>No sensors on this host, so there is no metric history to chart.</p>
              ) : (
                <>
                  <div className="hd-stats">
                    {(["latest", "min", "avg", "max"] as const).map((k) => (
                      <div key={k}>
                        <span>{k === "avg" ? "Average" : titleCaseWords(k)}</span>
                        <strong>{stats ? `${fmt(stats[k])}${chartSensor?.unit ? ` ${chartSensor.unit}` : ""}` : "—"}</strong>
                      </div>
                    ))}
                    <div>
                      <span>Readings</span>
                      <strong>{stats ? stats.count.toLocaleString("en-US") : "—"}</strong>
                    </div>
                  </div>
                  {history.isLoading ? (
                    <div className="ecc-skeleton" style={{ height: 220 }} />
                  ) : history.isError ? (
                    <Banner tone="warn">Metric history could not be loaded.</Banner>
                  ) : (
                    <>
                    {history.data?.fallback && points.length > 0 && (
                      <p className="hd-chart__note">
                        No readings in the last {WINDOWS.find((w) => w.key === windowKey)?.label}. Showing the last {points.length.toLocaleString("en-US")} readings, the newest from {ago(new Date(points[points.length - 1].t).toISOString())}.
                      </p>
                    )}
                    <MetricChart points={points} unit={chartSensor?.unit ?? null} warning={chartSensor?.warning_threshold ?? null} critical={chartSensor?.critical_threshold ?? null} />
                    </>
                  )}
                </>
              )}
            </Panel>
          </div>

          <div className="hd-col">
            <Panel title="Sensors at a glance" state={q.sensors.data ? "ready" : q.sensors.isError ? "error" : "loading"} onRetry={() => q.sensors.refetch()} skeletonHeight={200}>
              {sensors.length === 0 ? (
                <p className="pk-muted" style={{ margin: 0 }}>No sensors.</p>
              ) : (
                <ul className="hd-sensors">
                  {sensors.slice(0, 8).map((s) => {
                    const st = sensorState(s);
                    return (
                      <li key={s.id}>
                        <Link href={hrefWith({ sensor: s.id, tab: null })} scroll={false} className={`hd-sensor${chartSensor?.id === s.id ? " hd-sensor--on" : ""}`}>
                          <span className={`hd-dot hd-dot--${st}`} aria-hidden="true" />
                          <span className="hd-sensor__name">{s.name}</span>
                          <SensorGauge s={s} />
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
              {sensors.length > 8 && <Link className="ecc-link" href={hrefWith({ tab: "sensors" })} scroll={false}>All {sensors.length} sensors</Link>}
            </Panel>

            <Panel title="Recent alerts" state={q.alerts.data ? "ready" : q.alerts.isError ? "error" : "loading"} onRetry={() => q.alerts.refetch()} skeletonHeight={200}>
              {alerts.length === 0 ? (
                <p className="pk-muted" style={{ margin: 0 }}>No alerts recorded for this host.</p>
              ) : (
                <ul className="hd-alerts">
                  {[...alerts].sort((a, b) => b.last_occurred_at.localeCompare(a.last_occurred_at)).slice(0, 6).map((a) => (
                    <li key={a.id} className={`hd-alert hd-alert--${a.status === "resolved" ? "resolved" : a.severity}`}>
                      <span className="hd-alert__title">{a.title}</span>
                      <span className="hd-alert__meta">
                        {titleCaseWords(a.status)} · {ago(a.last_occurred_at)}{a.occurrence_count > 1 ? ` · ×${a.occurrence_count.toLocaleString("en-US")}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {alerts.length > 6 && <Link className="ecc-link" href={hrefWith({ tab: "alerts" })} scroll={false}>All alerts</Link>}
            </Panel>
          </div>
        </div>
      )}

      {tab === "sensors" && (
        <Panel title="Sensors" state={q.sensors.data ? "ready" : q.sensors.isError ? "error" : "loading"} onRetry={() => q.sensors.refetch()} skeletonHeight={300}>
          <DataTable caption="Sensors" columns={sensorColumns} rows={sensors} rowKey={(s) => s.id} rowHref={(s) => hrefWith({ tab: null, sensor: s.id })} defaultSort={{ key: "state", dir: "asc" }} emptyText="No sensors on this host." />
        </Panel>
      )}
      {tab === "alerts" && (
        <Panel title="Alerts" aside={alerts.length >= 200 ? "Latest 200" : undefined} state={q.alerts.data ? "ready" : q.alerts.isError ? "error" : "loading"} onRetry={() => q.alerts.refetch()} skeletonHeight={300}>
          <DataTable caption="Alerts" columns={alertColumns} rows={alerts} rowKey={(a) => a.id} defaultSort={{ key: "last", dir: "desc" }} emptyText="No alerts recorded for this host." />
        </Panel>
      )}
      {tab === "interfaces" && (
        <Panel title="Interfaces" state={q.interfaces.data ? "ready" : q.interfaces.isError ? "error" : "loading"} onRetry={() => q.interfaces.refetch()} skeletonHeight={240}>
          <DataTable caption="Interfaces" columns={ifColumns} rows={interfaces} rowKey={(i) => i.id} emptyText="No interfaces discovered on this host." />
        </Panel>
      )}
      {tab === "events" && (
        <Panel title="Events" state={q.events.data ? "ready" : q.events.isError ? "error" : "loading"} onRetry={() => q.events.refetch()} skeletonHeight={240}>
          <DataTable caption="Events" columns={eventColumns} rows={events} rowKey={(e) => e.id} defaultSort={{ key: "at", dir: "desc" }} emptyText="No events recorded for this host." />
        </Panel>
      )}
      {tab === "info" && (
        <Panel title="Properties" state={device ? "ready" : "loading"} skeletonHeight={240}>
          {device && (
            <dl className="hd-props">
              {[
                ["Name", device.name],
                ["Hostname", device.hostname],
                ["IP address", device.ip_address],
                ["MAC address", device.mac_address],
                ["Type", titleCaseWords(device.device_type)],
                ["Vendor", device.vendor],
                ["Model", device.model],
                ["OS version", device.os_version],
                ["Serial number", device.serial_number],
                ["Status", titleCaseWords(device.status)],
                ["External ID", device.external_id],
                ["Last seen", device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : null],
                ["First registered", new Date(device.created_at).toLocaleString()],
                ["Updated", new Date(device.updated_at).toLocaleString()],
              ].map(([k, v]) => (
                <div key={k as string}>
                  <dt>{k}</dt>
                  <dd>{v ?? <span className="pk-muted">—</span>}</dd>
                </div>
              ))}
            </dl>
          )}
        </Panel>
      )}
      <p className="hd-foot"><Waves size={12} aria-hidden="true" /> Refreshes every minute.</p>
    </PageShell>
  );
}

export default function HostPage() {
  return (
    <Suspense fallback={<PageShell title="Host"><Panel title="Host" state="loading" skeletonHeight={420} /></PageShell>}>
      <HostContent />
    </Suspense>
  );
}
