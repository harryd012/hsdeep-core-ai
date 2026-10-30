"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import {
  getBackupOverview,
  listBackupItems,
  listBackupJobs,
  listBackupVaults,
  type BackupItem,
  type BackupJob,
  type BackupOverview,
  type BackupRpoState,
  type BackupVault,
} from "@/lib/api";
import { createMonitoringSource, syncMonitoringSource } from "@/lib/monitoringApi";
import { DonutRing, Legend } from "@/components/command-center/primitives";
import {
  Banner,
  DataTable,
  DayColumns,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  titleCaseWords,
  type Column,
  type Tone,
} from "@/components/ui/PageKit";

const RPO: { key: BackupRpoState; label: string; color: string; tone: Tone }[] = [
  { key: "within", label: "Within RPO", color: "var(--ecc-good)", tone: "good" },
  { key: "breached", label: "RPO breached", color: "var(--ecc-bad)", tone: "bad" },
  { key: "never", label: "Never backed up", color: "var(--ecc-warn)", tone: "warn" },
  { key: "not_protected", label: "Not protected", color: "var(--ecc-unknown)", tone: "neutral" },
];
const JOB_SERIES = [
  { key: "completed", label: "Completed", color: "var(--ecc-good)" },
  { key: "completed_with_warnings", label: "With warnings", color: "var(--ecc-warn)" },
  { key: "failed", label: "Failed", color: "var(--ecc-bad)" },
  { key: "other", label: "Other", color: "var(--ecc-unknown)" },
];

function jobTone(status: string): Tone {
  if (status === "completed") return "good";
  if (status === "completed_with_warnings") return "warn";
  if (status === "failed") return "bad";
  if (status === "in_progress") return "info";
  return "neutral";
}

