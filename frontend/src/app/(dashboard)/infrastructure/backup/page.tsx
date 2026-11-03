"use client";

import { useMemo, useState, useSyncExternalStore, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import {
  getBackupCoverage,
  getBackupFailures,
  getBackupOverview,
  getBackupSecurity,
  getBackupStorageHistory,
  listBackupItems,
  listBackupJobs,
  listBackupVaults,
  updateBackupSettings,
  type BackupCheckStatus,
  type BackupCoverage,
  type BackupCoverageState,
  type BackupFailureGroup,
  type BackupFilterOptions,
  type BackupFilters,
  type BackupItem,
  type BackupJob,
  type BackupOverview,
  type BackupRpoState,
  type BackupSecurityPosture,
  type BackupStatusGroup,
  type BackupStorageHistory,
  type BackupVault,
  type BackupWorkloadSummary,
} from "@/lib/api";
import { downloadCsv, formatBytes, type CsvColumn } from "@/lib/csv";
import { useSiteHealth } from "@/hooks/useSiteHealth";
import { useAuth } from "@/lib/auth-context";
import { createMonitoringSource, syncMonitoringSource } from "@/lib/monitoringApi";
import { DonutRing, Legend } from "@/components/command-center/primitives";
import { instancesHref } from "./instancesHref";
import {
  Banner,
  BarList,
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
const COVERAGE: { key: BackupCoverageState; label: string; tone: Tone; info: string }[] = [
  { key: "unprotected", label: "Unprotected", tone: "bad", info: "In a connected subscription, but no backup item protects it." },
  { key: "protection_stopped", label: "Protection stopped", tone: "warn", info: "Has a backup item, but protection is stopped or paused." },
  { key: "not_monitored", label: "Not monitored", tone: "neutral", info: "Its subscription has no backup source connected, so its backups cannot be seen." },
  { key: "protected", label: "Protected", tone: "good", info: "Under active backup protection." },
];
type CoverageRow = BackupCoverage["resources"][number];
const JOB_SERIES = [
  { key: "completed", label: "Completed", color: "var(--ecc-good)" },
  { key: "completed_with_warnings", label: "With warnings", color: "var(--ecc-warn)" },
  { key: "failed", label: "Failed", color: "var(--ecc-bad)" },
  { key: "other", label: "Other", color: "var(--ecc-unknown)" },
];

/** Provider workload names in words; anything else is title-cased. */
const WORKLOAD_LABELS: Record<string, string> = {
  VM: "Virtual machines",
  SQLDataBase: "SQL databases",
  SAPHanaDatabase: "SAP HANA databases",
  SAPHanaDBInstance: "SAP HANA instances",
  SAPAseDatabase: "SAP ASE databases",
  AzureFileShare: "Azure file shares",
  FileFolder: "Files and folders",
  AzureSqlDb: "Azure SQL databases",
  Exchange: "Exchange",
  Sharepoint: "SharePoint",
};

function workloadLabel(w: string | null): string {
  return w === null ? "Not reported" : WORKLOAD_LABELS[w] ?? titleCaseWords(w);
}

const CHECK_TONE: Record<BackupCheckStatus, Tone> = {
  pass: "good",
  warn: "warn",
  fail: "bad",
  not_reported: "neutral",
  not_applicable: "neutral",
};
const CHECK_TEXT: Record<BackupCheckStatus, string> = {
  pass: "Pass",
  warn: "Review",
  fail: "Fail",
  not_reported: "Not reported",
  not_applicable: "Not applicable",
};

type FilterKey = keyof BackupFilters;

/** Command Center filters: each select appears only when it has a choice to make. */
function FilterBar({ options, value, onChange }: { options: BackupFilterOptions; value: BackupFilters; onChange: (f: BackupFilters) => void }) {
  const selects: { key: FilterKey; label: string; all: string; values: { value: string; label: string }[] }[] = [
    { key: "provider", label: "Provider", all: "All providers", values: options.providers.map((p) => ({ value: p, label: titleCaseWords(p) })) },
    { key: "subscription_id", label: "Subscription", all: "All subscriptions", values: options.subscriptions.map((s) => ({ value: s, label: s })) },
    { key: "resource_group", label: "Resource group", all: "All resource groups", values: options.resource_groups.map((r) => ({ value: r, label: r })) },
    { key: "location", label: "Region", all: "All regions", values: options.locations.map((l) => ({ value: l, label: l })) },
    { key: "storage_id", label: "Storage", all: "All storage", values: options.vaults },
    { key: "workload_type", label: "Workload", all: "All workloads", values: options.workload_types.map((w) => ({ value: w, label: workloadLabel(w) })) },
  ];
  const shown = selects.filter((s) => s.values.length > 1 || value[s.key]);
  if (shown.length === 0) return null;
  const active = Object.values(value).some(Boolean);
  return (
    <div className="pk-toolbar" role="group" aria-label="Backup filters">
      {shown.map((s) => (
        <select
          key={s.key}
          className="pk-input"
          aria-label={`Filter by ${s.label.toLowerCase()}`}
          value={value[s.key] ?? ""}
          onChange={(e) => onChange({ ...value, [s.key]: e.target.value || undefined })}
          style={{ width: "auto", maxWidth: 260 }}
        >
          <option value="">{s.all}</option>
          {s.values.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      ))}
      {active && (
        <button type="button" className="pk-button" onClick={() => onChange({})}>Clear filters</button>
      )}
    </div>
  );
}

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

/** Site filter: "all", "none" (no site assigned) or a site id. */
function inSite(siteId: string | null, filter: string): boolean {
  return filter === "all" || (filter === "none" ? siteId === null : siteId === filter);
}

function retention(days: number | null): string {
  if (days === null) return "—";
  return days % 365 === 0 ? `${days / 365} y` : days % 7 === 0 && days >= 14 ? `${days / 7} wk` : `${days} d`;
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

/** Yes / No / Not reported for a provider-reported flag (never inferred). */
function flag(value: boolean | null): { text: string; tone: Tone } {
  if (value === null) return { text: "Not reported", tone: "neutral" };
  return value ? { text: "Yes", tone: "good" } : { text: "No", tone: "warn" };
}

/** The tenant's RPO target. Everyone sees it; backup.write can change it or
 *  return it to the platform default. Alerts use the new value from the next poll. */
function RpoTarget({ overview, onSaved }: { overview: BackupOverview; onSaved: () => void }) {
  const { user } = useAuth();
  const canEdit = !!user && (user.is_superuser || user.permissions.includes("backup.write"));
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(overview.rpo_hours));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hoursValue = Number(value);
  const valid = Number.isInteger(hoursValue) && hoursValue >= 1 && hoursValue <= 720;

  async function save(rpoHours: number | null) {
    setBusy(true);
    setError(null);
    try {
      const saved = await updateBackupSettings(rpoHours);
      setValue(String(saved.rpo_hours));
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the RPO.");
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="pk-toolbar">
        <span className="pk-help">
          Target: <strong>{overview.rpo_hours} h</strong> {overview.rpo_is_default ? "(platform default)" : "(set for this customer)"}
        </span>
        {canEdit && (
          <button type="button" className="pk-button" onClick={() => { setValue(String(overview.rpo_hours)); setEditing(true); }}>
            Change
          </button>
        )}
      </div>
    );
  }
  return (
    <form className="pk-toolbar" onSubmit={(e) => { e.preventDefault(); if (valid) void save(hoursValue); }}>
      <label htmlFor="bk-rpo" className="pk-help">RPO (hours)</label>
      <input id="bk-rpo" className="pk-input" type="number" min={1} max={720} step={1} value={value} onChange={(e) => setValue(e.target.value)} style={{ width: 90 }} />
      <button type="submit" className="pk-button pk-button--primary" disabled={!valid || busy}>{busy ? "Saving…" : "Save"}</button>
      {!overview.rpo_is_default && <button type="button" className="pk-button" disabled={busy} onClick={() => void save(null)}>Use default</button>}
      <button type="button" className="pk-button" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
      {!valid && <span className="pk-help">1 to 720 hours.</span>}
      {error && <Banner tone="bad">{error}</Banner>}
    </form>
  );
}

/** Connect a Veeam Backup & Replication server: creates a `veeam_backup`
 *  monitoring source, then runs one collection immediately. */
function ConnectVeeam({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ name: "Veeam Backup", hostname: "", port: "9419", username: "", secret: "" });
  const [verifyTls, setVerifyTls] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad" | "warn"; text: string } | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const port = Number(form.port);
  const ready = form.name && form.hostname && form.username && form.secret && Number.isInteger(port) && port > 0 && port < 65536;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setMessage(null);
    try {
      const source = await createMonitoringSource({
        name: form.name,
        source_type: "veeam_backup",
        hostname: form.hostname.trim(),
        port,
        protocol: "https",
        username: form.username.trim(),
        verify_ssl: verifyTls,
        is_active: true,
        poll_interval_seconds: 900,
        credential: form.secret,
      });
      setForm((f) => ({ ...f, secret: "" }));
      const sync = await syncMonitoringSource(source.id).catch((err: unknown) => ({ status: "error", error: err instanceof Error ? err.message : String(err) }));
      setMessage(
        sync.status === "error"
          ? { tone: "warn", text: `Source saved, but the first collection failed: ${sync.error ?? "unknown error"}. Check the server name, port 9419 is reachable, and the user's password.` }
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
        Read-only. Uses the Veeam Backup &amp; Replication REST API (version 12 or later, port 9419). Create a Veeam user with the
        built-in <strong>Veeam Backup Viewer</strong> role and enter it below. The password is encrypted at rest and never shown again.
      </p>
      <div className="pk-field"><label htmlFor="vb-name">Name</label><input id="vb-name" className="pk-input" value={form.name} onChange={set("name")} required /></div>
      <div className="pk-field"><label htmlFor="vb-host">Backup server</label><input id="vb-host" className="pk-input" value={form.hostname} onChange={set("hostname")} placeholder="vbr01.corp.local" required /></div>
      <div className="pk-field"><label htmlFor="vb-port">REST API port</label><input id="vb-port" className="pk-input" type="number" min={1} max={65535} value={form.port} onChange={set("port")} required /></div>
      <div className="pk-field"><label htmlFor="vb-user">Username</label><input id="vb-user" className="pk-input" value={form.username} onChange={set("username")} placeholder="svc-veeam" autoComplete="off" required /></div>
      <div className="pk-field"><label htmlFor="vb-secret">Password</label><input id="vb-secret" className="pk-input" type="password" autoComplete="new-password" value={form.secret} onChange={set("secret")} required /></div>
      <div className="pk-field">
        <label htmlFor="vb-tls">TLS certificate</label>
        <span className="pk-help"><input id="vb-tls" type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} /> Verify (leave off for Veeam&apos;s default self-signed certificate)</span>
      </div>
      <div>
        <button type="submit" className="pk-button pk-button--primary" disabled={!ready || busy}>{busy ? "Connecting…" : "Connect and collect"}</button>
      </div>
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
    </form>
  );
}

type Provider = "azure" | "veeam";

/** Provider choice, then that provider's connect form. */
function ConnectBackup({ onDone }: { onDone: () => void }) {
  const [provider, setProvider] = useState<Provider>("azure");
  return (
    <>
      <div className="pk-toolbar">
        <Tabs<Provider>
          value={provider}
          onChange={setProvider}
          label="Backup provider"
          tabs={[{ key: "azure", label: "Azure Backup" }, { key: "veeam", label: "Veeam Backup & Replication" }]}
        />
      </div>
      {provider === "azure" ? <ConnectAzureBackup onDone={onDone} /> : <ConnectVeeam onDone={onDone} />}
    </>
  );
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

type BackupTab = "items" | "coverage" | "jobs" | "failures" | "vaults" | "security";
const TAB_KEYS: readonly string[] = ["items", "coverage", "jobs", "failures", "vaults", "security"];
/** The open tab lives in the URL hash (#jobs ...), so KPIs and other pages can link to it. */
function subscribeHash(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}
const RANGES = [
  { days: 1, label: "Last 24 hours" },
  { days: 7, label: "Last 7 days" },
  { days: 14, label: "Last 14 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];
const AGE_COLORS: Record<string, string> = {
  lt_24h: "var(--ecc-good)",
  "1_3d": "var(--ecc-warn)",
  "3_7d": "var(--ecc-warn)",
  "7_30d": "var(--ecc-bad)",
  gt_30d: "var(--ecc-bad)",
  never: "var(--ecc-unknown)",
};

function statusGroupColumns(header: string, notReported: string): Column<BackupStatusGroup>[] {
  return [
    { key: "key", header, sortValue: (g) => g.key, render: (g) => (g.key ? <span className="pk-truncate" title={g.key}>{g.key}</span> : <span className="pk-muted">{notReported}</span>) },
    { key: "items", header: "Items", numeric: true, sortValue: (g) => g.items_total, render: (g) => g.items_total },
    { key: "within", header: "Within RPO", numeric: true, sortValue: (g) => (g.items_protected ? g.items_within_rpo / g.items_protected : null), render: (g) => (g.items_protected ? `${Math.round((g.items_within_rpo / g.items_protected) * 100)}%` : <span className="pk-muted">—</span>) },
    { key: "breached", header: "Breached", numeric: true, sortValue: (g) => g.items_breaching_rpo + g.items_never_backed_up, render: (g) => { const v = g.items_breaching_rpo + g.items_never_backed_up; return v ? <span style={{ color: "var(--ecc-bad)" }} title={`${g.items_breaching_rpo} outside the RPO, ${g.items_never_backed_up} never backed up`}>{v}</span> : "0"; } },
    { key: "failed", header: "Failed", numeric: true, sortValue: (g) => g.items_last_failed, render: (g) => (g.items_last_failed ? <span style={{ color: "var(--ecc-bad)" }}>{g.items_last_failed}</span> : "0") },
  ];
}
const subscriptionColumns = statusGroupColumns("Subscription", "Not reported (on-premises)");
const regionColumns = statusGroupColumns("Region", "Not reported (on-premises)");
const TAB_TITLES: Record<BackupTab, string> = {
  items: "Protected items",
  coverage: "Backup coverage",
  jobs: "Backup jobs (7 days)",
  failures: "Failure analysis (7 days)",
  vaults: "Backup storage",
  security: "Security & compliance",
};

const workloadColumns: Column<BackupWorkloadSummary>[] = [
  { key: "workload", header: "Workload", sortValue: (w) => workloadLabel(w.workload_type), render: (w) => <span title={w.workload_type ?? undefined}>{workloadLabel(w.workload_type)}</span> },
  { key: "items", header: "Items", numeric: true, sortValue: (w) => w.items_total, render: (w) => w.items_total.toLocaleString("en-US") },
  { key: "protected", header: "Protected", numeric: true, sortValue: (w) => w.items_protected, render: (w) => w.items_protected.toLocaleString("en-US") },
  {
    key: "within", header: "Within RPO", numeric: true,
    sortValue: (w) => (w.items_protected ? w.items_within_rpo / w.items_protected : null),
    render: (w) => (w.items_protected ? `${w.items_within_rpo} (${Math.round((w.items_within_rpo / w.items_protected) * 100)}%)` : <span className="pk-muted">—</span>),
  },
  { key: "breached", header: "RPO breached", numeric: true, sortValue: (w) => w.items_breaching_rpo, render: (w) => (w.items_breaching_rpo ? <span style={{ color: "var(--ecc-bad)" }}>{w.items_breaching_rpo}</span> : "0") },
  { key: "never", header: "Never backed up", numeric: true, sortValue: (w) => w.items_never_backed_up, render: (w) => (w.items_never_backed_up ? <span style={{ color: "var(--ecc-warn)" }}>{w.items_never_backed_up}</span> : "0") },
  { key: "failed", header: "Last backup failed", numeric: true, sortValue: (w) => w.items_last_failed, render: (w) => (w.items_last_failed ? <span style={{ color: "var(--ecc-bad)" }}>{w.items_last_failed}</span> : "0") },
  { key: "stopped", header: "Not protected", numeric: true, sortValue: (w) => w.items_not_protected, render: (w) => w.items_not_protected },
  { key: "points", header: "Restore points", numeric: true, sortValue: (w) => w.recovery_points_total, render: (w) => (w.recovery_points_total === null ? <span className="pk-muted">—</span> : w.recovery_points_total.toLocaleString("en-US")) },
  { key: "size", header: "Backup size", numeric: true, sortValue: (w) => w.size_bytes, render: (w) => (w.size_bytes === null ? <span className="pk-muted">Not reported</span> : formatBytes(w.size_bytes)) },
];

const failureColumns: Column<BackupFailureGroup>[] = [
  { key: "code", header: "Error code", sortValue: (g) => g.error_code, render: (g) => (g.error_code ? <code>{g.error_code}</code> : <span className="pk-muted">Not reported</span>) },
  { key: "jobs", header: "Failed jobs", numeric: true, sortValue: (g) => g.failed_jobs, render: (g) => g.failed_jobs },
  {
    key: "items", header: "Items", numeric: true, sortValue: (g) => g.items_affected,
    render: (g) => <span title={g.items.join(", ") + (g.items_affected > g.items.length ? ` and ${g.items_affected - g.items.length} more` : "")}>{g.items_affected}</span>,
  },
  { key: "message", header: "Latest message", render: (g) => (g.sample_message ? <span className="pk-truncate" style={{ display: "inline-block", maxWidth: 460 }} title={g.sample_message}>{g.sample_message}</span> : <span className="pk-muted">—</span>) },
  { key: "storage", header: "Storage", render: (g) => g.vaults.join(", ") },
  { key: "last", header: "Last seen", sortValue: (g) => g.last_seen, render: (g) => <span className="pk-muted" title={g.last_seen ?? undefined}>{ago(g.last_seen)}</span> },
];

/** `asOf`: the overview's generated_at, so the last column is today on the server. */
function StorageTrend({ history, asOf }: { history: BackupStorageHistory; asOf: string }) {
  const gb = (b: number | null) => (b === null ? 0 : Math.round((b / 1024 ** 3) * 10) / 10);
  const byDay = new Map(history.points.map((p) => [p.day, p]));
  const last = history.points[history.points.length - 1];
  const start = new Date(Date.parse(asOf) - (history.days - 1) * 86_400_000);
  const buckets = Array.from({ length: history.days }, (_, i) => {
    const d = new Date(start.getTime() + i * 86_400_000).toISOString().slice(0, 10);
    const p = byDay.get(d);
    return {
      key: d,
      label: new Date(`${d}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      values: { used: gb(p?.storage_used_bytes ?? null) },
    };
  });
  return (
    <div style={{ marginBottom: 16 }}>
      <DayColumns label="Backup storage used per day" buckets={buckets} series={[{ key: "used", label: "Storage used (GB)", color: "var(--ecc-accent)" }]} height={120} format={(v) => `${v.toLocaleString("en-US")} GB`} />
      <p className="pk-help">
        {history.history_days === 0
          ? "Storage history starts with the next collection."
          : `${history.history_days} day${history.history_days === 1 ? "" : "s"} recorded${last?.storage_used_bytes != null ? `; latest ${formatBytes(last.storage_used_bytes)}` : ""}.`}{" "}
        {history.growth_bytes_per_day === null
          ? `A growth estimate needs ${history.min_history_days} days of history.`
          : `Estimated growth ${formatBytes(Math.abs(history.growth_bytes_per_day))}/day${history.growth_bytes_per_day < 0 ? " (shrinking)" : ""}; at that rate about ${formatBytes(history.projected_used_bytes_30d ?? 0)} in 30 days. This is an estimate from the recorded trend, not a forecast from the provider.`}
      </p>
    </div>
  );
}

function SecurityView({ posture }: { posture: BackupSecurityPosture }) {
  if (posture.locations.length === 0) return <p className="pk-muted pk-empty-note">No backup storage reported.</p>;
  type Location = BackupSecurityPosture["locations"][number];
  const columns: Column<Location>[] = [
    {
      key: "name", header: "Storage", sortValue: (l) => l.name,
      render: (l) => (
        <>
          {l.name}
          <div className="pk-muted">{[titleCaseWords(l.provider), titleCaseWords(l.kind), l.location].filter(Boolean).join(" · ")}</div>
        </>
      ),
    },
    ...posture.locations[0].checks.map((check, i): Column<Location> => ({
      key: check.key,
      header: check.label,
      sortValue: (l) => l.checks[i]?.status ?? null,
      render: (l) => {
        const c = l.checks[i];
        if (!c) return <span className="pk-muted">—</span>;
        const verdict = c.status === "pass" || c.status === "warn" || c.status === "fail";
        return <span title={c.value ?? CHECK_TEXT[c.status]}><Pill tone={CHECK_TONE[c.status]}>{verdict ? c.value ?? CHECK_TEXT[c.status] : CHECK_TEXT[c.status]}</Pill></span>;
      },
    })),
    { key: "passed", header: "Passed", numeric: true, sortValue: (l) => (l.evaluated ? l.passed / l.evaluated : null), render: (l) => (l.evaluated ? `${l.passed} of ${l.evaluated}` : "—") },
  ];
  return (
    <>
      <p className="pk-help">
        Each storage location&apos;s settings as the provider reports them. A setting the provider does not report is shown as
        &nbsp;<em>Not reported</em>; it never counts as a pass.
        {posture.score !== null && <> {posture.score}% of the checks with a verdict pass.</>}
      </p>
      <DataTable caption="Security checks per storage location" columns={columns} rows={posture.locations} rowKey={(l) => l.vault_id} defaultSort={{ key: "passed", dir: "asc" }} maxHeight={480} emptyText="No backup storage reported." />
    </>
  );
}

export default function BackupPage() {
  const qc = useQueryClient();
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash.slice(1), () => "");
  const tab: BackupTab = TAB_KEYS.includes(hash) ? (hash as BackupTab) : "items";
  const setTab = (t: BackupTab) => { window.location.hash = t; };
  const [range, setRange] = useState(14);
  const [rpoFilter, setRpoFilter] = useState<BackupRpoState | "all">("all");
  const [coverageFilter, setCoverageFilter] = useState<BackupCoverageState | "all">("all");
  const [siteFilter, setSiteFilter] = useState<string>("all");
  const [showConnect, setShowConnect] = useState(false);
  const [filters, setFilters] = useState<BackupFilters>({});

  const overview = useQuery({ queryKey: ["backup", "overview", filters, range], queryFn: () => getBackupOverview(filters, range), refetchInterval: 60_000, placeholderData: (prev) => prev });
  const o: BackupOverview | undefined = overview.data;
  const configured = !!o?.configured;
  const items = useQuery({ queryKey: ["backup", "items", filters], queryFn: () => listBackupItems({}, filters), enabled: configured, refetchInterval: 60_000 });
  const jobs = useQuery({ queryKey: ["backup", "jobs", filters], queryFn: () => listBackupJobs({ days: 7, limit: 500 }, filters), enabled: configured, refetchInterval: 60_000 });
  const vaults = useQuery({ queryKey: ["backup", "vaults", filters], queryFn: () => listBackupVaults(filters), enabled: configured, refetchInterval: 120_000 });
  const failures = useQuery({ queryKey: ["backup", "failures", filters], queryFn: () => getBackupFailures(7, filters), enabled: configured, refetchInterval: 120_000 });
  const security = useQuery({ queryKey: ["backup", "security", filters], queryFn: () => getBackupSecurity(filters), enabled: configured, refetchInterval: 300_000 });
  const history = useQuery({ queryKey: ["backup", "storage-history", filters], queryFn: () => getBackupStorageHistory(30, filters), enabled: configured, refetchInterval: 300_000 });
  const coverage = useQuery({ queryKey: ["backup", "coverage"], queryFn: getBackupCoverage, enabled: configured, refetchInterval: 120_000 });
  const siteHealth = useSiteHealth();
  const refresh = () => qc.invalidateQueries({ queryKey: ["backup"] });

  const siteName = useMemo(() => new Map((siteHealth.data?.sites ?? []).map((s) => [s.site_id, s.name])), [siteHealth.data]);
  const itemRows = useMemo(
    () => (items.data ?? []).filter((i) => (rpoFilter === "all" || i.rpo_state === rpoFilter) && inSite(i.site_id, siteFilter)),
    [items.data, rpoFilter, siteFilter],
  );
  const coverageRows = useMemo(
    () => (coverage.data?.resources ?? []).filter((r) => (coverageFilter === "all" || r.state === coverageFilter) && inSite(r.site_id, siteFilter)),
    [coverage.data, coverageFilter, siteFilter],
  );
  const sitesInUse = useMemo(() => {
    const ids = new Set<string | null>([...(items.data ?? []).map((i) => i.site_id), ...(coverage.data?.resources ?? []).map((r) => r.site_id)]);
    return [...ids];
  }, [items.data, coverage.data]);
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
    { key: "points", header: "Restore points", numeric: true, sortValue: (i) => i.recovery_point_count, render: (i) => (i.recovery_point_count === null ? <span className="pk-muted">—</span> : i.recovery_point_count.toLocaleString("en-US")) },
    { key: "retention", header: "Retention", numeric: true, sortValue: (i) => i.retention_days, render: (i) => (i.retention_days === null ? <span className="pk-muted">—</span> : retention(i.retention_days)) },
    { key: "policy", header: "Policy", sortValue: (i) => i.policy_name, render: (i) => i.policy_name ?? <span className="pk-muted">—</span> },
  ];
  const itemCsv: CsvColumn<BackupItem>[] = [
    { header: "Item", value: (i) => i.name },
    { header: "Workload", value: (i) => i.workload_type },
    { header: "Vault", value: (i) => i.vault_name },
    { header: "Site", value: (i) => (i.site_id ? siteName.get(i.site_id) ?? i.site_id : "") },
    { header: "RPO state", value: (i) => i.rpo_state },
    { header: "Last backup (UTC)", value: (i) => i.last_backup_at },
    { header: "Last result", value: (i) => i.last_backup_status },
    { header: "Protection", value: (i) => i.protection_state },
    { header: "Restore points", value: (i) => i.recovery_point_count },
    { header: "Oldest restore point (UTC)", value: (i) => i.oldest_recovery_point_at },
    { header: "Retention (days)", value: (i) => i.retention_days },
    { header: "Policy", value: (i) => i.policy_name },
    { header: "Resource ID", value: (i) => i.source_resource_id },
  ];

  const coverageColumns: Column<CoverageRow>[] = [
    {
      key: "name",
      header: "Resource",
      sortValue: (r) => r.name,
      render: (r) => (
        <>
          <span className="pk-cell-main">{r.name}</span>
          <span className="pk-cell-sub">{r.subscription_id ? `Subscription ${r.subscription_id}` : "Subscription not reported"}</span>
        </>
      ),
    },
    {
      key: "state",
      header: "Backup",
      sortValue: (r) => COVERAGE.findIndex((c) => c.key === r.state),
      render: (r) => {
        const c = COVERAGE.find((x) => x.key === r.state)!;
        return <span title={c.info}><Pill tone={c.tone}>{c.label}</Pill></span>;
      },
    },
    { key: "site", header: "Site", sortValue: (r) => (r.site_id ? siteName.get(r.site_id) ?? "" : null), render: (r) => (r.site_id ? siteName.get(r.site_id) ?? "—" : <span className="pk-muted">Unassigned</span>) },
  ];
  const coverageCsv: CsvColumn<CoverageRow>[] = [
    { header: "Resource", value: (r) => r.name },
    { header: "Backup", value: (r) => r.state },
    { header: "Subscription", value: (r) => r.subscription_id },
    { header: "Site", value: (r) => (r.site_id ? siteName.get(r.site_id) ?? r.site_id : "") },
    { header: "Resource ID", value: (r) => r.resource_id },
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
  const jobCsv: CsvColumn<BackupJob>[] = [
    { header: "Started (UTC)", value: (j) => j.started_at },
    { header: "Ended (UTC)", value: (j) => j.ended_at },
    { header: "Item", value: (j) => j.item_name },
    { header: "Operation", value: (j) => j.operation },
    { header: "Vault", value: (j) => j.vault_name },
    { header: "Status", value: (j) => j.status },
    { header: "Duration (s)", value: (j) => j.duration_seconds },
    { header: "Error code", value: (j) => j.error_code },
    { header: "Error", value: (j) => j.error_message },
  ];

  const vaultColumns: Column<BackupVault>[] = [
    {
      key: "name",
      header: "Storage location",
      sortValue: (v) => v.name,
      render: (v) => (
        <>
          <span className="pk-cell-main">{v.name}</span>
          <span className="pk-cell-sub">{[titleCaseWords(v.provider), v.resource_group, v.location].filter(Boolean).join(" · ")}</span>
        </>
      ),
    },
    { key: "kind", header: "Type", sortValue: (v) => v.kind, render: (v) => titleCaseWords(v.kind) },
    { key: "items", header: "Items", numeric: true, sortValue: (v) => v.items_total, render: (v) => v.items_total },
    { key: "breach", header: "Outside RPO", numeric: true, sortValue: (v) => v.items_breaching_rpo, render: (v) => (v.items_breaching_rpo ? <span style={{ color: "var(--ecc-bad)" }}>{v.items_breaching_rpo}</span> : "0") },
    { key: "soft", header: "Soft delete", sortValue: (v) => v.soft_delete_state, render: (v) => { const p = posture(v.soft_delete_state, ["enabled", "always_on"]); return <Pill tone={p.tone}>{p.text}</Pill>; } },
    { key: "immut", header: "Immutability", sortValue: (v) => v.immutability_state, render: (v) => { const p = posture(v.immutability_state, ["locked", "unlocked"]); return <Pill tone={p.tone}>{p.text}</Pill>; } },
    { key: "offsite", header: "Off-site copy", sortValue: (v) => (v.offsite === null ? null : Number(v.offsite)), render: (v) => { const f = flag(v.offsite); return <Pill tone={f.tone}>{f.text}</Pill>; } },
    { key: "redundancy", header: "Storage redundancy", sortValue: (v) => v.storage_redundancy, render: (v) => titleCaseWords(v.storage_redundancy) || <span className="pk-muted">Not reported</span> },
    {
      key: "free",
      header: "Free space",
      numeric: true,
      sortValue: (v) => (v.capacity_bytes && v.free_bytes !== null ? v.free_bytes / v.capacity_bytes : null),
      render: (v) => {
        if (!v.capacity_bytes || v.free_bytes === null) return <span className="pk-muted">—</span>;
        const pct = (v.free_bytes / v.capacity_bytes) * 100;
        const color = pct < 5 ? "var(--ecc-bad)" : pct < 15 ? "var(--ecc-warn)" : undefined;
        return <span style={color ? { color } : undefined} title={`${formatBytes(v.free_bytes)} free of ${formatBytes(v.capacity_bytes)}`}>{Math.round(pct)}%</span>;
      },
    },
    { key: "storage", header: "Storage used", numeric: true, sortValue: (v) => v.storage_used_bytes, render: (v) => (v.storage_used_bytes === null ? <span className="pk-muted">Not reported</span> : formatBytes(v.storage_used_bytes)) },
    { key: "policies", header: "Policies", numeric: true, sortValue: (v) => v.policies, render: (v) => v.policies },
    { key: "seen", header: "Last collected", sortValue: (v) => v.observed_at, render: (v) => <span className="pk-muted">{ago(v.observed_at)}</span> },
  ];

  const loadingOverview = overview.isLoading;
  const n = (v: number | undefined) => (v === undefined || !configured ? "—" : v.toLocaleString("en-US"));
  const stamp = new Date().toISOString().slice(0, 10);
  const exportButton = (onClick: () => void, disabled: boolean) => (
    <button type="button" className="pk-button" onClick={onClick} disabled={disabled}>Export CSV</button>
  );
  const siteSelect = sitesInUse.length > 1 || sitesInUse.some((id) => id !== null) ? (
    <select className="pk-input" aria-label="Filter by site" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)} style={{ width: "auto" }}>
      <option value="all">All sites</option>
      {sitesInUse.filter((id): id is string => id !== null).map((id) => (
        <option key={id} value={id}>{siteName.get(id) ?? "Unknown site"}</option>
      ))}
      {sitesInUse.includes(null) && <option value="none">Unassigned</option>}
    </select>
  ) : null;

  return (
    <PageShell
      title="Backup Overview"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center" }, { label: "Overview" }]}
      subtitle={`${o?.providers.length ? o.providers.map((p) => titleCaseWords(p)).join(", ") : "Azure"} Backup: whether every workload has a recent good backup, which servers have none, what failed, how long restore points are kept and how the backup storage is secured.`}
      actions={
        configured ? (
          <button type="button" className="pk-button" onClick={() => setShowConnect((s) => !s)}>
            {showConnect ? "Close" : "Add source"}
          </button>
        ) : undefined
      }
      meta={o?.observed_at ? `Collected ${ago(o.observed_at)}` : undefined}
    >
      {overview.error && <Banner tone="bad">Backup data could not be loaded: {overview.error instanceof Error ? overview.error.message : "request failed"}.</Banner>}
      {failingSource && <Banner tone="bad">Source “{failingSource.name}” is failing{failingSource.last_error ? `: ${failingSource.last_error}` : ""}. Figures below are from its last successful collection.</Banner>}
      {configured && !failingSource && stale && <Banner tone="warn">The last successful collection was {ago(o!.observed_at)}. Figures may be out of date.</Banner>}

      {o && !configured && (
        <Panel title="Connect a backup provider" info="Adds a backup monitoring source: one Azure subscription or one Veeam backup server." state="ready" className="pk-col-12">
          <ConnectBackup onDone={refresh} />
        </Panel>
      )}
      {configured && showConnect && (
        <Panel title="Add a backup source" info="Each Azure subscription or Veeam server is one source; storage from every source is combined below." state="ready" className="pk-col-12">
          <ConnectBackup onDone={refresh} />
        </Panel>
      )}

      {configured && (
        <div className="pk-toolbar">
          <select className="pk-input" aria-label="Time range" value={range} onChange={(e) => setRange(Number(e.target.value))} style={{ width: "auto" }}>
            {RANGES.map((r) => <option key={r.days} value={r.days}>{r.label}</option>)}
          </select>
          <button type="button" className="pk-button" onClick={refresh} disabled={overview.isFetching}>{overview.isFetching ? "Refreshing…" : "Refresh"}</button>
          {o && <span className="pk-muted">Updated {ago(o.generated_at)}</span>}
          <a className="pk-button" href={instancesHref(filters)}>Open backup instances</a>
        </div>
      )}
      {configured && o?.filter_options && <FilterBar options={o.filter_options} value={filters} onChange={setFilters} />}
      {configured && o?.filtered && <Banner tone="info">Filtered view: every figure below covers only the selected storage and workloads. Coverage of VMs is not filtered and is left out of the tiles.</Banner>}

      <StatStrip>
        <StatTile label="Protected items" href={configured ? instancesHref(filters, { protection: "active" }) : undefined} value={n(o?.items_protected)} info="Items under active backup protection." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Within RPO" href={configured ? instancesHref(filters, { rpo: "within" }) : undefined} value={n(o?.items_within_rpo)} tone="good" sub={o && configured && o.items_protected ? `${Math.round((o.items_within_rpo / o.items_protected) * 100)}% of protected` : undefined} info={`Protected items with a backup in the last ${o?.rpo_hours ?? 24} hours.`} loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="RPO breached" href={configured ? instancesHref(filters, { rpo: "breached" }) : undefined} value={n(o?.items_breaching_rpo)} alarm={(o?.items_breaching_rpo ?? 0) > 0} info={`Protected items whose last backup is older than ${o?.rpo_hours ?? 24} hours.`} loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Never backed up" href={configured ? instancesHref(filters, { rpo: "never" }) : undefined} value={n(o?.items_never_backed_up)} tone="warn" info="Protected items with no backup yet (for example, initial backup pending)." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Last backup failed" href={configured ? instancesHref(filters, { result: "failed" }) : undefined} value={n(o?.items_last_failed)} alarm={(o?.items_last_failed ?? 0) > 0} info="Items whose most recent backup failed." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Job success" badge="24h" href={configured ? "#jobs" : undefined} value={configured && o?.success_rate_24h !== null && o?.success_rate_24h !== undefined ? `${o.success_rate_24h}%` : "—"} naText={configured ? "No job finished in 24h" : "Not configured"} sub={o && configured ? `${o.jobs_24h.failed} failed of ${o.jobs_24h.total}` : undefined} info="Completed (including with warnings) over finished jobs in the last 24 hours." loading={loadingOverview} />
        <StatTile label="Unprotected VMs" href={configured ? "#coverage" : undefined} value={configured && o?.unprotected_resources !== null && o?.unprotected_resources !== undefined ? o.unprotected_resources.toLocaleString("en-US") : "—"} alarm={(o?.unprotected_resources ?? 0) > 0} info="Azure VMs in a connected subscription that no backup item protects. VMs in subscriptions without a backup source are listed as not monitored in Coverage." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
        <StatTile label="Storage used" href={configured ? "#vaults" : undefined} value={configured && o?.storage_used_bytes !== null && o?.storage_used_bytes !== undefined ? formatBytes(o.storage_used_bytes) : "—"} sub={o && configured && o.recovery_points_total !== null ? `${o.recovery_points_total.toLocaleString("en-US")} restore points` : undefined} info="Backup storage the vaults report (all redundancy tiers)." loading={loadingOverview} naText={configured ? "Not reported" : "Not configured"} />
        <StatTile label="Storage" href={configured ? "#security" : undefined} value={n(o?.vaults)} sub={o && configured ? (o.vaults_soft_delete_off ? `${o.vaults_soft_delete_off} with soft delete off` : `${o.vaults_offsite} off-site · ${o.vaults_immutable} immutable`) : undefined} alarm={(o?.vaults_soft_delete_off ?? 0) > 0} info="Backup storage locations read from the connected sources. Off-site: a copy is kept in another region or site. Immutable: restore points are locked against deletion until they expire." loading={loadingOverview} naText={configured ? undefined : "Not configured"} />
      </StatStrip>

      {configured && (
        <div className="pk-grid">
          <Panel title="Job outcomes" info={`Backup jobs per day by outcome over the selected range${o!.jobs_range ? `: ${o!.jobs_range.total} jobs, ${o!.jobs_range.failed} failed` : ""}.`} aside={RANGES.find((r) => r.days === o!.range_days)?.label.toLowerCase() ?? `last ${o!.range_days} days`} state={overview.isLoading ? "loading" : o!.daily.every((d) => d.completed + d.completed_with_warnings + d.failed + d.other === 0) ? "empty" : "ready"} emptyText="No backup job has run in this time range." className="pk-col-8">
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
            <RpoTarget overview={o!} onSaved={refresh} />
          </Panel>

          <Panel title="Workloads" info="Protected items by workload type, as each provider reports it, against the RPO." state={o!.workloads.length === 0 ? "empty" : "ready"} emptyText="No protected items reported yet." className="pk-col-12">
            <DataTable caption="Workloads" columns={workloadColumns} rows={o!.workloads} rowKey={(w) => w.workload_type ?? "_none"} rowHref={(w) => (w.workload_type ? instancesHref({ ...filters, workload_type: w.workload_type }) : null)} defaultSort={{ key: "items", dir: "desc" }} maxHeight={320} emptyText="No protected items reported yet." />
          </Panel>

          <Panel title="By subscription" info="Items by the subscription of their storage. Veeam and other on-premises storage reports none." state={o!.by_subscription.length === 0 ? "empty" : "ready"} emptyText="No protected items reported yet." className="pk-col-4">
            <DataTable caption="Backup status by subscription" columns={subscriptionColumns} rows={o!.by_subscription} rowKey={(g) => g.key ?? "_none"} rowHref={(g) => (g.key ? instancesHref({ ...filters, subscription_id: g.key }) : null)} defaultSort={{ key: "items", dir: "desc" }} maxHeight={260} emptyText="—" />
          </Panel>
          <Panel title="By region" info="Items by the region of their storage." state={o!.by_region.length === 0 ? "empty" : "ready"} emptyText="No protected items reported yet." className="pk-col-4">
            <DataTable caption="Backup status by region" columns={regionColumns} rows={o!.by_region} rowKey={(g) => g.key ?? "_none"} rowHref={(g) => (g.key ? instancesHref({ ...filters, location: g.key }) : null)} defaultSort={{ key: "items", dir: "desc" }} maxHeight={260} emptyText="—" />
          </Panel>
          <Panel title="Recovery point age" info="Protected items by the age of their newest backup." state={o!.items_protected === 0 ? "empty" : "ready"} emptyText="No protected items reported yet." className="pk-col-4">
            <BarList
              rows={o!.recovery_point_age.map((b) => ({ key: b.key, label: b.label, value: b.items, color: AGE_COLORS[b.key] }))}
              valueLabel={(v) => `${v} item${v === 1 ? "" : "s"}`}
            />
          </Panel>

          <Panel
            title={TAB_TITLES[tab]}
            info="Select a tab for protected items, coverage of your VMs, jobs, failures grouped by cause, storage or its security settings. Select an item to see its policy, restore points and job history."
            aside={
              <Tabs<BackupTab>
                value={tab}
                onChange={setTab}
                label="Backup view"
                tabs={[
                  { key: "items", label: "Items", count: items.data?.length },
                  { key: "coverage", label: "Coverage", count: coverage.data?.total },
                  { key: "jobs", label: "Jobs", count: jobs.data?.length },
                  { key: "failures", label: "Failures", count: failures.data?.failed_jobs },
                  { key: "vaults", label: "Storage", count: vaults.data?.length },
                  { key: "security", label: "Security" },
                ]}
              />
            }
            state={({ items, coverage, jobs, failures, vaults, security } as const)[tab].isLoading ? "loading" : "ready"}
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
                  {siteSelect}
                  {exportButton(() => downloadCsv(`backup-items-${stamp}.csv`, itemCsv, itemRows), itemRows.length === 0)}
                </div>
                <DataTable caption="Protected items" columns={itemColumns} rows={itemRows} rowKey={(i) => i.id} rowHref={(i) => `/infrastructure/backup/items/${i.id}`} defaultSort={{ key: "rpo", dir: "desc" }} maxHeight="max(420px, calc(100vh - 520px))" emptyText={rpoFilter === "all" && siteFilter === "all" ? "No protected items reported." : "No item matches these filters."} />
              </>
            )}
            {tab === "coverage" && (
              <>
                {coverage.data && coverage.data.total === 0 ? (
                  <p className="pk-help">
                    No Azure VMs are in inventory yet, so coverage cannot be checked. Connect the Azure subscription as an Azure monitoring source to list its VMs.
                  </p>
                ) : (
                  <>
                    <div className="pk-toolbar">
                      <Tabs<BackupCoverageState | "all">
                        value={coverageFilter}
                        onChange={setCoverageFilter}
                        label="Coverage filter"
                        tabs={[{ key: "all", label: "All" }, ...COVERAGE.map((c) => ({ key: c.key, label: c.label, count: coverage.data?.[c.key] }))]}
                      />
                      {siteSelect}
                      {exportButton(() => downloadCsv(`backup-coverage-${stamp}.csv`, coverageCsv, coverageRows), coverageRows.length === 0)}
                    </div>
                    <DataTable caption="Backup coverage" columns={coverageColumns} rows={coverageRows} rowKey={(r) => r.device_id} rowHref={(r) => `/infrastructure/hosts/${r.device_id}`} defaultSort={{ key: "state", dir: "asc" }} maxHeight="max(420px, calc(100vh - 520px))" emptyText="No resource in this state." />
                  </>
                )}
              </>
            )}
            {tab === "jobs" && (
              <>
                <div className="pk-toolbar">
                  {exportButton(() => downloadCsv(`backup-jobs-${stamp}.csv`, jobCsv, jobs.data ?? []), (jobs.data ?? []).length === 0)}
                </div>
                <DataTable caption="Backup jobs" columns={jobColumns} rows={jobs.data ?? []} rowKey={(j) => j.id} defaultSort={{ key: "started", dir: "desc" }} maxHeight="max(420px, calc(100vh - 480px))" emptyText="No backup job in the last 7 days." />
              </>
            )}
            {tab === "failures" && failures.data && (
              failures.data.failed_jobs === 0 ? (
                <p className="pk-muted pk-empty-note">No backup job failed in the last 7 days.</p>
              ) : (
                <>
                  <p className="pk-help">
                    {failures.data.failed_jobs} failed job{failures.data.failed_jobs === 1 ? "" : "s"} across {failures.data.items_affected} item{failures.data.items_affected === 1 ? "" : "s"},
                    grouped by the error code the provider reported. Hover the item count to see which items.
                  </p>
                  <DataTable caption="Failures by error code" columns={failureColumns} rows={failures.data.groups} rowKey={(g) => g.error_code ?? "_none"} defaultSort={{ key: "jobs", dir: "desc" }} maxHeight="max(420px, calc(100vh - 520px))" emptyText="No failed job." />
                </>
              )
            )}
            {tab === "vaults" && (
              <>
                {history.data && <StorageTrend history={history.data} asOf={o!.generated_at} />}
                <DataTable caption="Backup storage" columns={vaultColumns} rows={vaults.data ?? []} rowKey={(v) => v.id} rowHref={(v) => (v.device_id ? `/infrastructure/hosts/${v.device_id}` : null)} maxHeight={480} emptyText="No backup storage reported." />
              </>
            )}
            {tab === "security" && security.data && <SecurityView posture={security.data} />}
          </Panel>
        </div>
      )}
    </PageShell>
  );
}
