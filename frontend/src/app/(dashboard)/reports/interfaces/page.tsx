"use client";

import { useQuery } from "@tanstack/react-query";
import { Network } from "lucide-react";

import { downloadCsv } from "@/lib/csv";
import { formatBps } from "@/lib/network";
import { getInterfaceReport, type InterfaceRow } from "@/lib/observabilityReports";
import ReportPeriod, { usePeriod } from "@/components/reports/ReportPeriod";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, type Column } from "@/components/ui/PageKit";

/**
 * /reports/interfaces — per-interface traffic, utilisation and error rates
 * over a period, from the interface history recorded since P4.
 */

const pct = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)} %`);
const rate = (v: number | null) => (v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })} /s`);

const COLUMNS: Column<InterfaceRow>[] = [
  { key: "name", header: "Interface", sortValue: (r) => `${r.device} ${r.interface}`, render: (r) => <><strong>{r.interface}</strong><div className="pk-muted">{r.device}</div></> },
  { key: "speed", header: "Speed", numeric: true, sortValue: (r) => r.speed_mbps, render: (r) => (r.speed_mbps ? formatBps(r.speed_mbps * 1e6) : "—") },
  { key: "in", header: "In avg / peak", numeric: true, sortValue: (r) => r.rx_peak_bps, render: (r) => `${formatBps(r.rx_avg_bps)} / ${formatBps(r.rx_peak_bps)}` },
  { key: "out", header: "Out avg / peak", numeric: true, sortValue: (r) => r.tx_peak_bps, render: (r) => `${formatBps(r.tx_avg_bps)} / ${formatBps(r.tx_peak_bps)}` },
  { key: "util", header: "Utilization avg / peak", numeric: true, sortValue: (r) => r.utilization_peak_pct,
    render: (r) => (r.utilization_peak_pct !== null && r.utilization_peak_pct >= 80
      ? <Pill tone="warn">{pct(r.utilization_avg_pct)} / {pct(r.utilization_peak_pct)}</Pill> : `${pct(r.utilization_avg_pct)} / ${pct(r.utilization_peak_pct)}`) },
  { key: "errs", header: "Errors peak in / out", numeric: true, sortValue: (r) => Math.max(r.errors_in_peak_per_s ?? 0, r.errors_out_peak_per_s ?? 0),
    render: (r) => `${rate(r.errors_in_peak_per_s)} / ${rate(r.errors_out_peak_per_s)}` },
  { key: "samples", header: "Polls", numeric: true, sortValue: (r) => r.samples, render: (r) => r.samples },
];

export default function InterfaceReportPage() {
  const [period, setPeriod] = usePeriod(7);
  const q = useQuery({ queryKey: ["reports", "interfaces", period], queryFn: () => getInterfaceReport(period) });
  const d = q.data;
  const s = d?.summary;

  return (
    <PageShell title="Interface Traffic" icon={Network} crumbs={[{ label: "Reports", href: "/reports" }, { label: "Interface Traffic" }]}
      subtitle="Traffic, utilization and errors per interface"
      actions={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <ReportPeriod onChange={setPeriod} />
          <button type="button" className="pk-button" disabled={!d?.items.length}
            onClick={() => d && downloadCsv(`interfaces-${d.since.slice(0, 10)}-${d.until.slice(0, 10)}.csv`, [
              { header: "Device", value: (r: InterfaceRow) => r.device },
              { header: "Interface", value: (r) => r.interface },
              { header: "Speed (Mbps)", value: (r) => r.speed_mbps },
              { header: "In avg (bps)", value: (r) => r.rx_avg_bps },
              { header: "In peak (bps)", value: (r) => r.rx_peak_bps },
              { header: "Out avg (bps)", value: (r) => r.tx_avg_bps },
              { header: "Out peak (bps)", value: (r) => r.tx_peak_bps },
              { header: "Utilization avg %", value: (r) => r.utilization_avg_pct },
              { header: "Utilization peak %", value: (r) => r.utilization_peak_pct },
              { header: "Errors in peak /s", value: (r) => r.errors_in_peak_per_s },
              { header: "Errors out peak /s", value: (r) => r.errors_out_peak_per_s },
              { header: "Polls", value: (r) => r.samples },
            ], d.items)}>
            Export CSV
          </button>
        </div>
      }>
      {q.isError && <Banner tone="bad">The report could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      {d && d.items.length === 0 && (
        <Banner tone="info">No interface history in this period. History is recorded from two consecutive polls of an interface&apos;s counters.</Banner>
      )}
      <StatStrip>
        <StatTile label="Interfaces" value={s ? String(s.interfaces) : "—"} info="Interfaces with recorded history in the period." loading={q.isLoading} />
        <StatTile label="Peaked over 80 %" value={s ? String(s.busy_over_80) : "—"} tone={s && s.busy_over_80 > 0 ? "warn" : "good"} info="Interfaces whose utilization reached 80 % or more." loading={q.isLoading} />
        <StatTile label="With errors" value={s ? String(s.with_errors) : "—"} tone={s && s.with_errors > 0 ? "bad" : "good"} info="Interfaces with a recorded error rate above zero." loading={q.isLoading} />
      </StatStrip>
      <Panel title={`Interfaces (${d?.items.length ?? 0})`} info="Busiest peak first." state={q.isLoading ? "loading" : "ready"}>
        <DataTable caption="Interface traffic" columns={COLUMNS} rows={d?.items ?? []} rowKey={(r) => r.interface_id}
          rowHref={(r) => `/network/interfaces/${r.interface_id}`} emptyText="No interface history in this period." pageSize={100} />
      </Panel>
    </PageShell>
  );
}
