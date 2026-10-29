"use client";

import Link from "next/link";
import { AlertTriangle, Clock, Database, FileWarning, LayoutDashboard, Server, ShieldCheck, Siren, Timer } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCommandCenterBrief } from "@/hooks/useCommandCenterBrief";
import { useIncidentAnalytics } from "@/hooks/useIncidentAnalytics";
import { useSiteHealth } from "@/hooks/useSiteHealth";
import { useRecentIncidents } from "@/hooks/useRecentIncidents";
import { useCommandCenterFilters } from "@/hooks/useCommandCenterFilters";
import Chart from "@/components/viz/Chart";
import { AgingChart, Heatmap, TopSites, TrendChart } from "@/components/dashboard/IncidentAnalyticsPanel";
import type { CommandCenterBrief, Incident, IncidentSeverity, SiteHealthItem } from "@/lib/api";
import {
  INCIDENT_SEVERITIES,
  formatDuration,
  sevColor,
  titleCase,
} from "@/lib/severity";
import {
  NOT_MEASURED,
  RANGES,
  computeDelta,
  formatAge,
  formatClock,
  formatCount,
  formatKpiCount,
  formatMinutes,
  formatPct,
  isStale,
  pct,
  type CommandCenterRange,
} from "@/lib/commandCenter/format";
import { CardShell, DonutRing, KpiTile, Legend, type CardState } from "./primitives";
import SiteHoneycomb from "./SiteHoneycomb";
import AppearanceMenu from "@/components/AppearanceMenu";
import SituationBriefing from "./SituationBriefing";
import { getAlertSummary } from "@/lib/api";
import type { BriefingInput } from "@/lib/commandCenter/briefing";

/**
 * ExecutiveCommandCenter — the one-screen /dashboard (ECC-1).
 *
 * Row 1  KPI strip (estate, incidents, collectors, MTTA, MTTR)
 * Row 2  Incident trends · severity distribution · infrastructure health
 * Row 3  Top affected sites · sites honeycomb · collector health · aging
 * Row 4  Recent incidents · heatmap · resolution-time distribution
 *
 * Four reads, all pre-existing or additive: command-center-brief (fixed 24h
 * estate snapshot), incident-analytics (range + site + severity filters),
 * site-health, and the incident list. Nothing here invents a value: null is
 * "—" / "Not measured", a delta needs both windows, and each card carries its
 * own loading / error / empty state so one failing read never blanks the page.
 */

const POLL_MS = 60_000;

const RANGE_LABEL: Record<CommandCenterRange, string> = {
  "7d": "Last 7 days",
  "14d": "Last 14 days",
  "30d": "Last 30 days",
};

const STATUS_TONE: Record<string, string> = {
  open: "var(--ecc-bad)",
  acknowledged: "var(--ecc-warn)",
  investigating: "var(--ecc-warn)",
  monitoring: "var(--ecc-accent)",
  recovered: "var(--ecc-good)",
  resolved: "var(--ecc-good)",
  closed: "var(--ecc-unknown)",
  archived: "var(--ecc-unknown)",
};

function stateOf(q: { isLoading: boolean; error: unknown; data: unknown }, empty = false): CardState {
  if (q.data !== undefined && q.data !== null) return empty ? "empty" : "ready";
  if (q.isLoading) return "loading";
  if (q.error) return "error";
  return "loading";
}

function trendValues(series: CommandCenterBrief["trends"]["incidents"] | undefined): number[] {
  return series?.available ? series.buckets.map((b) => b.count) : [];
}

// ---------------------------------------------------------------------------
// Header + filters
// ---------------------------------------------------------------------------

