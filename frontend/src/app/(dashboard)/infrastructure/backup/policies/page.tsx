"use client";

import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import { getBackupOverview, listBackupPolicies, type BackupFilters, type BackupPolicyRow } from "@/lib/api";
import { estimateRetention, horizonLabel, type RetentionTiers } from "@/lib/backupRetention";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, SearchBox, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { PanelHeading, Props, SidePanel, retention, workloadLabel } from "../ui";

/**
 * /infrastructure/backup/policies — every backup policy (schedule and
 * grandfather-father-son retention) across the connected storage, the items
 * stored under it, and a retention simulation: how far back restore points
 * reach and roughly how many exist if the tiers change. The simulation is
 * arithmetic only; it changes nothing (policy edits come with the write phase).
 */

const SERVER_KEYS = ["provider", "subscription_id", "resource_group", "location", "storage_id", "workload_type"] as const;

function schedule(p: BackupPolicyRow): string | null {
  if (!p.schedule_frequency) return null;
  return `${titleCaseWords(p.schedule_frequency)}${p.schedule_times.length ? ` at ${p.schedule_times.join(", ")} UTC` : ""}`;
}

function tiersOf(p: BackupPolicyRow): RetentionTiers {
  return { daily_days: p.daily_retention_days, weekly_weeks: p.weekly_retention_weeks, monthly_months: p.monthly_retention_months, yearly_years: p.yearly_retention_years };
}

const columns: Column<BackupPolicyRow>[] = [
  { key: "name", header: "Policy", sortValue: (p) => p.name, render: (p) => <><span className="pk-cell-main">{p.name}</span><span className="pk-cell-sub">{[p.vault_name, titleCaseWords(p.provider)].join(" · ")}</span></> },
  { key: "workload", header: "Workload", sortValue: (p) => p.workload_type, render: (p) => (p.workload_type ? workloadLabel(p.workload_type) : <span className="pk-muted">—</span>) },
  { key: "schedule", header: "Schedule", sortValue: (p) => p.schedule_frequency, render: (p) => schedule(p) ?? <span className="pk-muted">Not reported</span> },
  { key: "daily", header: "Daily", numeric: true, sortValue: (p) => p.daily_retention_days, render: (p) => retention(p.daily_retention_days) },
  { key: "weekly", header: "Weekly", numeric: true, sortValue: (p) => p.weekly_retention_weeks, render: (p) => (p.weekly_retention_weeks === null ? "—" : `${p.weekly_retention_weeks} wk`) },
  { key: "monthly", header: "Monthly", numeric: true, sortValue: (p) => p.monthly_retention_months, render: (p) => (p.monthly_retention_months === null ? "—" : `${p.monthly_retention_months} mo`) },
  { key: "yearly", header: "Yearly", numeric: true, sortValue: (p) => p.yearly_retention_years, render: (p) => (p.yearly_retention_years === null ? "—" : `${p.yearly_retention_years} y`) },
  { key: "horizon", header: "Oldest point", numeric: true, sortValue: (p) => estimateRetention(tiersOf(p), p.schedule_frequency === "weekly" ? "weekly" : "daily").horizon_days, render: (p) => horizonLabel(estimateRetention(tiersOf(p), p.schedule_frequency === "weekly" ? "weekly" : "daily").horizon_days) },
  {
    key: "items", header: "Items", numeric: true, sortValue: (p) => p.items_assigned,
    render: (p) => <span title={p.protected_items_count !== null ? `The provider reports ${p.protected_items_count} protected items on this policy` : undefined}>{p.items_assigned}</span>,
  },
];

const CSV: CsvColumn<BackupPolicyRow>[] = [
  { header: "Policy", value: (p) => p.name },
  { header: "Storage", value: (p) => p.vault_name },
  { header: "Provider", value: (p) => p.provider },
  { header: "Workload", value: (p) => p.workload_type },
  { header: "Schedule", value: (p) => schedule(p) },
  { header: "Daily retention (days)", value: (p) => p.daily_retention_days },
  { header: "Weekly retention (weeks)", value: (p) => p.weekly_retention_weeks },
  { header: "Monthly retention (months)", value: (p) => p.monthly_retention_months },
  { header: "Yearly retention (years)", value: (p) => p.yearly_retention_years },
  { header: "Items stored under it", value: (p) => p.items_assigned },
  { header: "Protected items (provider)", value: (p) => p.protected_items_count },
];

