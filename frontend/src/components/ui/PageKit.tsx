"use client";

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import "@/app/styles/executive-command-center.css";
import "@/app/styles/page-kit.css";

/**
 * Page kit — the shared building blocks for every operational page, so each
 * screen has the same header, stat strip, panels, tables, pills and states as
 * the Executive Command Center. Presentation only: no fetching, and no value
 * is ever defaulted (callers pass "—" for anything not measured).
 *
 * Panels and KPI tiles are the Command Center primitives, re-exported here so
 * pages import from one place.
 */

export { CardShell as Panel, KpiTile as StatTile, InfoTip } from "@/components/command-center/primitives";
export type { CardState as PanelState } from "@/components/command-center/primitives";

export function PageShell({
  title,
  subtitle,
  crumbs,
  actions,
  meta,
  children,
}: {
  title: string;
  subtitle?: string;
  crumbs?: { label: string; href?: string }[];
  actions?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="pk-page">
      <header className="pk-header">
        <div>
          {crumbs && crumbs.length > 0 && (
            <nav className="pk-crumbs" aria-label="Breadcrumb">
              {crumbs.map((c, i) => (
                <span key={c.label}>
                  {i > 0 && " / "}
                  {c.href ? <Link href={c.href}>{c.label}</Link> : c.label}
                </span>
              ))}
            </nav>
          )}
          <h1 className="pk-title">{title}</h1>
          {subtitle && <p className="pk-subtitle">{subtitle}</p>}
        </div>
        {(actions || meta) && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
            {actions && <div className="pk-actions">{actions}</div>}
            {meta && <div className="pk-meta">{meta}</div>}
          </div>
        )}
      </header>
      {children}
    </div>
  );
}

export function StatStrip({ children }: { children: ReactNode }) {
  return (
    <div className="pk-stats" role="region" aria-label="Summary">
      {children}
    </div>
  );
}

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export function Pill({ tone, children, dot = true }: { tone: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`pk-pill pk-pill--${tone}`}>
      {dot && <span className="pk-pill__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

/** Status words used across collectors, devices, sensors and incidents -> tone. */
export function statusTone(status: string | null | undefined): Tone {
  const s = (status ?? "").toLowerCase().trim();
  if (["up", "online", "healthy", "ok", "connected", "success", "resolved", "closed", "recovered", "available", "active", "enabled"].includes(s)) return "good";
  if (["warning", "degraded", "partial", "stale", "acknowledged", "investigating", "monitoring", "busy", "pending", "past_due"].includes(s)) return "warn";
  if (["down", "offline", "critical", "error", "failed", "unreachable", "open", "expired"].includes(s)) return "bad";
  if (["info", "running", "queued", "discovered"].includes(s)) return "info";
  return "neutral";
}

export function titleCaseWords(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function SearchBox({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <div className="pk-search">
      <Search size={14} aria-hidden="true" />
      <input type="search" value={value} placeholder={placeholder} aria-label={label} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { key: NoInfer<K>; label: string; count?: number }[];
  value: K;
  onChange: (key: NoInfer<K>) => void;
  label: string;
}) {
  return (
    <div className="pk-tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" className="pk-tab" aria-selected={t.key === value} onClick={() => onChange(t.key)}>
          {t.label}
          {t.count !== undefined && <span style={{ marginLeft: 6, opacity: 0.7 }}>{t.count.toLocaleString("en-US")}</span>}
        </button>
      ))}
    </div>
  );
}

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => string | number | null;
  numeric?: boolean;
  width?: number | string;
}

/**
 * DataTable — sticky header, optional client-side sorting, row links, and
 * an explicit empty row. Rows are rendered as given; paging is the caller's.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  rowHref,
  emptyText = "No rows",
  maxHeight = 560,
  caption,
  defaultSort,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowHref?: (row: T) => string | null;
  emptyText?: string;
  maxHeight?: number | string;
  caption: string;
  defaultSort?: { key: string; dir: "asc" | "desc" };
}) {
  const router = useRouter();
  const [sort, setSort] = useState(defaultSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const val = col.sortValue;
    return [...rows].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true });
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [rows, sort, columns]);

  return (
    <div className="pk-table-wrap" style={{ maxHeight }}>
      <table className="pk-table">
        <caption className="ecc-sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined;
              return (
                <th key={c.key} scope="col" className={c.numeric ? "pk-num" : undefined} style={{ width: c.width }} aria-sort={ariaSort}>
                  {c.sortValue ? (
                    <button
                      type="button"
                      onClick={() =>
                        setSort(active && sort!.dir === "asc" ? { key: c.key, dir: "desc" } : { key: c.key, dir: "asc" })
                      }
                    >
                      {c.header}
                      <span aria-hidden="true" style={{ opacity: active ? 1 : 0.35 }}>
                        {active && sort!.dir === "desc" ? "↓" : "↑"}
                      </span>
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} style={{ textAlign: "center", padding: 28, color: "var(--ecc-text-muted)" }}>
                {emptyText}
              </td>
            </tr>
          ) : (
            sorted.map((row) => {
              const href = rowHref?.(row) ?? null;
              return (
                <tr
                  key={rowKey(row)}
                  className={href ? "pk-row--link" : undefined}
                  onClick={href ? () => router.push(href) : undefined}
                >
                  {columns.map((c) => (
                    <td key={c.key} className={c.numeric ? "pk-num" : undefined}>
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export function Banner({ tone, children }: { tone: "warn" | "bad" | "good" | "info"; children: ReactNode }) {
  return (
    <div className={`pk-banner pk-banner--${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <span>{children}</span>
    </div>
  );
}

/** "12 min ago" / "3 h ago" / "4 d ago"; "—" for missing or invalid timestamps. */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const m = Math.max(0, Math.round((now - t) / 60_000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}
