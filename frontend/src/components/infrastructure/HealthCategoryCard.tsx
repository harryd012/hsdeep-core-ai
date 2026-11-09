"use client";

import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { AlertOctagon, AlertTriangle } from "lucide-react";
import "@/app/styles/health-overview.css";

/** Health counts as the canonical overview buckets them (unknown is never healthy). */
export interface HealthCounts {
  total: number;
  healthy: number;
  warning: number;
  critical: number;
  unknown: number;
}

/**
 * One category of the infrastructure health overview: its name, how many are
 * critical / warning out of the total, and a bar split by health. The bar is
 * proportional to real counts only; an empty category draws an empty track.
 */
export default function HealthCategoryCard({
  title,
  icon: Icon,
  counts,
  href,
  note,
}: {
  title: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  counts: HealthCounts | null;
  href?: string;
  /** Shown instead of the bar when there is nothing to count (e.g. "Not connected"). */
  note?: ReactNode;
}) {
  const c = counts;
  const segments = c && c.total > 0
    ? (["critical", "warning", "healthy", "unknown"] as const).filter((k) => c[k] > 0).map((k) => ({ k, pct: (c[k] / c.total) * 100 }))
    : [];
  const label = c
    ? `${title}: ${c.critical} critical, ${c.warning} warning, ${c.healthy} healthy, ${c.unknown} unknown, of ${c.total}`
    : `${title}: ${typeof note === "string" ? note : "no data"}`;
  const body = (
    <>
      <div className="ho-card__head">
        <span className="ho-card__icon"><Icon size={16} aria-hidden /></span>
        <span className="ho-card__title" title={title}>{title}</span>
        {c && (
          <span className="ho-card__counts" aria-hidden="true">
            {c.critical > 0 && <span className="ho-count ho-count--critical"><AlertOctagon size={12} aria-hidden /> {c.critical}</span>}
            {c.warning > 0 && <span className="ho-count ho-count--warning"><AlertTriangle size={12} aria-hidden /> {c.warning}</span>}
            <span className="ho-count ho-count--total">{c.critical > 0 || c.warning > 0 ? "/ " : ""}{c.total.toLocaleString("en-US")}</span>
          </span>
        )}
      </div>
      {note && !c ? (
        <p className="ho-card__note">{note}</p>
      ) : (
        <span className="ho-bar" aria-hidden="true">
          {segments.map((s) => <span key={s.k} className={`ho-bar__seg ho-bar__seg--${s.k}`} style={{ width: `${s.pct}%` }} />)}
        </span>
      )}
    </>
  );
  return href ? (
    <Link href={href} className="ho-card" aria-label={label}>{body}</Link>
  ) : (
    <div className={`ho-card${c ? "" : " ho-card--muted"}`} aria-label={label} role="group">{body}</div>
  );
}
