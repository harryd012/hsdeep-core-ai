"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import {
  getBackupOverview,
  listBackupItems,
  listBackupVaults,
  type BackupFilters,
  type BackupItem,
  type BackupVault,
} from "@/lib/api";
import { downloadCsv, formatBytes, type CsvColumn } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, Pill, SearchBox, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { PanelHeading, Props, RPO_LABEL, SidePanel, jobTone } from "../ui";

/**
 * /infrastructure/backup/instances — every backup instance (protected item)
 * across the connected backup sources: search, filters, column choice, CSV
 * and a details drawer. The Command Center filters (provider, subscription,
 * resource group, region, storage, workload) are applied by the API; the
 * inventory filters below are applied here. Every filter lives in the URL,
 * so a filtered view can be bookmarked or shared. Read-only: protection
 * changes, on-demand backups and restores come with the approved write phase.
 */

const SERVER_KEYS = ["provider", "subscription_id", "resource_group", "location", "storage_id", "workload_type"] as const;
const PAGE_SIZE = 50;
const COLUMNS_KEY = "hsdeep.backup.instances.columns";

const RPO = RPO_LABEL;
const ACTIVE = ["protected", "protection_error", "ir_pending"];
const resultTone = jobTone;

function age(hours: number | null): string {
  if (hours === null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} d`;
}

type Row = BackupItem & { vault: BackupVault | undefined };

const ALL_COLUMNS: (Column<Row> & { optional?: boolean })[] = [
  { key: "name", header: "Backup instance", sortValue: (r) => r.name, render: (r) => <><strong>{r.name}</strong><div className="pk-muted pk-truncate" style={{ maxWidth: 280 }} title={r.source_resource_id ?? undefined}>{r.source_resource_id ?? titleCaseWords(r.vault?.provider)}</div></> },
  { key: "workload", header: "Datasource type", optional: true, sortValue: (r) => r.workload_type, render: (r) => r.workload_type ?? <span className="pk-muted">—</span> },
  { key: "subscription", header: "Subscription", optional: true, sortValue: (r) => r.vault?.subscription_id ?? null, render: (r) => r.vault?.subscription_id ? <span className="pk-truncate" style={{ maxWidth: 160, display: "inline-block" }} title={r.vault.subscription_id}>{r.vault.subscription_id}</span> : <span className="pk-muted">—</span> },
  { key: "rg", header: "Resource group", optional: true, sortValue: (r) => r.vault?.resource_group ?? null, render: (r) => r.vault?.resource_group ?? <span className="pk-muted">—</span> },
  { key: "region", header: "Region", optional: true, sortValue: (r) => r.vault?.location ?? null, render: (r) => r.vault?.location ?? <span className="pk-muted">—</span> },
  { key: "vault", header: "Storage", sortValue: (r) => r.vault_name, render: (r) => r.vault_name },
  { key: "policy", header: "Policy", optional: true, sortValue: (r) => r.policy_name, render: (r) => r.policy_name ?? <span className="pk-muted">—</span> },
  { key: "rpo", header: "RPO", sortValue: (r) => ["within", "not_protected", "never", "breached"].indexOf(r.rpo_state), render: (r) => <Pill tone={RPO[r.rpo_state].tone}>{RPO[r.rpo_state].text}</Pill> },
  { key: "last", header: "Last backup", sortValue: (r) => r.last_backup_at, render: (r) => (r.last_backup_at ? <span title={r.last_backup_at}>{ago(r.last_backup_at)}</span> : <span className="pk-muted">Never</span>) },
  { key: "age", header: "Recovery point age", numeric: true, optional: true, sortValue: (r) => r.age_hours, render: (r) => age(r.age_hours) },
  { key: "result", header: "Last result", sortValue: (r) => r.last_backup_status, render: (r) => (r.last_backup_status ? <Pill tone={resultTone(r.last_backup_status)}>{titleCaseWords(r.last_backup_status)}</Pill> : <span className="pk-muted" title="The provider reports results per job run, not per item">—</span>) },
  { key: "protection", header: "Protection state", sortValue: (r) => r.protection_state, render: (r) => titleCaseWords(r.protection_state) || <span className="pk-muted">—</span> },
  { key: "health", header: "Health", optional: true, sortValue: (r) => r.health_status, render: (r) => titleCaseWords(r.health_status) || <span className="pk-muted">—</span> },
  { key: "points", header: "Restore points", numeric: true, optional: true, sortValue: (r) => r.recovery_point_count, render: (r) => (r.recovery_point_count === null ? <span className="pk-muted">—</span> : r.recovery_point_count.toLocaleString("en-US")) },
  { key: "size", header: "Backup size", numeric: true, optional: true, sortValue: (r) => r.size_bytes, render: (r) => (r.size_bytes === null ? <span className="pk-muted">—</span> : formatBytes(r.size_bytes)) },
];
const DEFAULT_HIDDEN = ["subscription", "rg", "health", "size"];

const CSV: CsvColumn<Row>[] = [
  { header: "Backup instance", value: (r) => r.name },
  { header: "Datasource type", value: (r) => r.workload_type },
  { header: "Resource ID", value: (r) => r.source_resource_id },
  { header: "Provider", value: (r) => r.vault?.provider },
  { header: "Subscription", value: (r) => r.vault?.subscription_id },
  { header: "Resource group", value: (r) => r.vault?.resource_group },
  { header: "Region", value: (r) => r.vault?.location },
  { header: "Storage", value: (r) => r.vault_name },
  { header: "Policy", value: (r) => r.policy_name },
  { header: "RPO state", value: (r) => r.rpo_state },
  { header: "Last backup (UTC)", value: (r) => r.last_backup_at },
  { header: "Recovery point age (h)", value: (r) => r.age_hours },
  { header: "Last result", value: (r) => r.last_backup_status },
  { header: "Protection state", value: (r) => r.protection_state },
  { header: "Health", value: (r) => r.health_status },
  { header: "Restore points", value: (r) => r.recovery_point_count },
  { header: "Oldest restore point (UTC)", value: (r) => r.oldest_recovery_point_at },
  { header: "Daily retention (days)", value: (r) => r.retention_days },
  { header: "Backup size (bytes)", value: (r) => r.size_bytes },
];

function readHidden(): string[] {
  try {
    const raw = window.localStorage.getItem(COLUMNS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : DEFAULT_HIDDEN;
  } catch {
    return DEFAULT_HIDDEN;
  }
}

function Drawer({ row, onClose }: { row: Row; onClose: () => void }) {
  const v = row.vault;
  return (
    <SidePanel title={row.name} subtitle={[titleCaseWords(v?.provider), row.workload_type].filter(Boolean).join(" · ")} onClose={onClose}>
      <p style={{ margin: "12px 0" }}><Pill tone={RPO[row.rpo_state].tone}>{RPO[row.rpo_state].text}</Pill></p>
      <PanelHeading>Protection</PanelHeading>
      <Props rows={[
        ["Protection state", titleCaseWords(row.protection_state) || null],
        ["Health", titleCaseWords(row.health_status) || null],
        ["Last backup", row.last_backup_at ? `${new Date(row.last_backup_at).toLocaleString()} (${ago(row.last_backup_at)})` : "Never"],
        ["Last result", row.last_backup_status ? titleCaseWords(row.last_backup_status) : null],
        ["Restore points", row.recovery_point_count?.toLocaleString("en-US") ?? null],
        ["Oldest restore point", row.oldest_recovery_point_at ? new Date(row.oldest_recovery_point_at).toLocaleString() : null],
        ["Policy", row.policy_name],
        ["Daily retention", row.retention_days !== null ? `${row.retention_days} days` : null],
        ["Backup size", row.size_bytes !== null ? formatBytes(row.size_bytes) : null],
      ]} />
      <PanelHeading>Storage</PanelHeading>
      <Props rows={[
        ["Storage", row.vault_name],
        ["Type", v ? titleCaseWords(v.kind) : null],
        ["Subscription", v?.subscription_id ?? null],
        ["Resource group", v?.resource_group ?? null],
        ["Region", v?.location ?? null],
        ["Resource ID", row.source_resource_id ? <code style={{ wordBreak: "break-all" }}>{row.source_resource_id}</code> : null],
      ]} />
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
        <Link className="pk-button pk-button--primary" href={`/infrastructure/backup/items/${row.id}`}>Policy, restore points and jobs</Link>
        {row.device_id && <Link className="pk-button" href={`/infrastructure/hosts/${row.device_id}`}>View resource</Link>}
        <Link className="pk-button" href={`/infrastructure/backup/vaults/${row.vault_id}`}>View storage</Link>
        <Link className="pk-button" href={`/infrastructure/backup/jobs?storage_id=${row.vault_id}&q=${encodeURIComponent(row.name)}`}>View jobs</Link>
      </p>
      <p className="pk-help">On-demand backup, restore and protection changes are not available yet: this platform reads backups with read-only roles.</p>
    </SidePanel>
  );
}

function InstancesContent() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const get = (k: string) => params.get(k) ?? "";
  const filters: BackupFilters = useMemo(
    () => Object.fromEntries(SERVER_KEYS.map((k) => [k, params.get(k) || undefined])),
    [params],
  );
  const setParam = (updates: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const q = next.toString();
    router.replace(`${pathname}${q ? `?${q}` : ""}`, { scroll: false });
  };

  const overview = useQuery({ queryKey: ["backup", "overview", filters, 14], queryFn: () => getBackupOverview(filters), refetchInterval: 120_000 });
  const configured = !!overview.data?.configured;
  const items = useQuery({ queryKey: ["backup", "items", filters], queryFn: () => listBackupItems({}, filters), enabled: configured, refetchInterval: 60_000 });
  const vaults = useQuery({ queryKey: ["backup", "vaults", {}], queryFn: () => listBackupVaults(), enabled: configured, refetchInterval: 300_000 });

  const [search, setSearch] = useState(get("q"));
  const [hidden, setHidden] = useState<string[]>(DEFAULT_HIDDEN);
  const [showColumns, setShowColumns] = useState(false);
  const [selected, setSelected] = useState<Row | null>(null);
  // Column choice is a per-viewer convenience, read once on mount.
  useEffect(() => setHidden(readHidden()), []);
  const toggleColumn = (key: string) => {
    const next = hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key];
    setHidden(next);
    try { window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  };

  const vaultById = useMemo(() => new Map((vaults.data ?? []).map((v) => [v.id, v])), [vaults.data]);
  const all: Row[] = useMemo(() => (items.data ?? []).map((i) => ({ ...i, vault: vaultById.get(i.vault_id) })), [items.data, vaultById]);
  const options = (pick: (r: Row) => string | null | undefined) => [...new Set(all.map(pick).filter((v): v is string => !!v))].sort();

  const rpo = get("rpo"), result = get("result"), protection = get("protection"), policy = get("policy"), q = get("q").toLowerCase();
  const rows = all.filter((r) =>
    (!rpo || r.rpo_state === rpo)
    && (!result || (result === "none" ? r.last_backup_status === null : r.last_backup_status === result))
    && (!protection || (protection === "active" ? ACTIVE.includes(r.protection_state ?? "") : r.protection_state === protection))
    && (!policy || r.policy_name === policy)
    && (!q || r.name.toLowerCase().includes(q) || (r.source_resource_id ?? "").toLowerCase().includes(q) || r.vault_name.toLowerCase().includes(q)),
  );
  const columns = ALL_COLUMNS.filter((c) => !hidden.includes(c.key));
  const anyFilter = [...SERVER_KEYS, "rpo", "result", "protection", "policy", "q"].some((k) => params.get(k));

  // Debounce the search box into the URL.
  useEffect(() => {
    const t = setTimeout(() => { if (search !== get("q")) setParam({ q: search }); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const select = (key: string, label: string, all: string, values: { value: string; label: string }[]) => (
    <select key={key} className="pk-input" aria-label={label} value={get(key)} onChange={(e) => setParam({ [key]: e.target.value })} style={{ width: "auto", maxWidth: 240 }}>
      <option value="">{all}</option>
      {values.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
  const fo = overview.data?.filter_options;
  const opts = (values: string[], label = (v: string) => v) => values.map((v) => ({ value: v, label: label(v) }));

  return (
    <PageShell
      title="Backup Instances"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center", href: "/infrastructure/backup" }, { label: "Backup Instances" }]}
      subtitle="Every protected workload across the connected backup sources: where it is backed up, under which policy, how old its newest restore point is and whether it is inside the RPO."
      meta={overview.data?.observed_at ? `Collected ${ago(overview.data.observed_at)}` : undefined}
    >
      {overview.error && <Banner tone="bad">Backup data could not be loaded: {overview.error instanceof Error ? overview.error.message : "request failed"}.</Banner>}
      {overview.data && !configured && (
        <Banner tone="info">No backup source is connected yet. <Link className="ecc-link" href="/infrastructure/backup">Connect Azure Backup or Veeam</Link> to list backup instances.</Banner>
      )}
      {configured && (
        <Panel
          title={`${rows.length.toLocaleString("en-US")} of ${all.length.toLocaleString("en-US")} instances`}
          info="Select a row for details. Filters are kept in the address bar: bookmark or share the link to save a view."
          state={items.isLoading || vaults.isLoading ? "loading" : "ready"}
          className="pk-col-12"
        >
          <div className="pk-toolbar">
            <SearchBox value={search} onChange={setSearch} placeholder="Search name, resource id or storage" label="Search backup instances" />
            {select("rpo", "Filter by RPO state", "Any RPO state", Object.entries(RPO).map(([k, v]) => ({ value: k, label: v.text })))}
            {select("protection", "Filter by protection state", "Any protection", [{ value: "active", label: "Actively protected" }, ...opts(options((r) => r.protection_state), titleCaseWords)])}
            {select("result", "Filter by last result", "Any last result", [...opts(options((r) => r.last_backup_status), titleCaseWords), { value: "none", label: "Not reported" }])}
            {select("policy", "Filter by policy", "Any policy", opts(options((r) => r.policy_name)))}
            {fo && fo.workload_types.length > 1 && select("workload_type", "Filter by datasource type", "Any datasource type", opts(fo.workload_types))}
            {fo && fo.vaults.length > 1 && select("storage_id", "Filter by storage", "Any storage", fo.vaults)}
            {fo && fo.locations.length > 1 && select("location", "Filter by region", "Any region", opts(fo.locations))}
            {fo && fo.subscriptions.length > 1 && select("subscription_id", "Filter by subscription", "Any subscription", opts(fo.subscriptions))}
            {fo && fo.resource_groups.length > 1 && select("resource_group", "Filter by resource group", "Any resource group", opts(fo.resource_groups))}
            {fo && fo.providers.length > 1 && select("provider", "Filter by provider", "Any provider", opts(fo.providers, titleCaseWords))}
            {anyFilter && <button type="button" className="pk-button" onClick={() => { setSearch(""); router.replace(pathname, { scroll: false }); }}>Clear filters</button>}
            <button type="button" className="pk-button" aria-expanded={showColumns} onClick={() => setShowColumns((s) => !s)}>Columns</button>
            <button type="button" className="pk-button" disabled={rows.length === 0} onClick={() => downloadCsv(`backup-instances-${new Date().toISOString().slice(0, 10)}.csv`, CSV, rows)}>Export CSV</button>
          </div>
          {showColumns && (
            <fieldset className="pk-toolbar" style={{ border: 0, padding: 0 }}>
              <legend className="ecc-sr-only">Visible columns</legend>
              {ALL_COLUMNS.filter((c) => c.optional).map((c) => (
                <label key={c.key} className="pk-help" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  <input type="checkbox" checked={!hidden.includes(c.key)} onChange={() => toggleColumn(c.key)} /> {c.header}
                </label>
              ))}
            </fieldset>
          )}
          <DataTable
            caption="Backup instances"
            columns={columns}
            rows={rows}
            pageSize={PAGE_SIZE}
            rowKey={(r) => r.id}
            onRowClick={(r) => setSelected(r)}
            defaultSort={{ key: "rpo", dir: "desc" }}
            maxHeight="max(420px, calc(100vh - 380px))"
            emptyText={anyFilter ? "No backup instance matches these filters." : "No backup instances reported."}
          />
        </Panel>
      )}
      {selected && <Drawer row={selected} onClose={() => setSelected(null)} />}
    </PageShell>
  );
}

export default function BackupInstancesPage() {
  return (
    <Suspense>
      <InstancesContent />
    </Suspense>
  );
}
