"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";
import {
  getBackupFailures,
  getBackupHealth,
  getBackupOverview,
  listBackupVaults,
  type BackupFilters,
  type BackupVault,
} from "@/lib/api";
import { formatBytes } from "@/lib/csv";
import { DonutRing } from "@/components/command-center/primitives";
import { Banner, DataTable, DayColumns, PageShell, Panel, ago, type Column } from "@/components/ui/PageKit";
import { instancesHref } from "../instancesHref";
import { FilterBar, workloadLabel } from "../ui";

/**
 * /infrastructure/backup/dashboard — one-screen backup operations dashboard:
 * failure strikes, what is protected, how far back restore points reach, RPO
 * (SLA) compliance, the week's job outcomes, open backup alerts, storage
 * capacity and the most frequent errors. Every widget reads the Backup Center
 * APIs with the same filters; a widget with nothing to show says so.
 */

const C = { good: "var(--ecc-good)", warn: "var(--ecc-warn)", bad: "var(--ecc-bad)", info: "var(--ecc-accent)", none: "var(--ecc-unknown)" };

function Count({ value, color, href, title }: { value: number; color?: string; href?: string; title?: string }) {
  const body = <span style={{ color: value ? color : undefined, fontVariantNumeric: "tabular-nums" }} title={title}>{value.toLocaleString("en-US")}</span>;
  return href && value ? <Link href={href} className="ecc-link">{body}</Link> : body;
}

function Dots({ n, color }: { n: number; color: string }) {
  return (
    <span aria-hidden="true" style={{ letterSpacing: 2, color }}>
      {"●".repeat(n)}
    </span>
  );
}