function hours(h: number | null): string {
  if (h === null) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${Math.round(h / 24)} d`;
}

function duration(s: number | null): string {
  if (s === null) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** Posture label + tone: what Azure reported, never a guess. */
function posture(value: string | null, good: string[], label = titleCaseWords): { text: string; tone: Tone } {
  if (!value) return { text: "Not reported", tone: "neutral" };
  return { text: label(value), tone: good.includes(value) ? "good" : "warn" };
}

/** Connect an Azure subscription: creates an `azure_backup` monitoring source
 *  through the existing sources API, then runs one collection immediately. */
function ConnectAzureBackup({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ name: "Azure Backup", tenant_id: "", subscription_id: "", client_id: "", secret: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad" | "warn"; text: string } | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const ready = form.name && form.tenant_id && form.subscription_id && form.client_id && form.secret;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setMessage(null);
    try {
      const source = await createMonitoringSource({
        name: form.name,
        source_type: "azure_backup",
        is_active: true,
        poll_interval_seconds: 900,
        config: { tenant_id: form.tenant_id.trim(), subscription_id: form.subscription_id.trim(), client_id: form.client_id.trim() },
        credential: form.secret,
      });
      setForm((f) => ({ ...f, secret: "" }));
      const sync = await syncMonitoringSource(source.id).catch((err: unknown) => ({ status: "error", error: err instanceof Error ? err.message : String(err) }));
      setMessage(
        sync.status === "error"
          ? { tone: "warn", text: `Source saved, but the first collection failed: ${sync.error ?? "unknown error"}. Check the IDs, the secret and the Backup Reader role.` }
          : { tone: "good", text: "Connected. The first collection finished; it repeats every 15 minutes." },
      );
      onDone();
    } catch (err) {
      setMessage({ tone: "bad", text: err instanceof Error ? err.message : "Could not save the source." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="pk-form pk-form--wide" onSubmit={submit}>
      <p className="pk-help">
        Read-only. Create an app registration in Microsoft Entra ID, give its service principal the built-in <strong>Backup Reader</strong> role
        on the subscription, and paste its IDs and a client secret below. The secret is encrypted at rest and never shown again.
      </p>
      <div className="pk-field"><label htmlFor="bk-name">Name</label><input id="bk-name" className="pk-input" value={form.name} onChange={set("name")} required /></div>
      <div className="pk-field"><label htmlFor="bk-tenant">Directory (tenant) ID</label><input id="bk-tenant" className="pk-input" value={form.tenant_id} onChange={set("tenant_id")} placeholder="00000000-0000-0000-0000-000000000000" required /></div>
      <div className="pk-field"><label htmlFor="bk-sub">Subscription ID</label><input id="bk-sub" className="pk-input" value={form.subscription_id} onChange={set("subscription_id")} required /></div>
      <div className="pk-field"><label htmlFor="bk-client">Application (client) ID</label><input id="bk-client" className="pk-input" value={form.client_id} onChange={set("client_id")} required /></div>
      <div className="pk-field"><label htmlFor="bk-secret">Client secret</label><input id="bk-secret" className="pk-input" type="password" autoComplete="new-password" value={form.secret} onChange={set("secret")} required /></div>
      <div>
        <button type="submit" className="pk-button pk-button--primary" disabled={!ready || busy}>{busy ? "Connecting…" : "Connect and collect"}</button>
      </div>
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
    </form>
  );
}

export default function BackupPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"items" | "jobs" | "vaults">("items");
  const [rpoFilter, setRpoFilter] = useState<BackupRpoState | "all">("all");
  const [showConnect, setShowConnect] = useState(false);

  const overview = useQuery({ queryKey: ["backup", "overview"], queryFn: getBackupOverview, refetchInterval: 60_000 });
  const o: BackupOverview | undefined = overview.data;
  const configured = !!o?.configured;
  const items = useQuery({ queryKey: ["backup", "items"], queryFn: () => listBackupItems(), enabled: configured, refetchInterval: 60_000 });
  const jobs = useQuery({ queryKey: ["backup", "jobs"], queryFn: () => listBackupJobs({ days: 7, limit: 500 }), enabled: configured, refetchInterval: 60_000 });
  const vaults = useQuery({ queryKey: ["backup", "vaults"], queryFn: listBackupVaults, enabled: configured, refetchInterval: 120_000 });
  const refresh = () => qc.invalidateQueries({ queryKey: ["backup"] });

  const itemRows = useMemo(() => (items.data ?? []).filter((i) => rpoFilter === "all" || i.rpo_state === rpoFilter), [items.data, rpoFilter]);
  const rpoCounts = o
    ? { within: o.items_within_rpo, breached: o.items_breaching_rpo, never: o.items_never_backed_up, not_protected: o.items_not_protected }
    : null;
  const failingSource = o?.sources.find((s) => s.status === "error" || s.last_error);
  const stale = o?.observed_at ? (Date.parse(o.generated_at) - Date.parse(o.observed_at)) / 3_600_000 > 2 : false;

  const itemColumns: Column<BackupItem>[] = [
    {
      key: "name",
      header: "Protected item",
      sortValue: (i) => i.name,
      render: (i) => (
        <>
          <span className="pk-cell-main">{i.name}</span>
          <span className="pk-cell-sub">{[i.workload_type, i.vault_name].filter(Boolean).join(" · ")}</span>
        </>
      ),
    },
    {
      key: "rpo",
      header: "RPO",
      sortValue: (i) => RPO.findIndex((r) => r.key === i.rpo_state),
      render: (i) => {
        const r = RPO.find((x) => x.key === i.rpo_state)!;
        return <Pill tone={r.tone}>{r.label}</Pill>;
      },
    },
    { key: "age", header: "Last backup", numeric: true, sortValue: (i) => i.age_hours, render: (i) => (i.last_backup_at ? <span title={i.last_backup_at}>{hours(i.age_hours)} ago</span> : <span className="pk-muted">Never</span>) },
    { key: "result", header: "Last result", sortValue: (i) => i.last_backup_status, render: (i) => (i.last_backup_status ? <Pill tone={jobTone(i.last_backup_status)}>{titleCaseWords(i.last_backup_status)}</Pill> : <span className="pk-muted">—</span>) },
    { key: "protection", header: "Protection", sortValue: (i) => i.protection_state, render: (i) => titleCaseWords(i.protection_state) || "—" },
    { key: "policy", header: "Policy", sortValue: (i) => i.policy_name, render: (i) => i.policy_name ?? <span className="pk-muted">—</span> },
  ];

  const jobColumns: Column<BackupJob>[] = [
    { key: "started", header: "Started", sortValue: (j) => j.started_at, render: (j) => <span className="pk-muted" title={j.started_at ?? undefined}>{ago(j.started_at)}</span> },
    {
      key: "item",
      header: "Item",
      sortValue: (j) => j.item_name,
      render: (j) => (
        <>
          <span className="pk-cell-main">{j.item_name ?? "—"}</span>
          <span className="pk-cell-sub">{[j.operation, j.vault_name].filter(Boolean).join(" · ")}</span>
        </>
      ),
    },
    { key: "status", header: "Status", sortValue: (j) => j.status, render: (j) => <Pill tone={jobTone(j.status)}>{titleCaseWords(j.status)}</Pill> },
    { key: "duration", header: "Duration", numeric: true, sortValue: (j) => j.duration_seconds, render: (j) => duration(j.duration_seconds) },
    {
      key: "error",
      header: "Error",
      render: (j) =>
        j.error_code ? (
          <span className="pk-truncate" style={{ display: "block", maxWidth: 420, color: "var(--ecc-bad)" }} title={j.error_message ?? undefined}>
            {j.error_code}
            {j.error_message ? ` · ${j.error_message}` : ""}
          </span>
        ) : (
          <span className="pk-muted">—</span>
        ),
    },
  ];

  const vaultColumns: Column<BackupVault>[] = [
    {
      key: "name",
      header: "Vault",
      sortValue: (v) => v.name,
      render: (v) => (
        <>
          <span className="pk-cell-main">{v.name}</span>
          <span className="pk-cell-sub">{[v.resource_group, v.location].filter(Boolean).join(" · ")}</span>
        </>
      ),
    },
    { key: "items", header: "Items", numeric: true, sortValue: (v) => v.items_total, render: (v) => v.items_total },
    { key: "breach", header: "Outside RPO", numeric: true, sortValue: (v) => v.items_breaching_rpo, render: (v) => (v.items_breaching_rpo ? <span style={{ color: "var(--ecc-bad)" }}>{v.items_breaching_rpo}</span> : "0") },
    { key: "soft", header: "Soft delete", sortValue: (v) => v.soft_delete_state, render: (v) => { const p = posture(v.soft_delete_state, ["enabled", "always_on"]); return <Pill tone={p.tone}>{p.text}</Pill>; } },
    { key: "immut", header: "Immutability", sortValue: (v) => v.immutability_state, render: (v) => { const p = posture(v.immutability_state, ["locked", "unlocked"]); return <Pill tone={p.tone}>{p.text}</Pill>; } },
    { key: "redundancy", header: "Storage redundancy", sortValue: (v) => v.storage_redundancy, render: (v) => titleCaseWords(v.storage_redundancy) || <span className="pk-muted">Not reported</span> },
    { key: "seen", header: "Last collected", sortValue: (v) => v.observed_at, render: (v) => <span className="pk-muted">{ago(v.observed_at)}</span> },
  ];

  const loadingOverview = overview.isLoading;
  const n = (v: number | undefined) => (v === undefined || !configured ? "—" : v.toLocaleString("en-US"));

  return (
    <PageShell
      title="Backup"
      icon={DatabaseBackup}
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Backup" }]}
      subtitle="Azure Backup: whether every protected workload has a recent good backup, what failed and how the vaults are secured."
      actions={
        configured ? (
          <button type="button" className="pk-button" onClick={() => setShowConnect((s) => !s)}>
            {showConnect ? "Close" : "Add subscription"}
          </button>
        ) : undefined
      }
      meta={o?.observed_at ? `Collected ${ago(o.observed_at)}` : undefined}
    >
      {overview.error && <Banner tone="bad">Backup data could not be loaded: {overview.error instanceof Error ? overview.error.message : "request failed"}.</Banner>}
      {failingSource && <Banner tone="bad">Source “{failingSource.name}” is failing{failingSource.last_error ? `: ${failingSource.last_error}` : ""}. Figures below are from its last successful collection.</Banner>}
      {configured && !failingSource && stale && <Banner tone="warn">The last successful collection was {ago(o!.observed_at)}. Figures may be out of date.</Banner>}

      {o && !configured && (
        <Panel title="Connect Azure Backup" info="Adds an Azure Backup monitoring source for one subscription." state="ready" className="pk-col-12">
          <ConnectAzureBackup onDone={refresh} />
        </Panel>
      )}
      {configured && showConnect && (
        <Panel title="Add an Azure subscription" info="Each subscription is one source; vaults from every source are combined below." state="ready" className="pk-col-12">
          <ConnectAzureBackup onDone={() => { refresh(); }} />
        </Panel>
      )}

      <StatStrip>
        <StatTile label="Protected items" value={n(o?.items_protected)} info="Items under active backup protection." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Within RPO" value={n(o?.items_within_rpo)} tone="good" sub={o && configured && o.items_protected ? `${Math.round((o.items_within_rpo / o.items_protected) * 100)}% of protected` : undefined} info={`Protected items with a backup in the last ${o?.rpo_hours ?? 24} hours.`} loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="RPO breached" value={n(o?.items_breaching_rpo)} alarm={(o?.items_breaching_rpo ?? 0) > 0} info={`Protected items whose last backup is older than ${o?.rpo_hours ?? 24} hours.`} loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Never backed up" value={n(o?.items_never_backed_up)} tone="warn" info="Protected items with no backup yet (for example, initial backup pending)." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Last backup failed" value={n(o?.items_last_failed)} alarm={(o?.items_last_failed ?? 0) > 0} info="Items whose most recent backup failed." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Job success" badge="24h" value={configured && o?.success_rate_24h !== null && o?.success_rate_24h !== undefined ? `${o.success_rate_24h}%` : "—"} naText={configured ? "No job finished in 24h" : "Not configured"} sub={o && configured ? `${o.jobs_24h.failed} failed of ${o.jobs_24h.total}` : undefined} info="Completed (including with warnings) over finished jobs in the last 24 hours." loading={loadingOverview} />
        <StatTile label="Vaults" value={n(o?.vaults)} sub={o && configured && o.vaults_soft_delete_off ? `${o.vaults_soft_delete_off} with soft delete off` : undefined} alarm={(o?.vaults_soft_delete_off ?? 0) > 0} info="Recovery Services vaults read from the connected subscriptions." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
      </StatStrip>

      {configured && (
        <div className="pk-grid">
          <Panel title="Job outcomes" info="Backup jobs per day by outcome, last 14 days." aside="last 14 days" state={overview.isLoading ? "loading" : o!.jobs_7d.total === 0 && o!.daily.every((d) => d.completed + d.completed_with_warnings + d.failed + d.other === 0) ? "empty" : "ready"} emptyText="No backup job has run in the last 14 days." className="pk-col-8">
            <DayColumns
              label="Backup jobs per day"
              buckets={o!.daily.map((d) => ({ key: d.day, label: new Date(`${d.day}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }), values: { completed: d.completed, completed_with_warnings: d.completed_with_warnings, failed: d.failed, other: d.other } }))}
              series={JOB_SERIES}
              height={170}
            />
          </Panel>
          <Panel title="RPO compliance" info={`Every item by its backup recency against the ${o!.rpo_hours} h recovery-point objective.`} state={o!.items_total === 0 ? "empty" : "ready"} emptyText="No protected items reported yet." className="pk-col-4">
            {rpoCounts && (
              <div className="pk-donut-row">
                <DonutRing size={132} label="Items by RPO state" centerValue={String(o!.items_total)} centerLabel="items" segments={RPO.map((r) => ({ key: r.key, value: rpoCounts[r.key], color: r.color }))} />
                <Legend total={o!.items_total} rows={RPO.map((r) => ({ key: r.key, label: r.label, value: rpoCounts[r.key], color: r.color }))} />
              </div>
            )}
          </Panel>

          <Panel
            title={tab === "items" ? "Protected items" : tab === "jobs" ? "Backup jobs (7 days)" : "Vaults"}
            info="Select a tab for items, jobs or vault security posture."
            aside={
              <Tabs<"items" | "jobs" | "vaults">
                value={tab}
                onChange={setTab}
                label="Backup view"
                tabs={[
                  { key: "items", label: "Items", count: items.data?.length },
                  { key: "jobs", label: "Jobs", count: jobs.data?.length },
                  { key: "vaults", label: "Vaults", count: vaults.data?.length },
                ]}
              />
            }
            state={(tab === "items" ? items : tab === "jobs" ? jobs : vaults).isLoading ? "loading" : "ready"}
            className="pk-col-12"
          >
            {tab === "items" && (
              <>
                <div className="pk-toolbar">
                  <Tabs<BackupRpoState | "all">
                    value={rpoFilter}
                    onChange={setRpoFilter}
                    label="RPO filter"
                    tabs={[{ key: "all", label: "All" }, ...RPO.map((r) => ({ key: r.key, label: r.label, count: rpoCounts?.[r.key] }))]}
                  />
                </div>
                <DataTable caption="Protected items" columns={itemColumns} rows={itemRows} rowKey={(i) => i.id} defaultSort={{ key: "rpo", dir: "desc" }} maxHeight="max(420px, calc(100vh - 520px))" emptyText={rpoFilter === "all" ? "No protected items reported." : "No item in this state."} />
              </>
            )}
            {tab === "jobs" && (
              <DataTable caption="Backup jobs" columns={jobColumns} rows={jobs.data ?? []} rowKey={(j) => j.id} defaultSort={{ key: "started", dir: "desc" }} maxHeight="max(420px, calc(100vh - 480px))" emptyText="No backup job in the last 7 days." />
            )}
            {tab === "vaults" && (
              <DataTable caption="Vaults" columns={vaultColumns} rows={vaults.data ?? []} rowKey={(v) => v.id} rowHref={(v) => (v.device_id ? `/infrastructure/hosts/${v.device_id}` : null)} maxHeight={480} emptyText="No vault reported." />
            )}
          </Panel>
        </div>
      )}
    </PageShell>
  );
}
