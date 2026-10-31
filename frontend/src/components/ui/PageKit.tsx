"use client";

import Link from "next/link";
import { useMemo, useState, type ComponentType, type ReactNode } from "react";
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
  icon: Icon,
  children,
}: {
  title: string;
  /** Icon shown in a gradient badge beside the title. */
  icon?: ComponentType<{ size?: number }>;
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
          <h1 className="pk-title">
            {Icon && (
              <span className="pk-title__icon" aria-hidden="true">
                <Icon size={18} />
              </span>
            )}
            {title}
          </h1>
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

/**
 * BarList — ranked horizontal bars (top-N breakdowns). Bars are scaled to the
 * largest value shown; rows link out when `href` is given.
 */
export function BarList({
  rows,
  emptyText = "Nothing to rank",
  valueLabel = (v: number) => v.toLocaleString("en-US"),
}: {
  rows: { key: string; label: ReactNode; value: number; color?: string; href?: string; title?: string }[];
  emptyText?: string;
  valueLabel?: (v: number) => string;
}) {
  if (rows.length === 0) return <p className="pk-muted pk-empty-note">{emptyText}</p>;
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="pk-barlist">
      {rows.map((r) => {
        const body = (
          <>
            <span className="pk-barlist__label pk-truncate" title={r.title}>{r.label}</span>
            <span className="pk-barlist__track" aria-hidden="true">
              <span className="pk-barlist__fill" style={{ width: `${Math.max(2, (r.value / max) * 100)}%`, background: r.color ?? "var(--ecc-accent)" }} />
            </span>
            <span className="pk-barlist__value">{valueLabel(r.value)}</span>
          </>
        );
        return (
          <li key={r.key}>
            {r.href ? <Link className="pk-barlist__row" href={r.href}>{body}</Link> : <div className="pk-barlist__row">{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * DayColumns — stacked columns per day (or any ordered bucket). Each column
 * stacks its series in order; the tooltip lists every series value.
 */
export function DayColumns({
  buckets,
  series,
  height = 140,
  label,
}: {
  buckets: { key: string; label: string; values: Record<string, number> }[];
  series: { key: string; label: string; color: string }[];
  height?: number;
  label: string;
}) {
  const totals = buckets.map((b) => series.reduce((sum, s) => sum + (b.values[s.key] ?? 0), 0));
  const max = Math.max(...totals, 1);
  const every = Math.max(1, Math.ceil(buckets.length / 8));
  return (
    <figure className="pk-daycols" aria-label={label}>
      <div className="pk-daycols__plot" style={{ height }}>
        {buckets.map((b, i) => (
          <div
            key={b.key}
            className="pk-daycols__col"
            title={`${b.label}\n${series.map((s) => `${s.label}: ${(b.values[s.key] ?? 0).toLocaleString("en-US")}`).join("\n")}`}
          >
            {series.map((s) => {
              const v = b.values[s.key] ?? 0;
              return v > 0 ? <span key={s.key} style={{ height: `${(v / max) * 100}%`, background: s.color }} /> : null;
            })}
            {totals[i] === 0 && <span className="pk-daycols__zero" />}
          </div>
        ))}
      </div>
      <div className="pk-daycols__axis">
        {buckets.map((b, i) => (
          <span key={b.key}>{i % every === 0 ? b.label : ""}</span>
        ))}
      </div>
      <figcaption className="pk-daycols__legend">
        {series.map((s) => (
          <span key={s.key}><i style={{ background: s.color }} aria-hidden="true" />{s.label}</span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Group ISO timestamps into the last `days` calendar days (local time). */
export function dayBuckets<T>(
  items: T[],
  at: (item: T) => string | null | undefined,
  classify: (item: T) => string,
  days: number,
  now: Date = new Date(),
): { key: string; label: string; values: Record<string, number> }[] {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1));
  const out = Array.from({ length: days }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    return { key: d.toISOString().slice(0, 10), label: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }), values: {} as Record<string, number> };
  });
  for (const item of items) {
    const iso = at(item);
    if (!iso) continue;
    const t = new Date(iso);
    const idx = Math.floor((new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime() - start.getTime()) / 86_400_000);
    if (idx < 0 || idx >= days) continue;
    const k = classify(item);
    out[idx].values[k] = (out[idx].values[k] ?? 0) + 1;
  }
  return out;
}