/** A count in a ring, coloured by meaning (critical / warning / good / info). */
function HealthRing({ value, label, color, href }: { value: number; label: string; color: string; href?: string }) {
  const ring = (
    <div style={{ display: "grid", placeItems: "center", gap: 6 }}>
      <div style={{ width: 84, height: 84, borderRadius: "50%", border: `3px solid ${color}`, display: "grid", placeItems: "center", fontSize: 26, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        {value.toLocaleString("en-US")}
      </div>
      <span className="pk-muted" style={{ fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase" }}>{label}</span>
    </div>
  );
  return href ? <Link href={href} aria-label={`${value} ${label}`} style={{ color: "inherit", textDecoration: "none" }}>{ring}</Link> : ring;
}

/** Semicircle gauge of used / total. */
function Gauge({ used, total, label }: { used: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  const r = 52, cx = 64, cy = 64, len = Math.PI * r;
  const color = pct > 0.95 ? C.bad : pct > 0.85 ? C.warn : C.good;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <svg width="128" height="76" viewBox="0 0 128 76" role="img" aria-label={`${label}: ${Math.round(pct * 100)}% used`}>
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="var(--ecc-border)" strokeWidth="12" strokeLinecap="round" />
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round" strokeDasharray={`${pct * len} ${len}`} />
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize="18" fontWeight="600" fill="currentColor">{Math.round(pct * 100)}%</text>
      </svg>
      <div>
        <div className="pk-muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".06em" }}>Using</div>
        <div style={{ fontSize: 18, fontWeight: 600, color }}>{formatBytes(used)} <span className="pk-muted" style={{ fontWeight: 400, fontSize: 13 }}>/ {formatBytes(total)}</span></div>
        <div className="pk-muted">{label}</div>
      </div>
    </div>
  );
}

function Pills({ rows }: { rows: { label: string; value: number; color: string; href?: string }[] }) {
  return (
    <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap", marginTop: 8 }}>
      {rows.map((r) => {
        const pill = (
          <span style={{ display: "inline-block", minWidth: 64, textAlign: "center", padding: "2px 10px", borderRadius: 999, background: r.color, color: "#fff", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {r.value.toLocaleString("en-US")}
          </span>
        );
        return (
          <div key={r.label} style={{ display: "grid", justifyItems: "center", gap: 4 }}>
            <span className="pk-muted" style={{ fontSize: 12 }}>{r.label}</span>
            {r.href && r.value ? <Link href={r.href} aria-label={`${r.value} ${r.label}`}>{pill}</Link> : pill}
          </div>
        );
      })}
    </div>
  );
}

type ErrorRow = { code: string | null; jobs: number; items: number; message: string | null; vaults: string[] };
const errorColumns: Column<ErrorRow>[] = [
  { key: "code", header: "Error code", sortValue: (e) => e.code, render: (e) => <span style={{ color: C.bad }}>{e.code ?? "Not reported"}</span> },
  { key: "jobs", header: "Jobs affected", numeric: true, sortValue: (e) => e.jobs, render: (e) => e.jobs },
  { key: "items", header: "Items", numeric: true, sortValue: (e) => e.items, render: (e) => e.items },
  { key: "message", header: "Description", render: (e) => <><span>{e.message ?? "—"}</span><span className="pk-cell-sub">Storage: {e.vaults.join(", ")}</span></> },
];

export default function BackupDashboardPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<BackupFilters>({});
  const overview = useQuery({ queryKey: ["backup", "overview", filters, 7], queryFn: () => getBackupOverview(filters, 7), refetchInterval: 60_000, placeholderData: (p) => p });
  const o = overview.data;
  const configured = !!o?.configured;
  const health = useQuery({ queryKey: ["backup", "health", filters], queryFn: () => getBackupHealth(filters), enabled: configured, refetchInterval: 60_000 });
  const errors = useQuery({ queryKey: ["backup", "failures", filters, 1], queryFn: () => getBackupFailures(1, filters), enabled: configured, refetchInterval: 60_000 });
  const vaults = useQuery({ queryKey: ["backup", "vaults", filters], queryFn: () => listBackupVaults(filters), enabled: configured, refetchInterval: 120_000 });
  const h = health.data;

  const met = o ? o.items_within_rpo : 0;
  const missed = o ? o.items_breaching_rpo + o.items_never_backed_up : 0;
  const slaPct = met + missed ? Math.round((met / (met + missed)) * 100) : null;
  const jr = o?.jobs_range;
  const fixed = (vaults.data ?? []).filter((v): v is BackupVault & { capacity_bytes: number; free_bytes: number } => !!v.capacity_bytes && v.free_bytes !== null);
  const capTotal = fixed.reduce((s, v) => s + v.capacity_bytes, 0);
  const capUsed = fixed.reduce((s, v) => s + (v.capacity_bytes - v.free_bytes), 0);
  const elastic = (vaults.data ?? []).filter((v) => !v.capacity_bytes);
  const elasticUsed = elastic.reduce((s, v) => s + (v.storage_used_bytes ?? 0), 0);
  const topErrors: ErrorRow[] = (errors.data?.groups ?? []).slice(0, 5).map((g) => ({ code: g.error_code, jobs: g.failed_jobs, items: g.items_affected, message: g.sample_message, vaults: g.vaults }));
  const ready = (q: { isLoading: boolean }, empty: boolean) => (q.isLoading ? "loading" : empty ? "empty" : "ready") as "loading" | "empty" | "ready";

  return (
    <PageShell
      title="Backup Dashboard"
      icon={DatabaseBackup}
      crumbs={[{ label: "Backup Center", href: "/infrastructure/backup" }, { label: "Dashboard" }]}
      subtitle="Backup operations at a glance across every connected backup source: failure strikes, SLA, the week's jobs, open alerts, capacity and top errors."
      actions={configured ? <button type="button" className="pk-button" onClick={() => qc.invalidateQueries({ queryKey: ["backup"] })} disabled={overview.isFetching}>{overview.isFetching ? "Refreshing…" : "Refresh"}</button> : undefined}
      meta={o?.observed_at ? `Collected ${ago(o.observed_at)}` : undefined}
    >
      {overview.error && <Banner tone="bad">Backup data could not be loaded: {overview.error instanceof Error ? overview.error.message : "request failed"}.</Banner>}
      {o && !configured && (
        <Banner tone="info">No backup source is connected yet. <Link className="ecc-link" href="/infrastructure/backup">Connect Azure Backup or Veeam</Link> to fill this dashboard.</Banner>
      )}
      {configured && o?.filter_options && <FilterBar options={o.filter_options} value={filters} onChange={setFilters} />}

      {configured && o && (
        <div className="pk-grid">
          <Panel title="Strikes" info={`Items whose most recent backups failed in a row (restores excluded), over the last ${h?.strike_window_days ?? 30} days of job history.`} state={ready(health, !h?.strikes.length)} emptyText="No job history yet." className="pk-col-4">
            {h && (
              <table className="pk-table">
                <caption className="ecc-sr-only">Failure strikes</caption>
                <thead>
                  <tr>
                    <th scope="col"></th>
                    <th scope="col" className="pk-num" title="3 or more failed in a row"><Dots n={3} color={C.bad} /></th>
                    <th scope="col" className="pk-num" title="2 failed in a row"><Dots n={2} color={C.warn} /></th>
                    <th scope="col" className="pk-num" title="1 failed"><Dots n={1} color="var(--ecc-warn)" /></th>
                  </tr>
                </thead>
                <tbody>
                  {h.strikes.slice(0, 5).map((r) => (
                    <tr key={r.label}>
                      <th scope="row" style={{ fontWeight: 500 }}>{r.label === "All items" ? "All items" : workloadLabel(r.label)}</th>
                      <td className="pk-num"><Count value={r.three_plus} color={C.bad} /></td>
                      <td className="pk-num"><Count value={r.two} color={C.warn} /></td>
                      <td className="pk-num"><Count value={r.one} color={C.warn} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {h && h.strike_items.length > 0 && (
              <p className="pk-help" style={{ marginTop: 8 }}>
                Most strikes: {h.strike_items.slice(0, 3).map((s) => `${s.item_name} (${s.strikes})`).join(", ")}.{" "}
                <Link className="ecc-link" href={instancesHref(filters, { result: "failed" })}>Open failed items</Link>
              </p>
            )}
          </Panel>

          <Panel title="Usage overview" info="What the connected sources protect, by workload type, and the backup storage they report." state="ready" className="pk-col-5">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 0 }}>
              {[
                ...o.workloads.slice(0, 4).map((w) => ({ key: w.workload_type ?? "_", value: w.items_total.toLocaleString("en-US"), label: workloadLabel(w.workload_type), href: w.workload_type ? instancesHref({ ...filters, workload_type: w.workload_type }) : undefined })),
                { key: "storage", value: o.storage_used_bytes !== null ? formatBytes(o.storage_used_bytes) : "—", label: "Storage used", href: "/infrastructure/backup/vaults" },
                { key: "locations", value: String(o.vaults), label: "Storage locations", href: "/infrastructure/backup/vaults" },
              ].map((t, i) => (
                <div key={t.key} style={{ flex: "1 1 110px", padding: "8px 12px", borderLeft: i ? "1px solid var(--ecc-border)" : undefined, textAlign: "center" }}>
                  <div style={{ fontSize: 22, fontWeight: 600 }}>{t.href ? <Link href={t.href} style={{ color: "inherit", textDecoration: "none" }}>{t.value}</Link> : t.value}</div>
                  <div className="pk-muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em" }}>{t.label}</div>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Data retention" info="Items by how far back their backup policy keeps restore points (longest tier). Backup restore-point retention, not log retention." state={ready(health, !h)} className="pk-col-3">
            {h && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
                {h.retention.filter((b) => b.key !== "not_reported" || b.items > 0).map((b) => (
                  <div key={b.key} style={{ textAlign: "center" }}>
                    <div style={{ fontSize: 20, fontWeight: 600, color: b.key === "not_reported" ? undefined : C.info }}>{b.items.toLocaleString("en-US")}</div>
                    <div className="pk-muted" style={{ fontSize: 11 }}>{b.label}</div>
                  </div>
                ))}
              </div>
            )}
            <p className="pk-help" style={{ marginTop: 8 }}><Link className="ecc-link" href="/infrastructure/backup/policies">Policies and retention simulation</Link></p>
          </Panel>

          <Panel title="SLA" info={`Protected items with a backup inside the ${o.rpo_hours} h RPO (met) versus outside it or never backed up (missed).`} state={met + missed ? "ready" : "empty"} emptyText="No protected items yet." className="pk-col-4">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 24 }}>
              <DonutRing size={132} label="RPO met versus missed" centerValue={slaPct !== null ? `${slaPct}%` : "—"} centerLabel="SLA" segments={[{ key: "met", value: met, color: C.good }, { key: "missed", value: missed, color: C.bad }]} />
            </div>
            <Pills rows={[
              { label: "Met SLA", value: met, color: C.good, href: instancesHref(filters, { rpo: "within" }) },
              { label: "Missed SLA", value: missed, color: C.bad, href: instancesHref(filters, { rpo: "breached" }) },
            ]} />
          </Panel>

          <Panel title="Last 7 days job summary" info="Backup and restore jobs per day by outcome." aside={<Link className="ecc-link" href="/infrastructure/backup/jobs">Open jobs</Link>} state={o.daily.some((d) => d.completed + d.completed_with_warnings + d.failed + d.other) ? "ready" : "empty"} emptyText="No job in the last 7 days." className="pk-col-5">
            <DayColumns
              label="Jobs per day, last 7 days"
              buckets={o.daily.map((d) => ({ key: d.day, label: new Date(`${d.day}T00:00:00`).toLocaleDateString("en-US", { weekday: "short" }), values: { completed: d.completed, completed_with_warnings: d.completed_with_warnings, failed: d.failed, other: d.other } }))}
              series={[
                { key: "completed", label: "Completed", color: C.good },
                { key: "completed_with_warnings", label: "With warnings", color: C.warn },
                { key: "failed", label: "Failed", color: C.bad },
                { key: "other", label: "Other", color: C.none },
              ]}
              height={140}
            />
            {jr && (
              <Pills rows={[
                { label: "Completed", value: jr.completed, color: C.good, href: "/infrastructure/backup/jobs?status=completed" },
                { label: "With warnings", value: jr.completed_with_warnings, color: C.warn, href: "/infrastructure/backup/jobs?status=completed_with_warnings" },
                { label: "Failed", value: jr.failed, color: C.bad, href: "/infrastructure/backup/jobs?status=failed" },
                { label: "Other", value: jr.other + jr.in_progress, color: C.none },
              ]} />
            )}
          </Panel>

          <Panel title="Health" info="Open backup alerts by severity (raised from RPO, last result, protection, soft delete, free space and replication readings), and healthy protected items." aside={<Link className="ecc-link" href="/alerts">Alerts</Link>} state={ready(health, !h)} className="pk-col-3">
            {h && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 14, justifyItems: "center" }}>
                <HealthRing value={h.alerts_critical} label="Critical" color={C.bad} href="/alerts" />
                <HealthRing value={h.alerts_warning} label="Warning" color={C.warn} href="/alerts" />
                <HealthRing value={h.items_healthy} label="Good" color={C.good} href={instancesHref(filters, { rpo: "within" })} />
                <HealthRing value={h.alerts_info} label="Info" color={C.info} href="/alerts" />
              </div>
            )}
          </Panel>

          <Panel title="Current capacity usage" info="Fixed-size storage (Veeam repositories): used of total capacity. Elastic cloud storage (Azure vaults) has no fixed capacity; its usage is shown on its own." state={vaults.isLoading ? "loading" : (vaults.data ?? []).length ? "ready" : "empty"} emptyText="No backup storage reported." className="pk-col-4">
            {fixed.length > 0 ? (
              <Gauge used={capUsed} total={capTotal} label={`${fixed.length} fixed-size repositor${fixed.length === 1 ? "y" : "ies"}`} />
            ) : (
              <p className="pk-muted">No fixed-size storage reported.</p>
            )}
            {elastic.length > 0 && (
              <p style={{ marginTop: 12 }}>
                <strong>{formatBytes(elasticUsed)}</strong> <span className="pk-muted">in {elastic.length} elastic storage location{elastic.length === 1 ? "" : "s"} (no fixed capacity)</span>
              </p>
            )}
          </Panel>

          <Panel title="Top 5 errors in the last 24 hours" info="Failed jobs of the last 24 hours grouped by the provider's error code." aside={<Link className="ecc-link" href="/infrastructure/backup#failures">Failure analysis</Link>} state={ready(errors, topErrors.length === 0)} emptyText="No backup job failed in the last 24 hours." className="pk-col-8">
            <DataTable caption="Top errors, last 24 hours" columns={errorColumns} rows={topErrors} rowKey={(e) => e.code ?? "_none"} rowHref={(e) => `/infrastructure/backup/jobs?status=failed&days=1${e.code ? `&q=${encodeURIComponent(e.code)}` : ""}`} maxHeight={320} emptyText="—" />
          </Panel>
        </div>
      )}
    </PageShell>
  );
}
