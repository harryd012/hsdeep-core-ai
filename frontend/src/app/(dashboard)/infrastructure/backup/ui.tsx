"use client";

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import type { BackupCheckStatus, BackupFilterOptions, BackupFilters, BackupRpoState, BackupStorageHistory } from "@/lib/api";
import { formatBytes } from "@/lib/csv";
import { DayColumns, titleCaseWords, type Tone } from "@/components/ui/PageKit";

/** Shared formatting and building blocks for the Backup Center pages. */

export const RPO_LABEL: Record<BackupRpoState, { text: string; tone: Tone }> = {
  within: { text: "Within RPO", tone: "good" },
  breached: { text: "RPO breached", tone: "bad" },
  never: { text: "Never backed up", tone: "warn" },
  not_protected: { text: "Not protected", tone: "neutral" },
};

export function jobTone(status: string | null): Tone {
  if (status === "completed") return "good";
  if (status === "completed_with_warnings") return "warn";
  if (status === "failed") return "bad";
  if (status === "in_progress") return "info";
  return "neutral";
}

export function hours(h: number | null): string {
  if (h === null) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${Math.round(h / 24)} d`;
}

export function retention(days: number | null): string {
  if (days === null) return "—";
  return days % 365 === 0 ? `${days / 365} y` : days % 7 === 0 && days >= 14 ? `${days / 7} wk` : `${days} d`;
}

export function duration(s: number | null): string {
  if (s === null) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** Posture label + tone: what the provider reported, never a guess. */
export function posture(value: string | null, good: string[], label = titleCaseWords): { text: string; tone: Tone } {
  if (!value) return { text: "Not reported", tone: "neutral" };
  return { text: label(value), tone: good.includes(value) ? "good" : "warn" };
}

/** Yes / No / Not reported for a provider-reported flag (never inferred). */
export function flag(value: boolean | null): { text: string; tone: Tone } {
  if (value === null) return { text: "Not reported", tone: "neutral" };
  return value ? { text: "Yes", tone: "good" } : { text: "No", tone: "warn" };
}

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

export function workloadLabel(w: string | null): string {
  return w === null ? "Not reported" : WORKLOAD_LABELS[w] ?? titleCaseWords(w);
}

export const CHECK_TONE: Record<BackupCheckStatus, Tone> = {
  pass: "good",
  warn: "warn",
  fail: "bad",
  not_reported: "neutral",
  not_applicable: "neutral",
};
export const CHECK_TEXT: Record<BackupCheckStatus, string> = {
  pass: "Pass",
  warn: "Review",
  fail: "Fail",
  not_reported: "Not reported",
  not_applicable: "Not applicable",
};

/** Label / value list; a null value reads "Not reported". */
export function Props({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="hd-props">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v ?? <span className="pk-muted">Not reported</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Details drawer on the right; Escape or the close button closes it. */
export function SidePanel({ title, subtitle, onClose, children }: { title: string; subtitle?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <aside
      role="dialog"
      aria-label={title}
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: "min(460px, 100vw)", zIndex: 60, overflowY: "auto",
        background: "var(--ecc-surface, var(--background))", borderLeft: "1px solid var(--ecc-border)",
        boxShadow: "-8px 0 24px rgba(0,0,0,.18)", padding: 20,
      }}
    >
      <div style={{ display: "flex", alignItems: "start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0, fontSize: 18, overflowWrap: "anywhere" }}>{title}</h2>
          {subtitle && <p className="pk-muted" style={{ margin: "4px 0 0" }}>{subtitle}</p>}
        </div>
        <button type="button" className="pk-button" onClick={onClose} aria-label="Close details"><X size={14} aria-hidden="true" /></button>
      </div>
      {children}
    </aside>
  );
}

/** Small section heading inside a side panel. */
export function PanelHeading({ children }: { children: ReactNode }) {
  return <h3 className="pk-help" style={{ marginTop: 16, marginBottom: 6, fontWeight: 600 }}>{children}</h3>;
}

/** `asOf`: the overview's generated_at, so the last column is today on the server. */
export function StorageTrend({ history, asOf }: { history: BackupStorageHistory; asOf: string }) {
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

type FilterKey = keyof BackupFilters;

/** Command Center filters: each select appears only when it has a choice to make. */
export function FilterBar({ options, value, onChange }: { options: BackupFilterOptions; value: BackupFilters; onChange: (f: BackupFilters) => void }) {
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
