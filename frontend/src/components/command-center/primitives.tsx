"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import type { Delta } from "@/lib/commandCenter/format";
import { NOT_MEASURED } from "@/lib/commandCenter/format";

/**
 * Executive Command Center primitives (ECC-1): the card shell that owns the
 * loading / error / empty states, the KPI tile, a sparkline and a donut ring.
 * Presentation only — no fetching, no defaults that could pass for data.
 */

export type CardState = "loading" | "error" | "empty" | "ready";

export function InfoTip({ text }: { text: string }) {
  return (
    <button type="button" className="ecc-info" title={text} aria-label={text}>
      i
    </button>
  );
}

export function CardShell({
  title,
  info,
  aside,
  state,
  emptyText = "No data in this window",
  errorText,
  onRetry,
  skeletonHeight = 150,
  className = "",
  children,
}: {
  title: string;
  info?: string;
  aside?: ReactNode;
  state: CardState;
  emptyText?: string;
  errorText?: string;
  onRetry?: () => void;
  skeletonHeight?: number;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section className={`ecc-card ${className}`} aria-label={title} aria-busy={state === "loading"}>
      <header className="ecc-card__header">
        <h2 className="ecc-card__title">
          {title}
          {info && <InfoTip text={info} />}
        </h2>
        {aside && <div className="ecc-card__aside">{aside}</div>}
      </header>
      <div className="ecc-card__body">
        {state === "loading" && <div className="ecc-skeleton" style={{ height: skeletonHeight }} />}
        {state === "error" && (
          <div className="ecc-state ecc-state--error" role="alert">
            <span>{errorText ?? `Couldn't load ${title.toLowerCase()}`}</span>
            {onRetry && (
              <button type="button" className="ecc-button ecc-button--small" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        )}
        {state === "empty" && (
          <div className="ecc-state" role="status">
            {emptyText}
          </div>
        )}
        {state === "ready" && children}
      </div>
    </section>
  );
}

export function DeltaBadge({ delta, suffix }: { delta: Delta | null; suffix?: string }) {
  if (!delta) return null;
  const arrow = delta.direction === "up" ? "↑" : delta.direction === "down" ? "↓" : "→";
  return (
    <span className={`ecc-delta ecc-delta--${delta.tone}`}>
      {arrow} {delta.label}
      {suffix && <span className="ecc-kpi__sub"> {suffix}</span>}
    </span>
  );
}

export function Sparkline({
  values,
  color = "var(--ecc-accent)",
  label,
}: {
  values: number[];
  color?: string;
  label: string;
}) {
  if (values.length < 2 || values.every((v) => v === 0)) return null;
  const W = 72;
  const H = 24;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${H - 2 - (v / max) * (H - 4)}`);
  return (
    <svg className="ecc-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth={1.6} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

export interface KpiTileProps {
  label: string;
  /** Pre-formatted value; NOT_MEASURED renders muted with "Not measured". */
  value: string;
  info: string;
  href?: string;
  sub?: ReactNode;
  delta?: Delta | null;
  deltaSuffix?: string;
  spark?: { values: number[]; color?: string; label: string } | null;
  /** Small window badge, e.g. "24h", so a fixed-window tile is never mistaken for the range filter. */
  badge?: string;
  alarm?: boolean;
  loading?: boolean;
  unavailableReason?: string;
  /** Caption under a "—" value: "Not measured" (no sample) or "Unavailable" (read failed). */
  naText?: string;
}

export function KpiTile({
  label,
  value,
  info,
  href,
  sub,
  delta,
  deltaSuffix,
  spark,
  badge,
  alarm,
  loading,
  unavailableReason,
  naText = "Not measured",
}: KpiTileProps) {
  const na = value === NOT_MEASURED;
  const body = (
    <>
      <div className="ecc-kpi__label">
        <span>{label}</span>
        {badge && <span className="ecc-kpi__badge">{badge}</span>}
      </div>
      {loading ? (
        <div className="ecc-skeleton" style={{ height: 30, width: "60%" }} />
      ) : (
        <div className={`ecc-kpi__value${na ? " ecc-kpi__value--na" : alarm ? " ecc-kpi__value--bad" : ""}`}>{value}</div>
      )}
      <div className="ecc-kpi__foot">
        <div className="ecc-kpi__sub">
          {loading ? null : na ? (
            <span title={unavailableReason}>{naText}</span>
          ) : (
            <>
              {delta && <DeltaBadge delta={delta} suffix={deltaSuffix} />}
              {delta && sub ? <br /> : null}
              {sub}
            </>
          )}
        </div>
        {!loading && spark && <Sparkline {...spark} />}
      </div>
    </>
  );
  const aria = `${label}: ${na ? "not measured" : value}. ${info}`;
  return href ? (
    <Link href={href} className="ecc-kpi" aria-label={aria} title={info}>
      {body}
    </Link>
  ) : (
    <div className="ecc-kpi" aria-label={aria} title={info}>
      {body}
    </div>
  );
}

/** Donut ring drawn with stroke-dasharray segments; returns only the SVG. */
export function DonutRing({
  segments,
  size = 132,
  centerValue,
  centerLabel,
  label,
}: {
  segments: { key: string; value: number; color: string }[];
  size?: number;
  centerValue: string;
  centerLabel: string;
  label: string;
}) {
  const stroke = size * 0.14;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  let offset = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--ecc-track)" strokeWidth={stroke} />
      {total > 0 &&
        segments
          .filter((s) => s.value > 0)
          .map((s) => {
            const len = (s.value / total) * c;
            const el = (
              <circle
                key={s.key}
                cx={size / 2}
                cy={size / 2}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth={stroke}
                strokeDasharray={`${len} ${c - len}`}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              />
            );
            offset += len;
            return el;
          })}
      <text x="50%" y="47%" textAnchor="middle" fontSize={size * 0.17} fontWeight={700} fill="var(--ecc-text)">
        {centerValue}
      </text>
      <text x="50%" y="62%" textAnchor="middle" fontSize={size * 0.08} fill="var(--ecc-text-muted)">
        {centerLabel}
      </text>
    </svg>
  );
}

export function Legend({
  rows,
  total,
}: {
  rows: { key: string; label: string; value: number; color: string; href?: string }[];
  total: number;
}) {
  return (
    <ul className="ecc-legend">
      {rows.map((r) => {
        const pct = total > 0 ? `${Math.round((r.value / total) * 100)}%` : NOT_MEASURED;
        const content = (
          <>
            <i className="ecc-legend__swatch" style={{ background: r.color }} aria-hidden="true" />
            <span>{r.label}</span>
            <span className="ecc-legend__value">{r.value.toLocaleString("en-US")}</span>
            <span className="ecc-legend__pct">{pct}</span>
          </>
        );
        return (
          <li key={r.key}>
            {r.href ? (
              <Link className="ecc-legend__row" href={r.href} aria-label={`${r.label}: ${r.value}, ${pct}`}>
                {content}
              </Link>
            ) : (
              <div className="ecc-legend__row">{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
