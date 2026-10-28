"use client";

import { memo, useState, type CSSProperties, type ReactNode } from "react";
import Chart from "@/components/viz/Chart";
import type { IncidentAnalytics } from "@/lib/api";
import { useIncidentAnalytics } from "@/hooks/useIncidentAnalytics";
import { formatDuration, sevColor, sevRank, titleCase } from "@/lib/severity";

/**
 * IncidentAnalyticsPanel — the Executive Command Center incident charts:
 * opened-vs-resolved trend, severity distribution, open-incident aging,
 * weekday x hour heatmap, resolution-time distribution (median / P90 / P95)
 * and top affected sites.
 *
 * Data: ONE read, GET /api/dashboard/incident-analytics, via
 * useIncidentAnalytics. This component fetches nothing else and contains no
 * hardcoded or mock values. Every chart shows an explicit empty state when the
 * backend returns no data; percentiles the backend reports as null render
 * "No data" (never "0 min").
 */

const RANGES = [7, 14, 30] as const;

export function dayLabel(isoDay: string): string {
  const d = new Date(`${isoDay}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return isoDay;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Heatmap legend buckets (matches the dashboard reference: 0, 1-5, 6-10, 11-20, >20). */
const HEAT_STEPS: { label: string; test: (n: number) => boolean; color: string }[] = [
  { label: "0", test: (n) => n === 0, color: "rgba(148,163,184,0.14)" },
  { label: "1-5", test: (n) => n <= 5, color: "#3b82f6" },
  { label: "6-10", test: (n) => n <= 10, color: "#eab308" },
  { label: "11-20", test: (n) => n <= 20, color: "#f97316" },
  { label: "> 20", test: () => true, color: "#ef4444" },
];

function heatColor(count: number): string {
  return (HEAT_STEPS.find((s) => s.test(count)) ?? HEAT_STEPS[HEAT_STEPS.length - 1]).color;
}

const cardStyle: CSSProperties = {
  border: "1px solid var(--line, #1f2d38)",
  borderRadius: 10,
  padding: 14,
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  fontWeight: 600,
  color: "var(--fg, #e6edf3)",
};

const mutedStyle: CSSProperties = { color: "var(--muted, #6b7785)", fontSize: 11 };

function Card({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section style={cardStyle} aria-label={title}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h3 style={titleStyle}>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      style={{ ...mutedStyle, minHeight: 90, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center" }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trend (opened vs resolved) — two series, so it cannot reuse single-series Chart
// ---------------------------------------------------------------------------

const TREND_W = 600;
const TREND_H = 150;

export function TrendChart({ points }: { points: IncidentAnalytics["trend"] }) {
  const max = Math.max(1, ...points.flatMap((p) => [p.opened, p.resolved]));
  const hasData = points.some((p) => p.opened > 0 || p.resolved > 0);
  const x = (i: number) => (points.length <= 1 ? TREND_W / 2 : (i / (points.length - 1)) * TREND_W);
  const y = (v: number) => TREND_H - (v / max) * (TREND_H - 8) - 4;
  const line = (pick: (p: IncidentAnalytics["trend"][number]) => number) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(pick(p))}`).join(" ");

  const step = Math.max(1, Math.ceil(points.length / 6));
  const labelled = points.filter((_, i) => i % step === 0 || i === points.length - 1);

  return (
    <div style={{ position: "relative" }}>
      <div style={{ ...mutedStyle, display: "flex", gap: 12, marginBottom: 4 }}>
        <span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "#3b82f6", marginRight: 4 }} />Opened</span>
        <span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "#22c55e", marginRight: 4 }} />Resolved</span>
        <span style={{ marginLeft: "auto" }}>peak {max}</span>
      </div>
      <svg
        viewBox={`0 0 ${TREND_W} ${TREND_H}`}
        preserveAspectRatio="none"
        width="100%"
        height={TREND_H}
        role="img"
        aria-label="Incidents opened and resolved per day"
        style={{ display: "block" }}
      >
        <line x1={0} x2={TREND_W} y1={TREND_H - 4} y2={TREND_H - 4} stroke="var(--line, #1f2d38)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <path d={line((p) => p.opened)} fill="none" stroke="#3b82f6" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        <path d={line((p) => p.resolved)} fill="none" stroke="#22c55e" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {points.map((p, i) => (
          <rect key={p.day} x={x(i) - TREND_W / points.length / 2} y={0} width={TREND_W / points.length} height={TREND_H} fill="transparent">
            <title>{`${dayLabel(p.day)}: ${p.opened} opened, ${p.resolved} resolved`}</title>
          </rect>
        ))}
      </svg>
      <div style={{ ...mutedStyle, display: "flex", justifyContent: "space-between", marginTop: 4 }}>
        {labelled.map((p) => (
          <span key={p.day}>{dayLabel(p.day)}</span>
        ))}
      </div>
      {!hasData && (
        <div
          role="status"
          style={{ ...mutedStyle, position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}
        >
          No incidents opened or resolved in this window
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Aging (stacked by severity)
// ---------------------------------------------------------------------------

export function AgingChart({ buckets }: { buckets: IncidentAnalytics["aging"] }) {
  const max = Math.max(1, ...buckets.map((b) => b.total));
  const severities = Array.from(new Set(buckets.flatMap((b) => Object.keys(b.by_severity)))).sort(
    (a, b) => sevRank(a) - sevRank(b) || a.localeCompare(b),
  );
  const totalOpen = buckets.reduce((sum, b) => sum + b.total, 0);
  if (totalOpen === 0) return <EmptyNote>No open incidents</EmptyNote>;

  const BAR_AREA = 130;
  return (
    <div>
      <div style={{ ...mutedStyle, display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 6 }}>
        {severities.map((s) => (
          <span key={s}>
            <i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: sevColor(s), marginRight: 4 }} />
            {titleCase(s)}
          </span>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${buckets.length}, 1fr)`, gap: 8, alignItems: "end" }}>
        {buckets.map((b) => (
          <div key={b.key} style={{ textAlign: "center" }}>
            <div style={{ ...mutedStyle, marginBottom: 2 }}>{b.total}</div>
            <div
              style={{ height: BAR_AREA, display: "flex", flexDirection: "column-reverse", alignItems: "center" }}
              title={`${b.label}: ${b.total} open`}
            >
              <div style={{ width: "70%", maxWidth: 40, height: (b.total / max) * BAR_AREA, display: "flex", flexDirection: "column-reverse", borderRadius: 3, overflow: "hidden" }}>
                {severities.map((s) => {
                  const n = b.by_severity[s] ?? 0;
                  return n > 0 ? (
                    <div key={s} style={{ height: `${(n / b.total) * 100}%`, background: sevColor(s) }} title={`${titleCase(s)}: ${n}`} />
                  ) : null;
                })}
              </div>
            </div>
            <div style={{ ...mutedStyle, marginTop: 4 }}>{b.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Heatmap (weekday x hour, UTC)
// ---------------------------------------------------------------------------

export function Heatmap({ grid }: { grid: number[][] }) {
  const total = grid.reduce((sum, row) => sum + row.reduce((a, b) => a + b, 0), 0);
  if (total === 0) return <EmptyNote>No incidents opened in this period</EmptyNote>;
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "32px repeat(24, 1fr)", gap: 2, alignItems: "center" }}>
        {grid.map((row, d) => (
          <div key={WEEKDAYS[d]} style={{ display: "contents" }}>
            <span style={mutedStyle}>{WEEKDAYS[d]}</span>
            {row.map((count, h) => (
              <div
                key={h}
                style={{ height: 14, borderRadius: 2, background: heatColor(count) }}
                title={`${WEEKDAYS[d]} ${String(h).padStart(2, "0")}:00 UTC — ${count} opened`}
              />
            ))}
          </div>
        ))}
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} style={{ ...mutedStyle, fontSize: 9, textAlign: "center" }}>
            {h % 4 === 0 ? String(h).padStart(2, "0") : ""}
          </span>
        ))}
      </div>
      <div style={{ ...mutedStyle, display: "flex", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
        {HEAT_STEPS.map((s) => (
          <span key={s.label}>
            <i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: s.color, marginRight: 4 }} />
            {s.label}
          </span>
        ))}
        <span style={{ marginLeft: "auto" }}>UTC</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top affected sites
// ---------------------------------------------------------------------------

export function TopSites({ sites, other }: { sites: IncidentAnalytics["top_sites"]; other: number }) {
  const rows = [...sites.map((s) => ({ key: s.site_id ?? s.name, name: s.name, count: s.count })), ...(other > 0 ? [{ key: "__other", name: "Other", count: other }] : [])];
  if (rows.length === 0) return <EmptyNote>No incidents opened in this window</EmptyNote>;
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((r) => (
        <div key={r.key} style={{ display: "grid", gridTemplateColumns: "84px 1fr 32px", gap: 8, alignItems: "center", fontSize: 12 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--fg, #e6edf3)" }} title={r.name}>
            {r.name}
          </span>
          <div style={{ height: 10, borderRadius: 3, background: "rgba(148,163,184,0.14)" }}>
            <div style={{ width: `${(r.count / max) * 100}%`, height: "100%", borderRadius: 3, background: "#3b82f6" }} />
          </div>
          <span style={{ ...mutedStyle, textAlign: "right" }}>{r.count}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export default memo(function IncidentAnalyticsPanel() {
  const [days, setDays] = useState<number>(7);
  const query = useIncidentAnalytics(days);
  const data = query.data;

  const rangeSelect = (
    <select
      aria-label="Analytics window"
      value={days}
      onChange={(e) => setDays(Number(e.target.value))}
      style={{ fontSize: 12, background: "transparent", color: "var(--fg, #e6edf3)", border: "1px solid var(--line, #1f2d38)", borderRadius: 6, padding: "2px 6px" }}
    >
      {RANGES.map((r) => (
        <option key={r} value={r}>
          Last {r} days
        </option>
      ))}
    </select>
  );

  return (
    <section id="incident-analytics" className="hs-zone" aria-labelledby="incident-analytics-title" style={{ margin: "16px 0" }}>
      <div className="hs-zone__header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <h2 id="incident-analytics-title" className="hs-zone__title">Incident Analytics</h2>
          <p className="hs-zone__subtitle">Trends, severity, aging and resolution times from live incident data</p>
        </div>
        {rangeSelect}
      </div>

      {query.isLoading && (
        <div role="status" aria-live="polite" style={{ ...mutedStyle, padding: 16 }}>
          Loading incident analytics…
        </div>
      )}

      {query.error && !data && (
        <div role="alert" style={{ ...cardStyle, borderColor: "#ef4444" }}>
          <span style={{ fontSize: 13 }}>
            Incident analytics could not be loaded
            {query.error instanceof Error && query.error.message ? `: ${query.error.message}` : "."}
          </span>
          <button type="button" onClick={() => query.refetch()} style={{ alignSelf: "flex-start", fontSize: 12 }}>
            Retry
          </button>
        </div>
      )}

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16 }}>
          <Card title="Incident Trends">
            <TrendChart points={data.trend} />
          </Card>

          <Card title="Incident Severity Distribution" aside={<span style={mutedStyle}>Last {data.window_days} days</span>}>
            <Chart
              type="donut"
              height={150}
              unit=""
              centerLabel={{ title: "Total Incidents", value: String(data.total_opened) }}
              series={data.severity.map((s) => ({
                name: s.severity,
                color: sevColor(s.severity),
                data: [{ label: titleCase(s.severity), value: s.count }],
              }))}
            />
          </Card>

          <Card title="Top Affected Sites" aside={<span style={mutedStyle}>By incidents</span>}>
            <TopSites sites={data.top_sites} other={data.other_sites_count} />
          </Card>

          <Card title="Incident Aging" aside={<span style={mutedStyle}>Open incidents</span>}>
            <AgingChart buckets={data.aging} />
          </Card>

          <Card title="Incident Heatmap" aside={<span style={mutedStyle}>Last {data.heatmap_days / 7} weeks</span>}>
            <Heatmap grid={data.heatmap} />
          </Card>

          <Card title="Incident Duration Distribution" aside={<span style={mutedStyle}>Resolved incidents</span>}>
            <Chart
              type="bar"
              height={150}
              yLabel="incidents"
              series={[
                {
                  name: "Resolved incidents",
                  color: "var(--cyan)",
                  data: data.duration.buckets.map((b) => ({ label: b.label, value: b.count })),
                },
              ]}
            />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
              {[
                ["Median", data.duration.median_minutes],
                ["90th Percentile", data.duration.p90_minutes],
                ["95th Percentile", data.duration.p95_minutes],
              ].map(([label, value]) => (
                <div key={label as string} style={{ border: "1px solid var(--line, #1f2d38)", borderRadius: 8, padding: "6px 8px" }}>
                  <div style={mutedStyle}>{label as string}</div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: "var(--fg, #e6edf3)" }}>{formatDuration(value as number | null)}</div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </section>
  );
});
