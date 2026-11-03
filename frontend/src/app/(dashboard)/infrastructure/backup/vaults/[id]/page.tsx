"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import {
  ApiError,
  getBackupSecurity,
  getBackupStorageHistory,
  getBackupVault,
  listBackupItems,
  listBackupJobs,
  listBackupPolicies,
  type BackupItem,
  type BackupJob,
  type BackupPolicyRow,
} from "@/lib/api";
import { formatBytes } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, Tabs, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { CHECK_TEXT, CHECK_TONE, Props, RPO_LABEL, StorageTrend, duration, flag, jobTone, posture, retention } from "../../ui";

/**
 * /infrastructure/backup/vaults/[id] — one backup storage location: overview,
 * the backup instances and policies it holds, its jobs (30 days), storage
 * trend and security checks. Each tab reads the same filtered APIs as the
 * Backup Center pages (storage_id), so the figures always agree.
 */

type Tab = "overview" | "instances" | "policies" | "jobs" | "storage" | "security";

const itemColumns: Column<BackupItem>[] = [
  { key: "name", header: "Backup instance", sortValue: (i) => i.name, render: (i) => <><span className="pk-cell-main">{i.name}</span><span className="pk-cell-sub">{i.workload_type ?? ""}</span></> },
  { key: "rpo", header: "RPO", sortValue: (i) => ["within", "not_protected", "never", "breached"].indexOf(i.rpo_state), render: (i) => <Pill tone={RPO_LABEL[i.rpo_state].tone}>{RPO_LABEL[i.rpo_state].text}</Pill> },
  { key: "last", header: "Last backup", sortValue: (i) => i.last_backup_at, render: (i) => (i.last_backup_at ? ago(i.last_backup_at) : <span className="pk-muted">Never</span>) },
  { key: "protection", header: "Protection", sortValue: (i) => i.protection_state, render: (i) => titleCaseWords(i.protection_state) || "—" },
  { key: "policy", header: "Policy", sortValue: (i) => i.policy_name, render: (i) => i.policy_name ?? <span className="pk-muted">—</span> },
  { key: "points", header: "Restore points", numeric: true, sortValue: (i) => i.recovery_point_count, render: (i) => i.recovery_point_count?.toLocaleString("en-US") ?? <span className="pk-muted">—</span> },
];

const policyColumns: Column<BackupPolicyRow>[] = [
  { key: "name", header: "Policy", sortValue: (p) => p.name, render: (p) => <><span className="pk-cell-main">{p.name}</span><span className="pk-cell-sub">{p.workload_type ?? ""}</span></> },
  { key: "schedule", header: "Schedule", sortValue: (p) => p.schedule_frequency, render: (p) => (p.schedule_frequency ? `${titleCaseWords(p.schedule_frequency)}${p.schedule_times.length ? ` at ${p.schedule_times.join(", ")} UTC` : ""}` : <span className="pk-muted">Not reported</span>) },
  { key: "daily", header: "Daily", numeric: true, sortValue: (p) => p.daily_retention_days, render: (p) => retention(p.daily_retention_days) },
  { key: "weekly", header: "Weekly", numeric: true, sortValue: (p) => p.weekly_retention_weeks, render: (p) => (p.weekly_retention_weeks === null ? "—" : `${p.weekly_retention_weeks} wk`) },
  { key: "monthly", header: "Monthly", numeric: true, sortValue: (p) => p.monthly_retention_months, render: (p) => (p.monthly_retention_months === null ? "—" : `${p.monthly_retention_months} mo`) },
  { key: "yearly", header: "Yearly", numeric: true, sortValue: (p) => p.yearly_retention_years, render: (p) => (p.yearly_retention_years === null ? "—" : `${p.yearly_retention_years} y`) },
  { key: "items", header: "Items", numeric: true, sortValue: (p) => p.items_assigned, render: (p) => p.items_assigned },
];

