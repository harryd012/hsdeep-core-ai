"use client";

import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

import { downloadCsv } from "@/lib/csv";
import { getAvailabilityReport, type AvailabilityRow } from "@/lib/observabilityReports";
import ReportPeriod, { usePeriod } from "@/components/reports/ReportPeriod";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, titleCaseWords, type Column, type Tone } from "@/components/ui/PageKit";

/**
 * /reports/availability — per-device availability over a period, as the
 * share of recorded reachability checks that were up. A device with no check
 * in the period is counted as not measured, never as available.
 */

const tone = (pct: number): Tone => (pct >= 99.9 ? "good" : pct >= 99 ? "warn" : "bad");
const pctText = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 3 })} %`);

const COLUMNS: Column<AvailabilityRow>[] = [
  { key: "device", header: "Device", sortValue: (r) => r.device, render: (r) => <strong>{r.device}</strong> },
  { key: "type", header: "Type", sortValue: (r) => r.device_type, render: (r) => titleCaseWords(r.device_type) },
  { key: "crit", header: "Criticality", sortValue: (r) => r.criticality, render: (r) => titleCaseWords(r.criticality) || "—" },
  { key: "avail", header: "Availability", numeric: true, sortValue: (r) => r.availability_pct, render: (r) => <Pill tone={tone(r.availability_pct)}>{pctText(r.availability_pct)}</Pill> },
  { key: "down", header: "Failed checks", numeric: true, sortValue: (r) => r.down_samples, render: (r) => r.down_samples.toLocaleString("en-US") },
  { key: "samples", header: "Checks", numeric: true, sortValue: (r) => r.samples, render: (r) => r.samples.toLocaleString("en-US") },
  { key: "first", header: "First check", sortValue: (r) => r.first_sample, render: (r) => new Date(r.first_sample).toLocaleString() },
  { key: "last", header: "Last check", sortValue: (r) => r.last_sample, render: (r) => new Date(r.last_sample).toLocaleString() },
];

export default function AvailabilityReportPage() {
  const [period, setPeriod] = usePeriod(7);
  const q = useQuery({ queryKey: ["reports", "availability", period], queryFn: () => getAvailabilityReport(period) });
  const d = q.data;
  const s = d?.summary;

  return (
    <PageShell title="Availability" icon={ShieldCheck} crumbs={[{ label: "Reports", href: "/reports" }, { label: "Availability" }]}
      subtitle="Share of recorded reachability checks that were up, per device"
      actions={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <ReportPeriod onChange={setPeriod} />
          <button type="button" className="pk-button" disabled={!d?.items.length}
            onClick={() => d && downloadCsv(`availability-${d.since.slice(0, 10)}-${d.until.slice(0, 10)}.csv`, [
              { header: "Device", value: (r: AvailabilityRow) => r.device },
              { header: "Type", value: (r) => r.device_type },
              { header: "Address", value: (r) => r.address },
              { header: "Criticality", value: (r) => r.criticality },
              { header: "Availability %", value: (r) => r.availability_pct },
              { header: "Checks", value: (r) => r.samples },
              { header: "Failed checks", value: (r) => r.down_samples },
              { header: "First check", value: (r) => r.first_sample },
              { header: "Last check", value: (r) => r.last_sample },
            ], d.items)}>
            Export CSV
          </button>
        </div>
      }>
      {q.isError && <Banner tone="bad">The report could not be loaded{q.error?.message ? `: ${q.error.message}` : "."}</Banner>}
      {d && s && s.devices_measured === 0 && (
        <Banner tone="info">No reachability check was recorded in this period. Checks come from each collection of a device&apos;s monitoring source.</Banner>
      )}
      <StatStrip>
        <StatTile label="Fleet availability" value={s ? pctText(s.fleet_availability_pct) : "—"} info="Up checks over all checks of measured devices." loading={q.isLoading} naText="Not measured" />
        <StatTile label="Devices measured" value={s ? String(s.devices_measured) : "—"} sub={s ? `${s.devices_not_measured} not measured` : undefined} info="Devices with at least one recorded check in the period." loading={q.isLoading} />
        <StatTile label="Below 99.9 %" value={s ? String(s.devices_below_99_9) : "—"} tone={s && s.devices_below_99_9 > 0 ? "warn" : "good"} info="Measured devices under 99.9 % of checks up." loading={q.isLoading} />
        <StatTile label="Below 99 %" value={s ? String(s.devices_below_99) : "—"} tone={s && s.devices_below_99 > 0 ? "bad" : "good"} info="Measured devices under 99 % of checks up." loading={q.isLoading} />
      </StatStrip>
      <Panel title={`Devices (${d?.items.length ?? 0})`} info="Least available first. A percentage of checks, not of wall-clock time." state={q.isLoading ? "loading" : "ready"}>
        <DataTable caption="Device availability" columns={COLUMNS} rows={d?.items ?? []} rowKey={(r) => r.device_id}
          rowHref={(r) => `/infrastructure/hosts/${r.device_id}`} emptyText="No device was checked in this period." pageSize={100} />
      </Panel>
    </PageShell>
  );
}
