"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { BellRing } from "lucide-react";
import { getAlertSummary, listAlerts, type Alert } from "@/lib/api";
import { DonutRing, Legend } from "@/components/command-center/primitives";
import {
  Banner,
  BarList,
  DataTable,
  DayColumns,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  ago,
  dayBuckets,
  type Column,
} from "@/components/ui/PageKit";

/** Most recent alerts sampled for the breakdowns (the API caps a page at 200). */
const SAMPLE = 200;
const SEVERITY = [
  { key: "critical", label: "Critical", color: "var(--ecc-bad)" },
  { key: "warning", label: "Warning", color: "var(--ecc-warn)" },
  { key: "info", label: "Info", color: "var(--ecc-accent)" },
] as const;
const STATUS = [
  { key: "open", label: "Open", color: "var(--ecc-bad)" },
  { key: "acknowledged", label: "Acknowledged", color: "var(--ecc-warn)" },
  { key: "resolved", label: "Resolved", color: "var(--ecc-good)" },
] as const;

const fmt = (n: number | undefined) => (n === undefined ? "—" : n.toLocaleString("en-US"));
const pct = (n: number, total: number) => (total > 0 ? `${Math.round((n / total) * 100)}% of all alerts` : undefined);

function topBy(alerts: Alert[], key: (a: Alert) => string | null, n = 8) {
  const counts = new Map<string, number>();
  for (const a of alerts) {
    const k = key(a);
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
}

export default function AlertAnalysisPage() {
  const summary = useQuery({ queryKey: ["reports", "alert-summary"], queryFn: () => getAlertSummary(), refetchInterval: 60_000 });
  const sample = useQuery({ queryKey: ["reports", "alert-sample"], queryFn: () => listAlerts({ limit: SAMPLE }), refetchInterval: 60_000 });

  const s = summary.data;
  const alerts = useMemo(() => sample.data ?? [], [sample.data]);
  const total = s?.total ?? 0;
  const sampleState = sample.isLoading ? "loading" : sample.error ? "error" : alerts.length === 0 ? "empty" : "ready";
  const summaryState = summary.isLoading ? "loading" : summary.error ? "error" : total === 0 ? "empty" : "ready";

  const newest = alerts.reduce<string | null>((m, a) => (!m || a.last_occurred_at > m ? a.last_occurred_at : m), null);
  // Measured against when the sample was fetched, not the render-time clock.
  const quietHours = newest && sample.dataUpdatedAt ? (sample.dataUpdatedAt - new Date(newest).getTime()) / 3_600_000 : null;

  // The window stretches to reach the sampled alerts (14-90 days), so a quiet
  // month shows the last real activity instead of an all-zero chart.
  const oldest = alerts.reduce<string | null>((m, a) => (!m || a.last_occurred_at < m ? a.last_occurred_at : m), null);
  const spanDays = oldest && sample.dataUpdatedAt ? Math.ceil((sample.dataUpdatedAt - new Date(oldest).getTime()) / 86_400_000) + 1 : 30;
  const activityDays = Math.min(90, Math.max(14, spanDays));
  const activity = useMemo(() => dayBuckets(alerts, (a) => a.last_occurred_at, (a) => a.severity, activityDays), [alerts, activityDays]);
  const bySource = topBy(alerts, (a) => a.source);
  const byDevice = topBy(alerts, (a) => a.device_name);
  const noisiest = useMemo(() => [...alerts].sort((a, b) => b.occurrence_count - a.occurrence_count).slice(0, 25), [alerts]);

  const columns: Column<Alert>[] = [
    {
      key: "severity",
      header: "Severity",
      sortValue: (a) => SEVERITY.findIndex((x) => x.key === a.severity),
      render: (a) => <Pill tone={a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "info"}>{a.severity}</Pill>,
    },
    {
      key: "alert",
      header: "Alert",
      sortValue: (a) => a.title,
      render: (a) => (
        <>
          <span className="pk-cell-main">{a.title}</span>
          <span className="pk-cell-sub pk-truncate" title={a.message ?? undefined}>{a.message ?? a.sensor_name ?? "—"}</span>
        </>
      ),
    },
    { key: "device", header: "Device", sortValue: (a) => a.device_name, render: (a) => a.device_name ?? <span className="pk-muted">—</span> },
    { key: "status", header: "Status", sortValue: (a) => a.status, render: (a) => <Pill tone={a.status === "resolved" ? "good" : a.status === "acknowledged" ? "warn" : "bad"}>{a.status}</Pill> },
    { key: "count", header: "Occurrences", numeric: true, sortValue: (a) => a.occurrence_count, render: (a) => a.occurrence_count.toLocaleString("en-US") },
    { key: "last", header: "Last seen", sortValue: (a) => a.last_occurred_at, render: (a) => <span className="pk-muted">{ago(a.last_occurred_at)}</span> },
  ];

  return (
    <PageShell
      title="Alert Analysis"
      icon={BellRing}
      crumbs={[{ label: "Reports", href: "/reports" }, { label: "Alert Analysis" }]}
      subtitle="Where alert volume comes from, how severe it is and how much of it is still open."
      meta={sample.dataUpdatedAt ? `Updated ${ago(new Date(sample.dataUpdatedAt).toISOString())}` : undefined}
    >
      {quietHours !== null && quietHours > 48 && (
        <Banner tone="warn">
          The last alert fired {ago(newest)} ({new Date(newest!).toLocaleString()}). Quiet can mean healthy, but with failing
          collectors it usually means nothing is being collected. Check Collector Performance.
        </Banner>
      )}

      <StatStrip>
        <StatTile label="Total alerts" value={fmt(s?.total)} info="Every alert stored for this tenant, all time." loading={summary.isLoading} />
        <StatTile label="Critical" value={fmt(s?.by_severity.critical)} alarm={(s?.by_severity.critical ?? 0) > 0} sub={s ? pct(s.by_severity.critical, total) : undefined} info="Alerts raised at critical severity." loading={summary.isLoading} />
        <StatTile label="Warning" value={fmt(s?.by_severity.warning)} tone="warn" sub={s ? pct(s.by_severity.warning, total) : undefined} info="Alerts raised at warning severity." loading={summary.isLoading} />
        <StatTile label="Info" value={fmt(s?.by_severity.info)} sub={s ? pct(s.by_severity.info, total) : undefined} info="Informational alerts." loading={summary.isLoading} />
        <StatTile label="Open" value={fmt(s?.by_status.open)} alarm={(s?.by_status.open ?? 0) > 0} info="Alerts nobody has acknowledged or resolved." loading={summary.isLoading} />
        <StatTile label="Acknowledged" value={fmt(s?.by_status.acknowledged)} tone="warn" info="Alerts someone has taken ownership of." loading={summary.isLoading} />
        <StatTile label="Resolved" value={fmt(s?.by_status.resolved)} tone="good" sub={s ? pct(s.by_status.resolved, total) : undefined} info="Alerts closed manually or auto-resolved when the condition cleared." loading={summary.isLoading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel title="By severity" info="All alerts by severity." state={summaryState} className="pk-col-4" emptyText="No alerts stored yet.">
          {s && (
            <div className="pk-donut-row">
              <DonutRing size={132} label="Alerts by severity" centerValue={fmt(total)} centerLabel="alerts" segments={SEVERITY.map((x) => ({ key: x.key, value: s.by_severity[x.key], color: x.color }))} />
              <Legend total={total} rows={SEVERITY.map((x) => ({ key: x.key, label: x.label, value: s.by_severity[x.key], color: x.color, href: `/alerts?severity=${x.key}` }))} />
            </div>
          )}
        </Panel>
        <Panel title="By status" info="All alerts by lifecycle status." state={summaryState} className="pk-col-4" emptyText="No alerts stored yet.">
          {s && (
            <div className="pk-donut-row">
              <DonutRing size={132} label="Alerts by status" centerValue={fmt(s.by_status.open)} centerLabel="open" segments={STATUS.map((x) => ({ key: x.key, value: s.by_status[x.key], color: x.color }))} />
              <Legend total={total} rows={STATUS.map((x) => ({ key: x.key, label: x.label, value: s.by_status[x.key], color: x.color }))} />
            </div>
          )}
        </Panel>
        <Panel title="By source" info={`Which integration raised the alerts, across the ${SAMPLE} most recently active alerts.`} aside={`latest ${alerts.length}`} state={sampleState} className="pk-col-4">
          <BarList rows={bySource.map(([k, v]) => ({ key: k, label: k.replace(/_/g, " "), value: v }))} />
        </Panel>

        <Panel title="Alert activity" info={`Last occurrence per day, stacked by severity, for the ${SAMPLE} most recently active alerts. The window widens (up to 90 days) to reach them.`} aside={`last ${activityDays} days`} state={sampleState} className="pk-col-8">
          <DayColumns label="Alert activity per day" buckets={activity} series={SEVERITY.map((x) => ({ key: x.key, label: x.label, color: x.color }))} height={160} />
        </Panel>
        <Panel title="Top devices" info={`Devices with the most distinct alerts, across the ${SAMPLE} most recently active alerts.`} aside={`latest ${alerts.length}`} state={sampleState} className="pk-col-4">
          <BarList rows={byDevice.map(([k, v]) => ({ key: k, label: k, value: v, color: "var(--ecc-warn)" }))} emptyText="No alert is linked to a device." />
        </Panel>

        <Panel title="Noisiest alerts" info="Alerts that fired most often. A high occurrence count on one alert usually means a threshold or polling problem worth tuning." aside={`top ${noisiest.length} by occurrences`} state={sampleState} className="pk-col-12">
          <DataTable caption="Noisiest alerts" columns={columns} rows={noisiest} rowKey={(a) => a.id} defaultSort={{ key: "count", dir: "desc" }} maxHeight={520} />
        </Panel>
      </div>
    </PageShell>
  );
}
