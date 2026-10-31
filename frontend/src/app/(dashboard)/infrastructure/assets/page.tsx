"use client";

import { useEffect, useMemo, useState } from "react";
import { AssetDevice, AssetSite, listAssetDevices, listAssetSites } from "@/lib/api";
import {
  Banner,
  DataTable,
  PageShell,
  Panel,
  Pill,
  SearchBox,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

const PAGE_LIMIT = 100;
type StatusFilter = "all" | "up" | "down" | "other";

export default function AssetInventoryPage() {
  const [devices, setDevices] = useState<AssetDevice[]>([]);
  const [sites, setSites] = useState<AssetSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [devs, sts] = await Promise.all([
          listAssetDevices(undefined, { limit: PAGE_LIMIT }),
          listAssetSites(undefined, { limit: PAGE_LIMIT }),
        ]);
        if (!cancelled) {
          setDevices(devs);
          setSites(sts);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load assets");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = window.setInterval(load, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const siteById = useMemo(() => new Map(sites.map((s) => [s.id, s])), [sites]);
  const tone = (d: AssetDevice) => statusTone(d.status);
  const up = devices.filter((d) => tone(d) === "good").length;
  const down = devices.filter((d) => tone(d) === "bad").length;
  const other = devices.length - up - down;
  const truncated = devices.length >= PAGE_LIMIT;

  const q = search.trim().toLowerCase();
  const visible = devices.filter((d) => {
    if (status === "up" && tone(d) !== "good") return false;
    if (status === "down" && tone(d) !== "bad") return false;
    if (status === "other" && (tone(d) === "good" || tone(d) === "bad")) return false;
    return (
      !q ||
      [d.name, d.hostname, d.ip_address, d.vendor, d.model, d.device_type].some((v) => (v ?? "").toLowerCase().includes(q))
    );
  });

  const columns: Column<AssetDevice>[] = [
    {
      key: "name",
      header: "Device",
      sortValue: (d) => d.name.toLowerCase(),
      render: (d) => (
        <>
          <span className="pk-cell-main">{d.name}</span>
          {d.hostname && d.hostname !== d.name && <span className="pk-cell-sub">{d.hostname}</span>}
        </>
      ),
    },
    { key: "status", header: "Status", sortValue: (d) => d.status, render: (d) => <Pill tone={tone(d)}>{titleCaseWords(d.status)}</Pill> },
    { key: "type", header: "Type", sortValue: (d) => d.device_type, render: (d) => titleCaseWords(d.device_type) },
    {
      key: "vendor",
      header: "Vendor / model",
      sortValue: (d) => (d.vendor ?? "").toLowerCase(),
      render: (d) => (d.vendor || d.model ? [d.vendor, d.model].filter(Boolean).join(" ") : <span className="pk-muted">—</span>),
    },
    { key: "ip", header: "IP address", sortValue: (d) => d.ip_address, render: (d) => <span className="pk-mono">{d.ip_address ?? "—"}</span> },
    {
      key: "site",
      header: "Site",
      sortValue: (d) => (d.site_id ? siteById.get(d.site_id)?.name ?? "" : ""),
      render: (d) => (d.site_id ? siteById.get(d.site_id)?.name ?? "—" : <span className="pk-muted">Unassigned</span>),
    },
    { key: "env", header: "Environment", sortValue: (d) => d.environment, render: (d) => (d.environment ? titleCaseWords(d.environment) : <span className="pk-muted">—</span>) },
    { key: "crit", header: "Criticality", sortValue: (d) => d.criticality, render: (d) => (d.criticality ? <Pill tone={d.criticality === "critical" || d.criticality === "high" ? "warn" : "neutral"} dot={false}>{titleCaseWords(d.criticality)}</Pill> : <span className="pk-muted">—</span>) },
    {
      key: "seen",
      header: "Last seen",
      sortValue: (d) => (d.last_seen_at ? new Date(d.last_seen_at).getTime() : null),
      render: (d) => <span className="pk-muted">{d.last_seen_at ? ago(d.last_seen_at) : "Never"}</span>,
    },
  ];

  const v = (x: number) => (loading ? "—" : x.toLocaleString("en-US"));

  return (
    <PageShell
      title="Asset Inventory"
      subtitle="Devices and sites registered in the CMDB for this tenant."
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Asset Inventory" }]}
    >
      {error && <Banner tone="warn">Assets could not be refreshed: {error}</Banner>}
      {truncated && !loading && (
        <Banner tone="info">
          Showing the first {PAGE_LIMIT} devices. Counts below describe this page only — use the Explorer for the full, paged inventory.
        </Banner>
      )}

      <StatStrip>
        <StatTile label="Devices" value={v(devices.length)} info="Devices on this page of the inventory." sub={truncated ? "first page" : undefined} href="/explorer" loading={loading} />
        <StatTile label="Up" value={v(up)} info="Devices whose status is up." loading={loading} />
        <StatTile label="Down" value={v(down)} alarm={down > 0} info="Devices whose status is down, offline or critical." loading={loading} />
        <StatTile label="Other / unknown" value={v(other)} info="Devices in any other state, including no status yet." loading={loading} />
        <StatTile label="Sites" value={v(sites.length)} info="Sites defined for this tenant." loading={loading} />
      </StatStrip>

      <Panel title="Devices" aside={loading ? undefined : `${visible.length} of ${devices.length}`} state={loading ? "loading" : "ready"} skeletonHeight={360}>
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, hostname, IP, vendor" label="Search devices" />
          <Tabs
            label="Status"
            value={status}
            onChange={setStatus}
            tabs={[
              { key: "all", label: "All", count: devices.length },
              { key: "up", label: "Up", count: up },
              { key: "down", label: "Down", count: down },
              { key: "other", label: "Other", count: other },
            ]}
          />
        </div>
        <DataTable
          caption="Devices"
          columns={columns}
          rows={visible}
          rowKey={(d) => d.id}
          emptyText={devices.length === 0 ? "No devices registered yet. Add a monitoring source or run discovery." : "No devices match the current filter."}
        />
      </Panel>
    </PageShell>
  );
}