const jobColumns: Column<BackupJob>[] = [
  { key: "started", header: "Started", sortValue: (j) => j.started_at, render: (j) => <span title={j.started_at ?? undefined}>{ago(j.started_at)}</span> },
  { key: "item", header: "Resource", sortValue: (j) => j.item_name, render: (j) => <><span className="pk-cell-main">{j.item_name ?? "—"}</span><span className="pk-cell-sub">{j.operation ?? ""}</span></> },
  { key: "status", header: "Status", sortValue: (j) => j.status, render: (j) => <Pill tone={jobTone(j.status)}>{titleCaseWords(j.status)}</Pill> },
  { key: "duration", header: "Duration", numeric: true, sortValue: (j) => j.duration_seconds, render: (j) => duration(j.duration_seconds) },
  { key: "error", header: "Error", render: (j) => (j.error_code ? <span style={{ color: "var(--ecc-bad)" }} title={j.error_message ?? undefined}>{j.error_code}</span> : <span className="pk-muted">—</span>) },
];

export default function BackupVaultPage() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const [tab, setTab] = useState<Tab>("overview");
  const scope = { storage_id: id };
  const vault = useQuery({ queryKey: ["backup", "vault", id], queryFn: () => getBackupVault(id), enabled: !!id, retry: false, refetchInterval: 120_000 });
  const ok = !!vault.data;
  const items = useQuery({ queryKey: ["backup", "items", scope], queryFn: () => listBackupItems({}, scope), enabled: ok });
  const policies = useQuery({ queryKey: ["backup", "policies", scope], queryFn: () => listBackupPolicies(scope), enabled: ok });
  const jobs = useQuery({ queryKey: ["backup", "jobs", scope, 30], queryFn: () => listBackupJobs({ days: 30, limit: 500 }, scope), enabled: ok && tab === "jobs" });
  const history = useQuery({ queryKey: ["backup", "storage-history", scope], queryFn: () => getBackupStorageHistory(30, scope), enabled: ok });
  const security = useQuery({ queryKey: ["backup", "security", scope], queryFn: () => getBackupSecurity(scope), enabled: ok });
  const crumbs = (last: string) => [
    { label: "Backup Center", href: "/infrastructure/backup" },
    { label: "Vaults & Storage", href: "/infrastructure/backup/vaults" },
    { label: last },
  ];

  if (vault.isError) {
    const notFound = vault.error instanceof ApiError && vault.error.status === 404;
    return (
      <PageShell title="Backup storage" icon={DatabaseBackup} crumbs={crumbs(notFound ? "Not found" : "Unavailable")}>
        <Banner tone="warn">{notFound ? "This backup storage does not exist or is not part of your organization." : `The backup storage could not be loaded${vault.error?.message ? `: ${vault.error.message}` : "."}`}</Banner>
        <p><Link className="ecc-link" href="/infrastructure/backup/vaults">Back to Vaults & Storage</Link></p>
      </PageShell>
    );
  }

  const v = vault.data;
  const checks = security.data?.locations[0];
  const free = v?.capacity_bytes && v.free_bytes !== null ? Math.round((v.free_bytes / v.capacity_bytes) * 100) : null;

  return (
    <PageShell
      title={v?.name ?? "Backup storage"}
      icon={DatabaseBackup}
      crumbs={crumbs(v?.name ?? "Storage")}
      subtitle={v ? [titleCaseWords(v.provider), titleCaseWords(v.kind), v.location].filter(Boolean).join(" · ") : undefined}
      meta={v?.observed_at ? `Collected ${ago(v.observed_at)}` : undefined}
    >
      <StatStrip>
        <StatTile label="Backup instances" info="Items backed up to this storage." value={v ? String(v.items_total) : "—"} href={v ? `/infrastructure/backup/instances?storage_id=${id}` : undefined} loading={vault.isLoading} />
        <StatTile label="Outside RPO" info="Items breaching the RPO or never backed up." value={v ? String(v.items_breaching_rpo) : "—"} alarm={(v?.items_breaching_rpo ?? 0) > 0} href={v ? `/infrastructure/backup/instances?storage_id=${id}&rpo=breached` : undefined} loading={vault.isLoading} />
        <StatTile label="Storage used" info="Backup storage the provider reports for this location." value={v?.storage_used_bytes != null ? formatBytes(v.storage_used_bytes) : "—"} naText="Not reported" sub={free !== null ? `${free}% free of ${formatBytes(v!.capacity_bytes!)}` : undefined} alarm={free !== null && free < 15} loading={vault.isLoading} />
        <StatTile label="Security checks" info="Checks with a verdict that pass; settings the provider does not report are not counted." value={checks?.evaluated ? `${checks.passed} of ${checks.evaluated}` : "—"} naText="Not reported" loading={security.isLoading} />
      </StatStrip>

      <Panel
        title={{ overview: "Overview", instances: "Backup instances", policies: "Backup policies", jobs: "Jobs (30 days)", storage: "Storage", security: "Security" }[tab]}
        info="Configuration, contents and posture of this storage location as the provider reports it."
        aside={
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            label="Storage view"
            tabs={[
              { key: "overview", label: "Overview" },
              { key: "instances", label: "Instances", count: items.data?.length },
              { key: "policies", label: "Policies", count: policies.data?.length },
              { key: "jobs", label: "Jobs" },
              { key: "storage", label: "Storage" },
              { key: "security", label: "Security" },
            ]}
          />
        }
        state={!v ? "loading" : "ready"}
        className="pk-col-12"
      >
        {v && tab === "overview" && (
          <Props rows={[
            ["Provider", titleCaseWords(v.provider)],
            ["Type", titleCaseWords(v.kind)],
            ["Subscription", v.subscription_id],
            ["Resource group", v.resource_group],
            ["Region", v.location],
            ["Storage redundancy", titleCaseWords(v.storage_redundancy) || null],
            ["Off-site copy", flag(v.offsite).text],
            ["Soft delete", posture(v.soft_delete_state, ["enabled", "always_on"]).text],
            ["Immutability", titleCaseWords(v.immutability_state) || (v.immutable ? "Immutable" : null)],
            ["Encryption key", v.encryption === "customer_managed" ? "Customer-managed" : v.encryption === "platform_managed" ? "Platform-managed" : null],
            ["Public network access", titleCaseWords(v.public_network_access) || null],
            ["Policies", String(v.policies)],
            ["Inventory resource", v.device_id ? <Link key="d" className="ecc-link" href={`/infrastructure/hosts/${v.device_id}`}>Open in inventory</Link> : null],
          ]} />
        )}
        {tab === "instances" && (
          <DataTable caption="Backup instances in this storage" columns={itemColumns} rows={items.data ?? []} rowKey={(i) => i.id} rowHref={(i) => `/infrastructure/backup/items/${i.id}`} defaultSort={{ key: "rpo", dir: "desc" }} pageSize={50} maxHeight={520} emptyText="No backup instance in this storage." />
        )}
        {tab === "policies" && (
          <DataTable caption="Backup policies in this storage" columns={policyColumns} rows={policies.data ?? []} rowKey={(p) => p.id} rowHref={() => `/infrastructure/backup/policies?storage_id=${id}`} defaultSort={{ key: "name", dir: "asc" }} maxHeight={520} emptyText="No backup policy reported for this storage." />
        )}
        {tab === "jobs" && (
          <>
            <p className="pk-help"><Link className="ecc-link" href={`/infrastructure/backup/jobs?storage_id=${id}&days=30`}>Open in Backup Jobs</Link> for filters, job details and export.</p>
            <DataTable caption="Jobs in this storage" columns={jobColumns} rows={jobs.data ?? []} rowKey={(j) => j.id} defaultSort={{ key: "started", dir: "desc" }} pageSize={50} maxHeight={520} emptyText={jobs.isLoading ? "Loading…" : "No job in the last 30 days."} />
          </>
        )}
        {tab === "storage" && v && history.data && <StorageTrend history={history.data} asOf={new Date(history.dataUpdatedAt).toISOString()} />}
        {tab === "security" && (
          checks ? (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
              {checks.checks.map((c) => (
                <li key={c.key} style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid var(--ecc-border)", paddingBottom: 8 }}>
                  <span><strong>{c.label}</strong>{c.value && <span className="pk-muted"> · {c.value}</span>}</span>
                  <Pill tone={CHECK_TONE[c.status]}>{CHECK_TEXT[c.status]}</Pill>
                </li>
              ))}
            </ul>
          ) : <p className="pk-muted pk-empty-note">{security.isLoading ? "Loading…" : "No security settings reported."}</p>
        )}
      </Panel>
      <p className="pk-help">Diagnostic settings, activity log and access control for this storage are not collected yet; they arrive with the Azure Monitor and Log Analytics phases.</p>
    </PageShell>
  );
}
