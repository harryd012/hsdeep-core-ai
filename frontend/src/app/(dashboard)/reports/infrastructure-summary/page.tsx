"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Server } from "lucide-react";
import { getExplorerHosts, type ExplorerFacetValue, type HostExplorerRow } from "@/lib/api";
import { DonutRing, Legend } from "@/components/command-center/primitives";
import {
  Banner,
  BarList,
  DataTable,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/** Rows loaded for the inventory table and freshness breakdown (API maximum). */
const PAGE = 200;

const HEALTH = [
  { key: "critical", label: "Critical alerts", color: "var(--ecc-bad)" },
  { key: "warning", label: "Warning alerts", color: "var(--ecc-warn)" },
  { key: "no_alerts", label: "No open alerts", color: "var(--ecc-good)" },
];
const STATUS_COLOR: Record<string, string> = { up: "var(--ecc-good)", warning: "var(--ecc-warn)", down: "var(--ecc-down)", unknown: "var(--ecc-unknown)" };
const FRESHNESS = [
  { key: "1h", label: "Within 1 hour", max: 1, color: "var(--ecc-good)" },
  { key: "24h", label: "Within 24 hours", max: 24, color: "var(--ecc-accent)" },
  { key: "7d", label: "Within 7 days", max: 24 * 7, color: "var(--ecc-warn)" },
  { key: "old", label: "Older than 7 days", max: Infinity, color: "var(--ecc-bad)" },
];

const count = (facet: ExplorerFacetValue[] | undefined, value: string) => facet?.find((f) => f.value === value)?.count ?? 0;
const facetBars = (facet: ExplorerFacetValue[] | undefined, color?: string) =>
  (facet ?? []).slice(0, 8).map((f) => ({ key: f.value, label: titleCaseWords(f.value) || "Unspecified", value: f.count, color }));

export default function InfrastructureSummaryPage() {
  const q = useQuery({ queryKey: ["reports", "infrastructure"], queryFn: () => getExplorerHosts({ limit: PAGE }), refetchInterval: 60_000 });
  const data = q.data;
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const total = data?.total ?? 0;
  const state = q.isLoading ? "loading" : q.error ? "error" : total === 0 ? "empty" : "ready";

  // Measured against the server's generation time, not the browser clock.
  const generatedAt = data?.generated_at;
  const freshness = useMemo(() => {
    const now = generatedAt ? new Date(generatedAt).getTime() : 0;
    const out = FRESHNESS.map((f) => ({ ...f, value: 0 }));
    let never = 0;
    for (const r of rows) {
      const at = r.last_telemetry_at ?? r.last_seen_at;
      if (!at) { never += 1; continue; }
      const h = (now - new Date(at).getTime()) / 3_600_000;
      out.find((f) => h <= f.max)!.value += 1;
    }
    return { buckets: out, never };
  }, [rows, generatedAt]);
  const newestTelemetry = rows.reduce<string | null>((m, r) => (r.last_telemetry_at && (!m || r.last_telemetry_at > m) ? r.last_telemetry_at : m), null);

  const columns: Column<HostExplorerRow>[] = [
    {
      key: "name",
      header: "Host",
      sortValue: (r) => r.name,
      render: (r) => (
        <>
          <span className="pk-cell-main">{r.name}</span>
          <span className="pk-cell-sub">{[r.ip_address, r.hostname].filter(Boolean).join(" · ") || "—"}</span>
        </>
      ),
    },
    { key: "health", header: "Health", sortValue: (r) => HEALTH.findIndex((h) => h.key === r.health), render: (r) => <Pill tone={r.health === "critical" ? "bad" : r.health === "warning" ? "warn" : "good"}>{HEALTH.find((h) => h.key === r.health)?.label ?? r.health}</Pill> },
    { key: "status", header: "Status", sortValue: (r) => r.status, render: (r) => <Pill tone={statusTone(r.status)}>{r.status}</Pill> },
    { key: "type", header: "Type", sortValue: (r) => r.device_type, render: (r) => titleCaseWords(r.device_type) },
    { key: "vendor", header: "Vendor", sortValue: (r) => r.vendor, render: (r) => r.vendor ?? <span className="pk-muted">—</span> },
    { key: "site", header: "Site", sortValue: (r) => r.site_name, render: (r) => r.site_name ?? <span className="pk-muted">Unassigned</span> },
    { key: "alerts", header: "Open alerts", numeric: true, sortValue: (r) => r.alerts.critical * 1000 + r.alerts.warning, render: (r) => (r.alerts.critical + r.alerts.warning + r.alerts.info) || <span className="pk-muted">0</span> },
    { key: "seen", header: "Last telemetry", sortValue: (r) => r.last_telemetry_at ?? r.last_seen_at, render: (r) => <span className="pk-muted">{ago(r.last_telemetry_at ?? r.last_seen_at)}</span> },
  ];

  const statusFacet = data?.facets.status ?? [];
  const unassigned = rows.filter((r) => !r.site_id).length;

  return (
    <PageShell
      title="Infrastructure Summary"
      icon={Server}
      crumbs={[{ label: "Reports", href: "/reports" }, { label: "Infrastructure Summary" }]}
      subtitle="What is in the estate, how healthy it is and how fresh the data behind that health is."
      meta={data ? `Generated ${ago(data.generated_at)}` : undefined}
    >
      {freshness.buckets[3].value > 0 && freshness.buckets[3].value >= rows.length / 2 && (
        <Banner tone="warn">
          {freshness.buckets[3].value} of {rows.length} hosts have had no telemetry for over 7 days
          {newestTelemetry ? ` (newest telemetry ${ago(newestTelemetry)})` : ""}. Their status reflects the last collection, not the present.
        </Banner>
      )}

      <StatStrip>
        <StatTile label="Hosts" value={data ? total.toLocaleString("en-US") : "—"} info="Every monitored host and device in this tenant." href="/infrastructure/hosts" loading={q.isLoading} />
        <StatTile label="Critical alerts" value={data ? String(count(data.facets.health, "critical")) : "—"} alarm={count(data?.facets.health, "critical") > 0} info="Hosts with at least one open critical alert." loading={q.isLoading} />
        <StatTile label="Warning alerts" value={data ? String(count(data.facets.health, "warning")) : "—"} tone="warn" info="Hosts whose worst open alert is a warning." loading={q.isLoading} />
        <StatTile label="Down" value={data ? String(count(data.facets.status, "down")) : "—"} alarm={count(data?.facets.status, "down") > 0} sub={data && total ? `${Math.round((count(data.facets.status, "down") / total) * 100)}% of hosts` : undefined} info="Hosts whose last known status is down." loading={q.isLoading} />
        <StatTile label="Device types" value={data ? String(data.facets.device_type.length) : "—"} info="Distinct device types in the inventory." loading={q.isLoading} />
        <StatTile label="Vendors" value={data ? String(data.facets.vendor.length) : "—"} info="Distinct vendors in the inventory." loading={q.isLoading} />
        <StatTile label="No site" value={data ? String(unassigned) : "—"} tone={unassigned ? "warn" : "good"} sub={rows.length < total ? `of the first ${rows.length}` : undefined} info="Hosts not assigned to a site, so site-level health and impact cannot include them." href="/organization" loading={q.isLoading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel title="Alert health" info="Hosts by their worst open alert." state={state} className="pk-col-4">
          {data && (
            <div className="pk-donut-row">
              <DonutRing size={132} label="Hosts by alert health" centerValue={total.toLocaleString("en-US")} centerLabel="hosts" segments={HEALTH.map((h) => ({ key: h.key, value: count(data.facets.health, h.key), color: h.color }))} />
              <Legend total={total} rows={HEALTH.map((h) => ({ key: h.key, label: h.label, value: count(data.facets.health, h.key), color: h.color, href: `/infrastructure/hosts?health=${h.key}` }))} />
            </div>
          )}
        </Panel>
        <Panel title="Status" info="Hosts by last known status." state={state} className="pk-col-4">
          {data && (
            <div className="pk-donut-row">
              <DonutRing size={132} label="Hosts by status" centerValue={String(count(data.facets.status, "up"))} centerLabel="up" segments={statusFacet.map((f) => ({ key: f.value, value: f.count, color: STATUS_COLOR[f.value] ?? "var(--ecc-unknown)" }))} />
              <Legend total={total} rows={statusFacet.map((f) => ({ key: f.value, label: titleCaseWords(f.value), value: f.count, color: STATUS_COLOR[f.value] ?? "var(--ecc-unknown)", href: `/infrastructure/hosts?status=${f.value}` }))} />
            </div>
          )}
        </Panel>
        <Panel title="Telemetry freshness" info={`How recently each host reported, across the first ${PAGE} hosts.`} aside={rows.length < total ? `${rows.length} of ${total}` : undefined} state={state} className="pk-col-4">
          <BarList rows={[...freshness.buckets.map((b) => ({ key: b.key, label: b.label, value: b.value, color: b.color })), ...(freshness.never ? [{ key: "never", label: "Never reported", value: freshness.never, color: "var(--ecc-unknown)" }] : [])]} />
        </Panel>

        <Panel title="By device type" info="Hosts per device type." state={state} className="pk-col-4">
          <BarList rows={facetBars(data?.facets.device_type)} />
        </Panel>
        <Panel title="By vendor" info="Hosts per vendor." state={state} className="pk-col-4">
          <BarList rows={facetBars(data?.facets.vendor, "var(--ecc-good)")} emptyText="No vendor recorded for any host." />
        </Panel>
        <Panel title="By environment and criticality" info="How the estate is classified. Unclassified hosts cannot be prioritised by business impact." state={state} className="pk-col-4">
          <BarList rows={facetBars(data?.facets.environment, "var(--ecc-accent)")} emptyText="No environment set on any host." />
          <h3 className="pk-subhead">Criticality</h3>
          <BarList rows={facetBars(data?.facets.criticality, "var(--ecc-warn)")} emptyText="No criticality set on any host." />
        </Panel>

        <Panel title="Inventory" info="Hosts ordered by health, worst first. Open a host for its metrics, sensors and alerts." aside={rows.length < total ? `first ${rows.length} of ${total}` : `${total} hosts`} state={state} className="pk-col-12">
          <DataTable caption="Host inventory" columns={columns} rows={rows} rowKey={(r) => r.id} rowHref={(r) => `/infrastructure/hosts/${r.id}`} maxHeight={560} />
        </Panel>
      </div>
    </PageShell>
  );
}