const TIER_FIELDS: { key: keyof RetentionTiers; label: string; max: number }[] = [
  { key: "daily_days", label: "Daily (days)", max: 9999 },
  { key: "weekly_weeks", label: "Weekly (weeks)", max: 5163 },
  { key: "monthly_months", label: "Monthly (months)", max: 1188 },
  { key: "yearly_years", label: "Yearly (years)", max: 99 },
];

function Simulation({ policy, onClose }: { policy: BackupPolicyRow; onClose: () => void }) {
  const frequency = policy.schedule_frequency === "weekly" ? "weekly" : "daily";
  const current = tiersOf(policy);
  const [proposed, setProposed] = useState<RetentionTiers>(current);
  const now = estimateRetention(current, frequency);
  const next = estimateRetention(proposed, frequency);
  const delta = (a: number, b: number) => (b === a ? "no change" : `${b > a ? "+" : ""}${b - a}`);
  const set = (key: keyof RetentionTiers) => (e: { target: { value: string } }) => {
    const v = e.target.value.trim();
    setProposed((p) => ({ ...p, [key]: v === "" ? null : Math.max(0, Math.floor(Number(v)) || 0) }));
  };

  return (
    <SidePanel title={policy.name} subtitle={[policy.vault_name, schedule(policy)].filter(Boolean).join(" · ")} onClose={onClose}>
      <PanelHeading>Policy</PanelHeading>
      <Props rows={[
        ["Workload", policy.workload_type ? workloadLabel(policy.workload_type) : null],
        ["Schedule", schedule(policy)],
        ["Items stored under it", String(policy.items_assigned)],
        ["Protected items (provider)", policy.protected_items_count?.toString() ?? null],
        ["Last collected", policy.observed_at ? ago(policy.observed_at) : null],
      ]} />
      <PanelHeading>Retention simulation</PanelHeading>
      <p className="pk-help">
        Change the tiers to see how far back restore points would reach and roughly how many would exist once the policy has run that long.
        Arithmetic only: nothing is changed in {titleCaseWords(policy.provider)}, and storage is not estimated (it depends on how much the data changes).
      </p>
      <div className="pk-form" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {TIER_FIELDS.map((f) => (
          <div key={f.key} className="pk-field">
            <label htmlFor={`sim-${f.key}`}>{f.label}</label>
            <input
              id={`sim-${f.key}`}
              className="pk-input"
              type="number"
              min={0}
              max={f.max}
              value={proposed[f.key] ?? ""}
              placeholder="Not used"
              disabled={f.key === "daily_days" && frequency === "weekly"}
              onChange={set(f.key)}
            />
          </div>
        ))}
      </div>
      <table className="pk-table" style={{ marginTop: 12 }}>
        <caption className="ecc-sr-only">Current and proposed retention</caption>
        <thead><tr><th scope="col"></th><th scope="col" className="pk-num">Current</th><th scope="col" className="pk-num">Proposed</th><th scope="col" className="pk-num">Change</th></tr></thead>
        <tbody>
          <tr><th scope="row">Oldest restore point</th><td className="pk-num">{horizonLabel(now.horizon_days)}</td><td className="pk-num">{horizonLabel(next.horizon_days)}</td><td className="pk-num">{next.horizon_days === now.horizon_days ? "no change" : `${next.horizon_days > now.horizon_days ? "+" : ""}${next.horizon_days - now.horizon_days} days`}</td></tr>
          <tr><th scope="row">Restore points per item</th><td className="pk-num">~{now.total}</td><td className="pk-num">~{next.total}</td><td className="pk-num">{delta(now.total, next.total)}</td></tr>
          <tr><th scope="row">… across {policy.items_assigned} item{policy.items_assigned === 1 ? "" : "s"}</th><td className="pk-num">~{now.total * policy.items_assigned}</td><td className="pk-num">~{next.total * policy.items_assigned}</td><td className="pk-num">{delta(now.total * policy.items_assigned, next.total * policy.items_assigned)}</td></tr>
        </tbody>
      </table>
      <p className="pk-help" style={{ marginTop: 12 }}>
        Per tier now: {now.daily} daily, {now.weekly} weekly, {now.monthly} monthly, {now.yearly} yearly (a point kept by several tiers counts once).
        Shortening retention removes existing restore points when the provider applies it. This is backup restore-point retention; Log Analytics data retention is set separately on the workspace.
      </p>
      <p style={{ marginTop: 12 }}>
        <Link className="pk-button" href={`/infrastructure/backup/instances?storage_id=${policy.vault_id}&policy=${encodeURIComponent(policy.name)}`}>View its backup instances</Link>
      </p>
    </SidePanel>
  );
}

