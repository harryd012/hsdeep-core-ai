"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import { getBackupOverview, getBackupSecurity, listBackupVaults, type BackupVault } from "@/lib/api";
import { downloadCsv, formatBytes, type CsvColumn } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, Pill, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { flag, posture } from "../ui";

/**
 * /infrastructure/backup/vaults — every backup storage location (Azure
 * Recovery Services vault, Veeam repository ...) with its size, items and
 * security posture. Select one for its detail page.
 */

const CSV: CsvColumn<BackupVault>[] = [
  { header: "Name", value: (v) => v.name },
  { header: "Provider", value: (v) => v.provider },
  { header: "Type", value: (v) => v.kind },
  { header: "Subscription", value: (v) => v.subscription_id },
  { header: "Resource group", value: (v) => v.resource_group },
  { header: "Region", value: (v) => v.location },
  { header: "Items", value: (v) => v.items_total },
  { header: "Items outside RPO", value: (v) => v.items_breaching_rpo },
  { header: "Policies", value: (v) => v.policies },
  { header: "Storage used (bytes)", value: (v) => v.storage_used_bytes },
  { header: "Capacity (bytes)", value: (v) => v.capacity_bytes },
  { header: "Free (bytes)", value: (v) => v.free_bytes },
  { header: "Redundancy", value: (v) => v.storage_redundancy },
  { header: "Soft delete", value: (v) => v.soft_delete_state },
  { header: "Immutability", value: (v) => v.immutability_state },
  { header: "Encryption", value: (v) => v.encryption },
  { header: "Last collected (UTC)", value: (v) => v.observed_at },
];

export default function BackupVaultsPage() {
  const overview = useQuery({ queryKey: ["backup", "overview", {}, 14], queryFn: () => getBackupOverview(), refetchInterval: 120_000 });
  const configured = !!overview.data?.configured;
  const vaults = useQuery({ queryKey: ["backup", "vaults", {}], queryFn: () => listBackupVaults(), enabled: configured, refetchInterval: 120_000 });
  const security = useQuery({ queryKey: ["backup", "security", {}], queryFn: () => getBackupSecurity(), enabled: configured, refetchInterval: 300_000 });
  const score = new Map((security.data?.locations ?? []).map((l) => [l.vault_id, l]));

  const columns: Column<BackupVault>[] = [
    { key: "name", header: "Storage", sortValue: (v) => v.name, render: (v) => <><span className="pk-cell-main">{v.name}</span><span className="pk-cell-sub">{[titleCaseWords(v.provider), titleCaseWords(v.kind)].join(" · ")}</span></> },
    { key: "region", header: "Region", sortValue: (v) => v.location, render: (v) => v.location ?? <span className="pk-muted">—</span> },
    { key: "rg", header: "Resource group", sortValue: (v) => v.resource_group, render: (v) => v.resource_group ?? <span className="pk-muted">—</span> },
    { key: "items", header: "Items", numeric: true, sortValue: (v) => v.items_total, render: (v) => v.items_total },
    { key: "breach", header: "Outside RPO", numeric: true, sortValue: (v) => v.items_breaching_rpo, render: (v) => (v.items_breaching_rpo ? <span style={{ color: "var(--ecc-bad)" }}>{v.items_breaching_rpo}</span> : "0") },
    { key: "policies", header: "Policies", numeric: true, sortValue: (v) => v.policies, render: (v) => v.policies },
    { key: "storage", header: "Storage used", numeric: true, sortValue: (v) => v.storage_used_bytes, render: (v) => (v.storage_used_bytes === null ? <span className="pk-muted">Not reported</span> : formatBytes(v.storage_used_bytes)) },
    { key: "redundancy", header: "Redundancy", sortValue: (v) => v.storage_redundancy, render: (v) => titleCaseWords(v.storage_redundancy) || <span className="pk-muted">Not reported</span> },
    { key: "soft", header: "Soft delete", sortValue: (v) => v.soft_delete_state, render: (v) => { const p = posture(v.soft_delete_state, ["enabled", "always_on"]); return <Pill tone={p.tone}>{p.text}</Pill>; } },
    { key: "immutable", header: "Immutable", sortValue: (v) => (v.immutable === null ? null : Number(v.immutable)), render: (v) => { const f = flag(v.immutable); return <Pill tone={f.tone}>{f.text}</Pill>; } },
    {
      key: "security", header: "Security checks", numeric: true,
      sortValue: (v) => { const l = score.get(v.id); return l && l.evaluated ? l.passed / l.evaluated : null; },
      render: (v) => { const l = score.get(v.id); return l && l.evaluated ? `${l.passed} of ${l.evaluated}` : <span className="pk-muted">—</span>; },
    },
    { key: "seen", header: "Last collected", sortValue: (v) => v.observed_at, render: (v) => <span className="pk-muted">{ago(v.observed_at)}</span> },
  ];

  return (
    <PageShell
      title="Vaults & Storage"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center", href: "/infrastructure/backup" }, { label: "Vaults & Storage" }]}
      subtitle="Every backup storage location: Azure Recovery Services vaults and Veeam repositories, with what they hold and how they are secured."
      meta={overview.data?.observed_at ? `Collected ${ago(overview.data.observed_at)}` : undefined}
    >
      {overview.data && !configured && (
        <Banner tone="info">No backup source is connected yet. <Link className="ecc-link" href="/infrastructure/backup">Connect Azure Backup or Veeam</Link> to list storage.</Banner>
      )}
      {configured && (
        <Panel
          title={`${(vaults.data ?? []).length} storage locations`}
          info="Select a storage location for its instances, policies, jobs, storage trend and security checks."
          aside={<button type="button" className="pk-button" disabled={!vaults.data?.length} onClick={() => downloadCsv(`backup-storage-${new Date().toISOString().slice(0, 10)}.csv`, CSV, vaults.data ?? [])}>Export CSV</button>}
          state={vaults.isLoading ? "loading" : "ready"}
          className="pk-col-12"
        >
          <DataTable caption="Backup storage" columns={columns} rows={vaults.data ?? []} rowKey={(v) => v.id} rowHref={(v) => `/infrastructure/backup/vaults/${v.id}`} defaultSort={{ key: "name", dir: "asc" }} maxHeight="max(420px, calc(100vh - 360px))" emptyText="No backup storage reported." />
        </Panel>
      )}
    </PageShell>
  );
}
