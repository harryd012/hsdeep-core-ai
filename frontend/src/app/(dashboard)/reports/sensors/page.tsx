"use client";

import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

import { downloadCsv } from "@/lib/csv";
import { getSensorReport, type SensorRow } from "@/lib/observabilityReports";
import ReportPeriod, { usePeriod } from "@/components/reports/ReportPeriod";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, statusTone, titleCaseWords, type Column } from "@/components/ui/PageKit";

/**
 * /reports/sensors — per-sensor reading statistics over a period and how long
 * its status alerts were open within it (time outside the period is not counted).
 */

const num = (v: number | null, unit: string | null) =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;

function duration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} h`;
  return `${(minutes / 1440).toFixed(1)} d`;
}

const COLUMNS: Column<SensorRow>[] = [
  { key: "sensor", header: "Sensor", sortValue: (r) => r.sensor, render: (r) => <><strong>{r.sensor}</strong><div className="pk-muted">{r.device ?? "—"}</div></> },
  { key: "type", header: "Type", sortValue: (r) => r.metric_type, render: (r) => titleCaseWords(r.metric_type) },
  { key: "status", header: "Now", sortValue: (r) => r.status, render: (r) => <Pill tone={statusTone(r.status)}>{titleCaseWords(r.status)}</Pill> },
  { key: "avg", header: "Average", numeric: true, sortValue: (r) => r.avg, render: (r) => num(r.avg, r.unit) },
  { key: "p95", header: "p95", numeric: true, sortValue: (r) => r.p95, render: (r) => num(r.p95, r.unit) },
  { key: "max", header: "Max", numeric: true, sortValue: (r) => r.max, render: (r) => num(r.max, r.unit) },
  { key: "samples", header: "Readings", numeric: true, sortValue: (r) => r.samples, render: (r) => r.samples.toLocaleString("en-US") },
  { key: "alerts", header: "Alerts", numeric: true, sortValue: (r) => r.alerts, render: (r) => r.alerts },
  { key: "time", header: "Time in alert", numeric: true, sortValue: (r) => r.alert_minutes, render: (r) => (r.alert_minutes ? duration(r.alert_minutes) : <span className="pk-muted">0</span>) },
];

export default function SensorReportPage() {
  const [period, setPeriod] = usePeriod(7);
  const q = useQuery({ queryKey: ["reports", "sensors", period], queryFn: () => getSensorReport(period) });
  const d = q.data;
  const s = d?.summary;

  return (
    <PageShell title="Sensor Performance" icon={Gauge} crumbs={[{ label: "Reports", href: "/reports" }, { label: "Sensor Performance" }]}
      subtitle="Reading statistics and time in alert per sensor"
      actions={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <ReportPeriod onChange={setPeriod} />
          <button type="button" className="pk-button" disabled={!d?.items.length}
            onClick={() => d && downloadCsv(`sensors-${d.since.slice(0, 10)}-${d.until.slice(0, 10)}.csv`, [
              { header: "Sensor", value: (r: SensorRow) => r.sensor },
              { header: "Device", value: (r) => r.device },
              { header: "Type", value: (r) => r.metric_type },
              { header: "Unit", value: (r) => r.unit },
              { header: "Average", value: (r) => r.avg },
              { header: "Min", value: (r) => r.min },
              { header: "Max", value: (r) => r.max },
              { header: "p95", value: (r) => r.p95 },
              { header: "Readings", value: (r) => r.samples },
              { header: "Alerts", value: (r) => r.alerts },
              { header: "Minutes in alert", value: (r) => r.alert_minutes },
            ], d.items)}>
            Export CSV
          </button>
        </div>
      }>
      {q.isError && <Banner tone="bad">The report could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      <StatStrip>
        <StatTile label="Sensors" value={s ? String(s.sensors) : "—"} sub={s ? `${s.with_readings} with readings` : undefined} info="Sensors with readings or alerts in the period." loading={q.isLoading} />
        <StatTile label="With alerts" value={s ? String(s.with_alerts) : "—"} tone={s && s.with_alerts > 0 ? "warn" : "good"} info="Sensors whose status alert was open at some point in the period." loading={q.isLoading} />
        <StatTile label="Time in alert" value={s ? duration(s.alert_minutes) : "—"} info="Sum over sensors of the time their alerts were open within the period." loading={q.isLoading} />
      </StatStrip>
      <Panel title={`Sensors (${d?.items.length ?? 0})`} info="Most time in alert first. Statistics are over the sensor's recorded readings in the period." state={q.isLoading ? "loading" : "ready"}>
        <DataTable caption="Sensor performance" columns={COLUMNS} rows={d?.items ?? []} rowKey={(r) => r.sensor_id}
          rowHref={(r) => `/monitoring/sensors/${r.sensor_id}`} emptyText="No sensor reading or alert in this period." pageSize={100} />
      </Panel>
    </PageShell>
  );
}