function PoliciesContent() {
  const params = useSearchParams();
  const filters: BackupFilters = useMemo(() => Object.fromEntries(SERVER_KEYS.map((k) => [k, params.get(k) || undefined])), [params]);
  const overview = useQuery({ queryKey: ["backup", "overview", filters, 14], queryFn: () => getBackupOverview(filters), refetchInterval: 120_000 });
  const configured = !!overview.data?.configured;
  const policies = useQuery({ queryKey: ["backup", "policies", filters], queryFn: () => listBackupPolicies(filters), enabled: configured, refetchInterval: 300_000 });
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<BackupPolicyRow | null>(null);
  const rows = (policies.data ?? []).filter((p) => !search || `${p.name} ${p.vault_name} ${p.workload_type ?? ""}`.toLowerCase().includes(search.toLowerCase()));
  const unused = (policies.data ?? []).filter((p) => p.items_assigned === 0).length;

  return (
    <PageShell
      title="Backup Policies"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center", href: "/infrastructure/backup" }, { label: "Backup Policies" }]}
      subtitle="Backup schedules and how long each tier of restore points is kept, with a simulation of retention changes."
      meta={overview.data?.observed_at ? `Collected ${ago(overview.data.observed_at)}` : undefined}
    >
      {overview.data && !configured && (
        <Banner tone="info">No backup source is connected yet. <Link className="ecc-link" href="/infrastructure/backup">Connect Azure Backup or Veeam</Link> to list policies.</Banner>
      )}
      {configured && policies.data && policies.data.length > 0 && unused > 0 && (
        <Banner tone="info">{unused} polic{unused === 1 ? "y has" : "ies have"} no backup instance stored under it.</Banner>
      )}
      {configured && (
        <Panel
          title={`${rows.length} policies`}
          info="Select a policy for its details and the retention simulation. Veeam job retention is not collected yet; Veeam jobs list without tiers."
          state={policies.isLoading ? "loading" : "ready"}
          className="pk-col-12"
        >
          <div className="pk-toolbar">
            <SearchBox value={search} onChange={setSearch} placeholder="Search policy, storage or workload" label="Search policies" />
            <button type="button" className="pk-button" disabled={rows.length === 0} onClick={() => downloadCsv(`backup-policies-${new Date().toISOString().slice(0, 10)}.csv`, CSV, rows)}>Export CSV</button>
          </div>
          <DataTable caption="Backup policies" columns={columns} rows={rows} rowKey={(p) => p.id} onRowClick={(p) => setSelected(p)} defaultSort={{ key: "name", dir: "asc" }} pageSize={50} maxHeight="max(420px, calc(100vh - 400px))" emptyText={search ? "No policy matches the search." : "No backup policy reported."} />
        </Panel>
      )}
      {selected && <Simulation key={selected.id} policy={selected} onClose={() => setSelected(null)} />}
    </PageShell>
  );
}

export default function BackupPoliciesPage() {
  return (
    <Suspense>
      <PoliciesContent />
    </Suspense>
  );
}