function Header({
  range,
  siteId,
  severity,
  sites,
  updatedAt,
  anyError,
  refreshing,
  onChange,
  onRefresh,
}: {
  range: CommandCenterRange;
  siteId: string | null;
  severity: IncidentSeverity | null;
  sites: SiteHealthItem[];
  updatedAt: number | null;
  anyError: boolean;
  refreshing: boolean;
  onChange: (patch: { range?: CommandCenterRange; siteId?: string | null; severity?: IncidentSeverity | null }) => void;
  onRefresh: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(id);
  }, []);
  const stale = isStale(updatedAt, now, POLL_MS);
  const chip = anyError
    ? { cls: "ecc-chip--down", text: "Partial data" }
    : stale
      ? { cls: "ecc-chip--stale", text: "Stale" }
      : { cls: "ecc-chip--live", text: "Live" };
  const sortedSites = useMemo(() => [...sites].sort((a, b) => a.code.localeCompare(b.code)), [sites]);

  return (
    <header className="ecc-header">
      <div>
        <h1 className="ecc-header__title"><span className="ecc-header__icon" aria-hidden="true"><LayoutDashboard size={18} /></span>Executive Command Center</h1>
        <p className="ecc-header__subtitle">Infrastructure health, incident intelligence and response performance</p>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
        <div className="ecc-header__controls" role="group" aria-label="Dashboard filters">
          <select
            className="ecc-select"
            aria-label="Time range"
            value={range}
            onChange={(e) => onChange({ range: e.target.value as CommandCenterRange })}
          >
            {RANGES.map((r) => (
              <option key={r} value={r}>
                {RANGE_LABEL[r]}
              </option>
            ))}
          </select>
          <select
            className="ecc-select"
            aria-label="Site"
            value={siteId ?? ""}
            onChange={(e) => onChange({ siteId: e.target.value || null })}
          >
            <option value="">All sites</option>
            {sortedSites.map((s) => (
              <option key={s.site_id} value={s.site_id}>
                {s.code} — {s.name}
              </option>
            ))}
          </select>
          <select
            className="ecc-select"
            aria-label="Severity"
            value={severity ?? ""}
            onChange={(e) => onChange({ severity: (e.target.value || null) as IncidentSeverity | null })}
          >
            <option value="">All severities</option>
            {INCIDENT_SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
          <AppearanceMenu variant="header" />
          <button type="button" className="ecc-button" onClick={onRefresh} disabled={refreshing} aria-label="Refresh all data">
            <span aria-hidden="true">⟳</span> {refreshing ? "Refreshing" : "Refresh"}
          </button>
        </div>
        <div className="ecc-header__meta" aria-live="polite">
          <span>Updated {formatClock(updatedAt)}</span>
          <span className={`ecc-chip ${chip.cls}`}>
            <span className="ecc-chip__dot" aria-hidden="true" />
            {chip.text}
          </span>
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// KPI strip
// ---------------------------------------------------------------------------

function KpiStrip({ brief, loading, error }: { brief: CommandCenterBrief | undefined; loading: boolean; error: boolean }) {
  const devices = brief?.infrastructure_health.devices;
  const collectors = brief?.infrastructure_health.collectors;
  const kpis = brief?.kpis;
  const prev = kpis?.previous ?? null;
  const missing = !brief && !loading;
  const reason = error ? "The command-center brief could not be loaded" : undefined;
  const v = (fn: () => string) => (brief ? fn() : NOT_MEASURED);
  const naText = brief ? "Not measured" : "Unavailable";

  const atRisk = devices ? devices.critical + devices.down : null;
  const collectorPct = collectors ? pct(collectors.healthy, collectors.total) : null;

  return (
    <div className="ecc-kpis" role="region" aria-label="Key performance indicators">
      <KpiTile
        label="Monitored Devices" icon={Server} tone="info"
        value={v(() => formatKpiCount(devices!.total))}
        info="All registered devices in the estate (current state)."
        href="/explorer"
        sub={devices ? `${formatCount(devices.unknown)} with no status` : undefined}
        loading={loading}
        naText={naText}
        unavailableReason={missing ? reason : undefined}
      />
      <KpiTile
        label="Healthy Devices" icon={ShieldCheck} tone="good"
        value={v(() => formatKpiCount(devices!.healthy))}
        info="Devices whose latest status is up."
        href="/explorer"
        sub={devices ? `${formatPct(pct(devices.healthy, devices.total))} of estate` : undefined}
        loading={loading}
        naText={naText}
        unavailableReason={reason}
      />
      <KpiTile
        label="Critical / Down" icon={AlertTriangle}
        value={v(() => formatKpiCount(atRisk))}
        info="Devices whose latest status is critical, down or offline. Sparkline: devices reported down per hour, last 24h."
        href="/explorer"
        alarm={!!atRisk}
        sub={devices ? `${formatCount(devices.critical)} critical · ${formatCount(devices.down)} down` : undefined}
        spark={{ values: trendValues(brief?.trends.devices_down), color: "var(--ecc-bad)", label: "Devices down per hour" }}
        loading={loading}
        naText={naText}
        unavailableReason={reason}
      />
      <KpiTile
        label="Active Incidents" icon={FileWarning} tone="warn"
        value={v(() => formatKpiCount(kpis!.open_incidents))}
        info={`${kpis ? formatCount(kpis.open_incidents) + " incidents" : "Incidents"} open, acknowledged or investigating now. Delta compares incidents opened in the last 24h with the 24h before.`}
        href="/incidents"
        delta={kpis && prev ? computeDelta(kpis.incident_volume_24h, prev.incident_volume, "lower-is-better") : null}
        sub={kpis ? `${formatCount(kpis.incident_volume_24h)} opened in 24h` : undefined}
        spark={{ values: trendValues(brief?.trends.incidents), label: "Incidents opened per hour" }}
        loading={loading}
        naText={naText}
        unavailableReason={reason}
      />
      <KpiTile
        label="Critical Incidents" icon={Siren}
        value={v(() => formatKpiCount(kpis!.critical_open_incidents))}
        info={`${kpis ? formatCount(kpis.critical_open_incidents) : "Active"} active incidents with critical severity.`}
        href="/incidents?severity=critical"
        alarm={!!kpis?.critical_open_incidents}
        sub={kpis ? `${formatPct(pct(kpis.critical_open_incidents, kpis.open_incidents))} of active` : undefined}
        loading={loading}
        naText={naText}
        unavailableReason={reason}
      />
      <KpiTile
        label="Collector Health" icon={Database} tone="info"
        value={collectors ? formatPct(collectorPct) : NOT_MEASURED}
        info="Share of configured collectors whose last 24h of runs were healthy. Sparkline: failed collection runs per hour."
        href="/monitoring"
        sub={collectors && collectors.total ? `${collectors.healthy}/${collectors.total} healthy` : undefined}
        spark={{ values: trendValues(brief?.trends.collection_failures), color: "var(--ecc-warn)", label: "Failed collection runs per hour" }}
        loading={loading}
        naText={naText}
        unavailableReason={collectors && !collectors.total ? "No collectors configured" : reason}
      />
      <KpiTile
        label="MTTA" icon={Timer} tone="info"
        value={v(() => formatMinutes(kpis!.mtta_minutes))}
        info="Mean time to acknowledge incidents opened in the last 24h. Delta vs the previous 24h."
        href="/incidents"
        badge="24h"
        delta={kpis && prev ? computeDelta(kpis.mtta_minutes, prev.mtta_minutes, "lower-is-better") : null}
        deltaSuffix="vs prev 24h"
        loading={loading}
        naText={naText}
        unavailableReason={brief ? "No incident was acknowledged in the last 24h" : reason}
      />
      <KpiTile
        label="MTTR" icon={Clock} tone="info"
        value={v(() => formatMinutes(kpis!.mttr_minutes))}
        info="Mean time to resolve incidents resolved in the last 24h. Delta vs the previous 24h."
        href="/reports"
        badge="24h"
        delta={kpis && prev ? computeDelta(kpis.mttr_minutes, prev.mttr_minutes, "lower-is-better") : null}
        deltaSuffix="vs prev 24h"
        loading={loading}
        naText={naText}
        unavailableReason={brief ? "No incident was resolved in the last 24h" : reason}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

const DEVICE_ROWS: { key: keyof CommandCenterBrief["infrastructure_health"]["devices"]; label: string; color: string }[] = [
  { key: "healthy", label: "Healthy", color: "var(--ecc-good)" },
  { key: "warning", label: "Warning", color: "var(--ecc-warn)" },
  { key: "critical", label: "Critical", color: "var(--ecc-bad)" },
  { key: "down", label: "Down / unreachable", color: "var(--ecc-down)" },
  { key: "unknown", label: "Unknown", color: "var(--ecc-unknown)" },
];

function InfraHealthCard({ q }: { q: ReturnType<typeof useCommandCenterBrief> }) {
  const devices = q.data?.infrastructure_health.devices;
  return (
    <CardShell
      title="Infrastructure Health"
      info="Current status of every registered device, using the same rule as the per-site honeycomb."
      aside="Current state"
      state={stateOf(q, devices?.total === 0)}
      emptyText="No devices registered yet. Add a monitoring source to start collecting."
      onRetry={() => q.refetch()}
      className="ecc-span-4"
    >
      {devices && (
        <div className="ecc-split">
          <DonutRing
            label={`${devices.total} devices: ${DEVICE_ROWS.map((r) => `${devices[r.key]} ${r.label}`).join(", ")}`}
            centerValue={formatCount(devices.total)}
            centerLabel="Devices"
            segments={DEVICE_ROWS.map((r) => ({ key: r.key, value: devices[r.key], color: r.color }))}
          />
          <Legend
            total={devices.total}
            rows={DEVICE_ROWS.map((r) => ({ key: r.key, label: r.label, value: devices[r.key], color: r.color, href: "/explorer" }))}
          />
        </div>
      )}
    </CardShell>
  );
}

function CollectorHealthCard({ q }: { q: ReturnType<typeof useCommandCenterBrief> }) {
  const collectors = q.data?.infrastructure_health.collectors;
  const failures = q.data?.trends.collection_failures;
  const share = collectors ? pct(collectors.healthy, collectors.total) : null;
  const R = 46;
  const C = Math.PI * R;
  const rows = collectors
    ? [
        { label: "Healthy", value: collectors.healthy, color: "var(--ecc-good)" },
        { label: "Degraded", value: collectors.degraded, color: "var(--ecc-warn)" },
        { label: "Failed", value: collectors.failed, color: "var(--ecc-bad)" },
        { label: "Stale", value: collectors.stale, color: "var(--ecc-unknown)" },
        { label: "Unknown", value: collectors.unknown, color: "var(--ecc-unknown)" },
      ]
    : [];
  return (
    <CardShell
      title="Collector Health & Data Freshness"
      info="Run-based health of each configured collector over the last 24h, and failed runs per hour."
      aside={<Link className="ecc-link" href="/monitoring">Sources →</Link>}
      state={stateOf(q, collectors?.total === 0)}
      emptyText="No collectors configured."
      onRetry={() => q.refetch()}
      className="ecc-span-3"
    >
      {collectors && (
        <>
          <div className="ecc-split">
            <svg width={110} height={66} viewBox="0 0 110 66" role="img" aria-label={`${formatPct(share)} of collectors healthy`}>
              <path d="M 9 58 A 46 46 0 0 1 101 58" fill="none" stroke="var(--ecc-track)" strokeWidth={10} strokeLinecap="round" />
              {share !== null && share > 0 && (
                <path
                  d="M 9 58 A 46 46 0 0 1 101 58"
                  fill="none"
                  stroke={share >= 95 ? "var(--ecc-good)" : share >= 80 ? "var(--ecc-warn)" : "var(--ecc-bad)"}
                  strokeWidth={10}
                  strokeLinecap="round"
                  strokeDasharray={`${(share / 100) * C} ${C}`}
                />
              )}
              <text x={55} y={50} textAnchor="middle" fontSize={18} fontWeight={700} fill="var(--ecc-text)">
                {formatPct(share)}
              </text>
              <text x={55} y={64} textAnchor="middle" fontSize={9} fill="var(--ecc-text-muted)">
                {collectors.healthy}/{collectors.total} healthy
              </text>
            </svg>
            <ul className="ecc-status-list">
              {rows.map((r) => (
                <li key={r.label}>
                  <span>
                    <i style={{ background: r.color }} aria-hidden="true" />
                    {r.label}
                  </span>
                  <strong>{r.value}</strong>
                </li>
              ))}
            </ul>
          </div>
          {failures?.available && failures.buckets.some((b) => b.count > 0) ? (
            <Chart
              type="bar"
              height={70}
              grid={false}
              series={[
                {
                  name: "Failed runs",
                  color: "var(--ecc-bad)",
                  data: failures.buckets.map((b) => ({ label: formatClock(b.ts).slice(0, 5), value: b.count })),
                },
              ]}
            />
          ) : (
            <p className="ecc-card__note">No failed collection runs in the last 24h.</p>
          )}
          <p className="ecc-card__note" title={q.data!.collection_health.distinction}>
            A failed collection means data was not delivered — not that the monitored infrastructure is down.
          </p>
        </>
      )}
    </CardShell>
  );
}

function RecentIncidentsCard({
  q,
  siteById,
}: {
  q: ReturnType<typeof useRecentIncidents>;
  siteById: Map<string, SiteHealthItem>;
}) {
  const rows: Incident[] = q.data ?? [];
  return (
    <CardShell
      title="Recent Incidents"
      info="Most recent incidents for the selected site and severity."
      aside={<Link className="ecc-link" href="/incidents">View all →</Link>}
      state={stateOf(q, rows.length === 0)}
      emptyText="No incidents match the current filters."
      onRetry={() => q.refetch()}
      skeletonHeight={220}
      className="ecc-span-5 ecc-span-wide"
    >
      <div className="ecc-table-wrap">
        <table className="ecc-table">
          <caption className="ecc-sr-only">Recent incidents</caption>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Title</th>
              <th scope="col">Site</th>
              <th scope="col">Status</th>
              <th scope="col">Age</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((inc) => {
              const site = inc.site_id ? siteById.get(inc.site_id) : undefined;
              return (
                <tr key={inc.id}>
                  <td>
                    <span className="ecc-pill" style={{ color: sevColor(inc.severity) }}>
                      {titleCase(inc.severity)}
                    </span>
                  </td>
                  <td className="ecc-table__title">
                    <Link className="ecc-table__link" href={`/incidents/${inc.id}`}>
                      {inc.title}
                    </Link>
                  </td>
                  <td title={site?.name}>{site?.code ?? (inc.site_id ? "—" : "Unassigned")}</td>
                  <td>
                    <span className="ecc-pill" style={{ color: STATUS_TONE[inc.status] ?? "var(--ecc-text-muted)" }}>
                      {titleCase(inc.status)}
                    </span>
                  </td>
                  <td title={`Last alert ${formatAge(inc.last_alert_at)} ago`}>{formatAge(inc.opened_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </CardShell>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ExecutiveCommandCenter() {
  const { filters, update } = useCommandCenterFilters();
  const queryClient = useQueryClient();

  const brief = useCommandCenterBrief();
  const analytics = useIncidentAnalytics(filters.days, { siteId: filters.siteId, severity: filters.severity });
  const siteHealth = useSiteHealth();
  const recent = useRecentIncidents({ siteId: filters.siteId, severity: filters.severity });
  // Same query key as the sidebar's Alerts badge, so this reuses that cache.
  const alertSummary = useQuery({
    queryKey: ["nav", "alert-summary"],
    queryFn: () => getAlertSummary(),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });

  const briefingInput = useMemo<BriefingInput>(() => {
    const b = brief.data;
    return {
      devices: b?.infrastructure_health.devices ?? null,
      collectors: b?.infrastructure_health.collectors ?? null,
      sourceErrors: b?.collection_health.sources.map((src) => ({ name: src.name, error: src.error })) ?? null,
      incidents: b
        ? { open: b.kpis.open_incidents, critical: b.kpis.critical_open_incidents, opened24h: b.kpis.incident_volume_24h, resolved24h: b.kpis.resolved_incidents_24h }
        : null,
      sites: siteHealth.data ? { total: siteHealth.data.sites.length, unassignedDevices: siteHealth.data.unassigned_devices } : null,
      alerts: alertSummary.data ? { open: alertSummary.data.by_status.open, critical: alertSummary.data.by_severity.critical } : null,
    };
  }, [brief.data, siteHealth.data, alertSummary.data]);

  const sites = useMemo(() => siteHealth.data?.sites ?? [], [siteHealth.data]);
  const siteById = useMemo(() => new Map(sites.map((s) => [s.site_id, s])), [sites]);
  const a = analytics.data;
  const queries = [brief, analytics, siteHealth, recent];
  const updatedAt = Math.max(0, ...queries.map((q) => q.dataUpdatedAt || 0)) || null;
  const anyError = queries.some((q) => !!q.error);
  const refreshing = queries.some((q) => q.isFetching);

  const scopeNote = [
    filters.siteId ? siteById.get(filters.siteId)?.code ?? "1 site" : null,
    filters.severity ? titleCase(filters.severity) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const windowAside = `${RANGE_LABEL[filters.range]}${scopeNote ? ` · ${scopeNote}` : ""}`;
  const analyticsState = (empty: boolean) => stateOf(analytics, empty);
  const severityRows = INCIDENT_SEVERITIES.map((s) => ({
    key: s,
    label: titleCase(s),
    value: a?.severity.find((x) => x.severity === s)?.count ?? 0,
    color: sevColor(s),
    href: `/incidents?severity=${s}`,
  }));

  return (
    <div className="ecc">
      <Header
        range={filters.range}
        siteId={filters.siteId}
        severity={filters.severity}
        sites={sites}
        updatedAt={updatedAt}
        anyError={anyError}
        refreshing={refreshing}
        onChange={update}
        onRefresh={() => queryClient.invalidateQueries({ queryKey: ["dashboard"] })}
      />

      <SituationBriefing input={briefingInput} loading={brief.isLoading && !brief.data} />

      <KpiStrip brief={brief.data} loading={brief.isLoading && !brief.data} error={!!brief.error} />

      <div className="ecc-row">
        <CardShell
          title="Incident Trends"
          info="Incidents opened and resolved per UTC day for the selected range and filters."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-5 ecc-span-wide"
        >
          {a && <TrendChart points={a.trend} />}
        </CardShell>

        <CardShell
          title="Incident Severity Distribution"
          info="Incidents opened in the selected range, by severity. Select a row to open those incidents."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-3"
        >
          {a && (
            <div className="ecc-split">
              <DonutRing
                label={`${a.total_opened} incidents opened: ${severityRows.map((r) => `${r.value} ${r.label}`).join(", ")}`}
                centerValue={formatCount(a.total_opened)}
                centerLabel="Incidents"
                size={116}
                segments={severityRows}
              />
              <Legend total={a.total_opened} rows={severityRows} />
            </div>
          )}
        </CardShell>

        <InfraHealthCard q={brief} />
      </div>

      <div className="ecc-row">
        <CardShell
          title="Top Affected Sites"
          info="Sites with the most incidents opened in the selected range."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-3"
        >
          {a && <TopSites sites={a.top_sites} other={a.other_sites_count} />}
        </CardShell>

        <CardShell
          title="Infrastructure Overview (Sites)"
          info="Each site's worst device state and its active incident count. Select a site to filter the dashboard."
          aside={siteHealth.data?.unassigned_devices ? `${siteHealth.data.unassigned_devices} devices unassigned` : undefined}
          state={stateOf(siteHealth, sites.length === 0)}
          emptyText="No sites defined yet."
          onRetry={() => siteHealth.refetch()}
          className="ecc-span-3"
        >
          <SiteHoneycomb sites={sites} selectedSiteId={filters.siteId} onSelect={(siteId) => update({ siteId })} />
        </CardShell>

        <CollectorHealthCard q={brief} />

        <CardShell
          title="Incident Aging"
          info="Currently active incidents by how long they have been open, stacked by severity."
          aside={scopeNote || "Active incidents"}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-3"
        >
          {a && <AgingChart buckets={a.aging} />}
        </CardShell>
      </div>

      <div className="ecc-row">
        <RecentIncidentsCard q={recent} siteById={siteById} />

        <CardShell
          title="Incident Heatmap"
          info="Incidents opened by weekday and hour (UTC) — reveals recurring load windows."
          aside={a ? `Last ${a.heatmap_days / 7} weeks${scopeNote ? ` · ${scopeNote}` : ""}` : undefined}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-4"
        >
          {a && <Heatmap grid={a.heatmap} />}
        </CardShell>

        <CardShell
          title="Resolution Time Distribution"
          info="How long incidents resolved in the selected range took to resolve."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-3"
        >
          {a &&
            (a.duration.resolved_count === 0 ? (
              <div className="ecc-state" role="status">
                No incidents resolved in this window
              </div>
            ) : (
              <>
                <Chart
                  type="bar"
                  height={130}
                  yLabel="incidents"
                  series={[
                    {
                      name: "Resolved incidents",
                      color: "var(--ecc-accent)",
                      data: a.duration.buckets.map((b) => ({ label: b.label, value: b.count })),
                    },
                  ]}
                />
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8, marginTop: 8 }}>
                  {(
                    [
                      ["Median", a.duration.median_minutes],
                      ["P90", a.duration.p90_minutes],
                      ["P95", a.duration.p95_minutes],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label} style={{ border: "1px solid var(--ecc-border)", borderRadius: 8, padding: "6px 8px" }}>
                      <div style={{ fontSize: 11, color: "var(--ecc-text-muted)" }}>{label}</div>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>{formatDuration(value)}</div>
                    </div>
                  ))}
                </div>
              </>
            ))}
        </CardShell>
      </div>
    </div>
  );
}
