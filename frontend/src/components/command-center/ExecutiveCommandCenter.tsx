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
  rangeDays,
  type CommandCenterRange,
} from "@/lib/commandCenter/format";
import { CardShell, DonutRing, KpiTile, Legend, type CardState } from "./primitives";
import SiteHoneycomb from "./SiteHoneycomb";
import CoverageRow from "./CoverageRow";
import AppearanceMenu from "@/components/AppearanceMenu";
import SituationBriefing from "./SituationBriefing";
import { getAlertSummary, getBackupOverview, getTaskCounts, getWorkflowStatistics, type CloudOperationsKPI } from "@/lib/api";
import { useCloudOperations } from "@/hooks/useCloudOperations";
import { BarList, DayColumns } from "@/components/ui/PageKit";
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
  "90d": "Last 90 days",
};

/**
 * Shown instead of an all-zero chart when the selected window has no
 * incidents: when the last one happened, and a one-click wider window that
 * would include it (or the widest window when it is older still).
 */
function QuietWindow({
  what,
  range,
  lastAt,
  now,
  onWiden,
}: {
  what: string;
  range: CommandCenterRange;
  lastAt: string | null;
  /** Reference time (when the data was fetched), not the render clock. */
  now: number;
  onWiden: (range: CommandCenterRange) => void;
}) {
  const days = lastAt ? Math.max(1, Math.ceil((now - Date.parse(lastAt)) / 86_400_000)) : null;
  const target = RANGES.find((r) => rangeDays(r) > rangeDays(range) && (days === null || rangeDays(r) >= days)) ?? RANGES[RANGES.length - 1];
  const canWiden = rangeDays(target) > rangeDays(range);
  return (
    <div className="ecc-quiet" role="status">
      <p className="ecc-quiet__title">No {what} in the {RANGE_LABEL[range].toLowerCase()}</p>
      <p className="ecc-quiet__detail">
        {lastAt ? `Last incident activity ${days} day${days === 1 ? "" : "s"} ago (${new Date(lastAt).toLocaleDateString()}).` : "No incident has been recorded yet."}
      </p>
      {canWiden && lastAt && (
        <button type="button" className="ecc-quiet__action" onClick={() => onWiden(target)}>
          Show {RANGE_LABEL[target].toLowerCase()}
        </button>
      )}
    </div>
  );
}

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
        naText={brief ? "None acknowledged in 24h" : naText}
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
        naText={brief ? "None resolved in 24h" : naText}
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
  // Latest incident activity (same filters). The recent list is ordered by
  // last_alert_at, so its maximum is exact even though the list is capped.
  const lastIncidentAt = (recent.data ?? []).reduce<string | null>((m, i) => (!m || i.last_alert_at > m ? i.last_alert_at : m), null);
  const quietWindow = !!a && a.total_opened === 0 && a.duration.resolved_count === 0;
  const quiet = (what: string) => (
    <QuietWindow what={what} range={filters.range} lastAt={lastIncidentAt} now={analytics.dataUpdatedAt || recent.dataUpdatedAt} onWiden={(range) => update({ range })} />
  );
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

      <CoverageRow />

      <div className="ecc-row">
        <CardShell
          title="Incident Trends"
          info="Incidents opened and resolved per UTC day for the selected range and filters."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-5 ecc-span-wide"
        >
          {a && (quietWindow ? quiet("incidents opened or resolved") : <TrendChart points={a.trend} />)}
        </CardShell>

        <CardShell
          title="Incident Severity Distribution"
          info="Incidents opened in the selected range, by severity. Select a row to open those incidents."
          aside={windowAside}
          state={analyticsState(false)}
          onRetry={() => analytics.refetch()}
          className="ecc-span-3"
        >
          {a && a.total_opened === 0 && quiet("incidents opened")}
          {a && a.total_opened > 0 && (
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
          {a &&
            (siteHealth.data && sites.length === 0 ? (
              <div className="ecc-quiet" role="status">
                <p className="ecc-quiet__title">Ranked by site once sites exist</p>
                <p className="ecc-quiet__detail">
                  {a.total_opened
                    ? `${a.total_opened.toLocaleString("en-US")} incident${a.total_opened === 1 ? "" : "s"} opened in this window, none attributable to a site yet.`
                    : "No incidents opened in this window either."}{" "}
                  See Infrastructure Overview to set sites up.
                </p>
              </div>
            ) : a.total_opened === 0 ? (
              quiet("incidents opened")
            ) : (
              <TopSites sites={a.top_sites} other={a.other_sites_count} />
            ))}
        </CardShell>

        <CardShell
          title="Infrastructure Overview (Sites)"
          info="Each site's worst device state and its active incident count. Select a site to filter the dashboard."
          aside={siteHealth.data?.unassigned_devices ? `${siteHealth.data.unassigned_devices} devices unassigned` : undefined}
          state={stateOf(siteHealth, false)}
          onRetry={() => siteHealth.refetch()}
          className="ecc-span-3"
        >
          {siteHealth.data && sites.length === 0 ? (
            <div className="ecc-quiet" role="status">
              <p className="ecc-quiet__title">No sites yet</p>
              <p className="ecc-quiet__detail">
                {siteHealth.data.unassigned_devices
                  ? `${siteHealth.data.unassigned_devices} devices are monitored but belong to no site. `
                  : ""}
                Sites unlock per-location health, the site filter on this page and site impact on incidents.
              </p>
              <ol className="ecc-quiet__steps">
                <li>Create your locations in Organization</li>
                <li>Assign devices to them</li>
              </ol>
              <a className="ecc-quiet__action" href="/organization">Set up sites</a>
            </div>
          ) : (
            <SiteHoneycomb sites={sites} selectedSiteId={filters.siteId} onSelect={(siteId) => update({ siteId })} />
          )}
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
          {a &&
            (a.heatmap.every((row) => row.every((n) => n === 0)) ? (
              <div className="ecc-quiet" role="status">
                <p className="ecc-quiet__title">No incidents opened in the last {a.heatmap_days / 7} weeks</p>
                <p className="ecc-quiet__detail">
                  {lastIncidentAt
                    ? `Last incident activity ${new Date(lastIncidentAt).toLocaleDateString()}. The heatmap fills in as incidents open, revealing recurring load windows.`
                    : "The heatmap fills in as incidents open, revealing recurring load windows."}
                </p>
              </div>
            ) : (
              <Heatmap grid={a.heatmap} />
            ))}
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
              quiet("incidents resolved")
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

      <div className="ecc-row">
        <BackupHealthCard />
      </div>

      <div className="ecc-row">
        <CloudOperationsCard />
        <AutomationWorkCard />
      </div>
    </div>
  );
}

const BACKUP_RPO = [
  { key: "within", label: "Within RPO", color: "var(--ecc-good)" },
  { key: "breached", label: "RPO breached", color: "var(--ecc-bad)" },
  { key: "never", label: "Never backed up", color: "var(--ecc-warn)" },
  { key: "not_protected", label: "Not protected", color: "var(--ecc-unknown)" },
] as const;

/** Data protection row: RPO compliance, job outcomes and failures from the
 *  Azure Backup collector. One line with a connect link until a source exists. */
function BackupHealthCard() {
  const q = useQuery({ queryKey: ["dashboard", "backup-overview"], queryFn: getBackupOverview, refetchInterval: POLL_MS, retry: false });
  const o = q.data;
  const state: CardState = q.isLoading ? "loading" : q.error ? "error" : "ready";
  const counts = o ? { within: o.items_within_rpo, breached: o.items_breaching_rpo, never: o.items_never_backed_up, not_protected: o.items_not_protected } : null;
  return (
    <CardShell
      title="Backup Health"
      info="Azure Backup: protected items against the recovery-point objective, job outcomes and failures. Select to open Backup."
      aside={o?.configured ? (o.observed_at ? `Collected ${formatAge(o.observed_at)}` : "Awaiting first collection") : undefined}
      state={state}
      onRetry={() => q.refetch()}
      className="ecc-span-full"
    >
      {o && !o.configured && (
        <div className="ecc-quiet ecc-quiet--inline" role="status">
          <p className="ecc-quiet__title">Backup monitoring is not connected</p>
          <p className="ecc-quiet__detail">Connect an Azure subscription (read-only, Backup Reader role) to track backup recency, failed jobs and vault security here.</p>
          <Link className="ecc-quiet__action" href="/infrastructure/backup">Connect Azure Backup</Link>
        </div>
      )}
      {o && o.configured && counts && (
        <div className="ecc-backup">
          <Link href="/infrastructure/backup" className="ecc-split" aria-label="Open Backup">
            <DonutRing
              label={`${o.items_total} backup items: ${BACKUP_RPO.map((r) => `${counts[r.key]} ${r.label}`).join(", ")}`}
              centerValue={formatCount(o.items_total)}
              centerLabel="Items"
              size={104}
              segments={BACKUP_RPO.map((r) => ({ key: r.key, value: counts[r.key], color: r.color }))}
            />
            <Legend total={o.items_total} rows={BACKUP_RPO.map((r) => ({ key: r.key, label: r.label, value: counts[r.key], color: r.color }))} />
          </Link>
          <dl className="ecc-backup__stats">
            <div><dt>RPO</dt><dd>{o.rpo_hours} h</dd></div>
            <div><dt>Last backup failed</dt><dd className={o.items_last_failed ? "is-bad" : undefined}>{o.items_last_failed}</dd></div>
            <div><dt>Job success 24h</dt><dd>{o.success_rate_24h === null ? NOT_MEASURED : `${o.success_rate_24h}%`}</dd></div>
            <div><dt>Failed jobs 7d</dt><dd className={o.jobs_7d.failed ? "is-bad" : undefined}>{o.jobs_7d.failed}</dd></div>
            <div><dt>Vaults</dt><dd>{o.vaults}{o.vaults_soft_delete_off ? <span className="is-warn"> · {o.vaults_soft_delete_off} soft delete off</span> : null}</dd></div>
          </dl>
          <div className="ecc-backup__jobs">
            <DayColumns
              label="Backup jobs per day, last 14 days"
              height={96}
              buckets={o.daily.map((d) => ({ key: d.day, label: d.day.slice(5), values: { completed: d.completed, completed_with_warnings: d.completed_with_warnings, failed: d.failed, other: d.other } }))}
              series={[
                { key: "completed", label: "Completed", color: "var(--ecc-good)" },
                { key: "completed_with_warnings", label: "Warnings", color: "var(--ecc-warn)" },
                { key: "failed", label: "Failed", color: "var(--ecc-bad)" },
                { key: "other", label: "Other", color: "var(--ecc-unknown)" },
              ]}
            />
          </div>
        </div>
      )}
    </CardShell>
  );
}

/** One compact figure in a dashboard card; "Not configured" (with the reason
 *  as a tooltip) instead of a number when the integration does not exist. */
function MiniStat({ label, value, sub, reason, tone }: { label: string; value: string | null; sub?: string; reason?: string | null; tone?: "bad" | "warn" | "good" }) {
  return (
    <div className="ecc-mini">
      <dt>{label}</dt>
      {value === null ? (
        <dd className="ecc-mini__na" title={reason ?? undefined}>Not configured</dd>
      ) : (
        <dd className={tone ? `is-${tone}` : undefined}>{value}</dd>
      )}
      {sub && <span className="ecc-mini__sub">{sub}</span>}
    </div>
  );
}

/** Cloud Operations: the six-card snapshot (GET /api/dashboard/cloud-operations),
 *  including Backup Readiness. Unconfigured integrations say so with the reason. */
function CloudOperationsCard() {
  const q = useCloudOperations();
  const c = q.data;
  const state: CardState = q.isLoading ? "loading" : q.error ? "error" : "ready";
  const num = (k: CloudOperationsKPI | undefined) => (k && !k.unavailable && typeof k.value === "number" ? k.value : null);
  const health = c?.resource_health.value as { online?: number; total?: number } | null | undefined;
  const readiness = num(c?.backup_readiness);
  const critical = (c?.active_incidents.detail as { critical?: number } | null)?.critical;
  return (
    <CardShell
      title="Cloud Operations"
      info="Cloud resources and their health, active incidents, and cloud integrations (spend, policy, backup). Integrations that are not connected say so instead of showing 0."
      state={state}
      onRetry={() => q.refetch()}
      className="ecc-span-half"
    >
      {c && (
        <dl className="ecc-minis">
          <MiniStat label="Cloud resources" value={num(c.cloud_resources) === null ? null : formatCount(num(c.cloud_resources)!)} reason={c.cloud_resources.unavailable_reason} />
          <MiniStat
            label="Resources online"
            value={health && typeof health.total === "number" ? `${formatCount(health.online ?? 0)} / ${formatCount(health.total)}` : null}
            reason={c.resource_health.unavailable_reason}
            tone={health && health.total && (health.online ?? 0) < health.total ? "warn" : undefined}
          />
          <MiniStat label="Active incidents" value={num(c.active_incidents) === null ? null : formatCount(num(c.active_incidents)!)} sub={critical !== undefined ? `${formatCount(critical)} critical` : undefined} reason={c.active_incidents.unavailable_reason} tone={critical ? "bad" : undefined} />
          <MiniStat label="Backup readiness" value={readiness === null ? null : `${readiness}%`} sub={readiness === null ? undefined : "protected items inside RPO"} reason={c.backup_readiness.unavailable_reason} tone={readiness !== null && readiness < 95 ? "warn" : undefined} />
          <MiniStat label="Cloud spend" value={num(c.cloud_spend) === null ? null : formatCount(num(c.cloud_spend)!)} reason={c.cloud_spend.unavailable_reason} />
          <MiniStat label="Policy compliance" value={num(c.policy_compliance) === null ? null : `${num(c.policy_compliance)}%`} reason={c.policy_compliance.unavailable_reason} />
        </dl>
      )}
    </CardShell>
  );
}

const TASK_STATE_ORDER = ["assigned", "accepted", "in_progress", "verifying", "recorded", "completed", "failed", "cancelled"];
/** Lifecycle order; states the backend adds later sort last. */
const taskRank = (state: string) => {
  const i = TASK_STATE_ORDER.indexOf(state);
  return i === -1 ? TASK_STATE_ORDER.length : i;
};

/** Automation & Work: workflow executions (GET /api/workflows/statistics) and
 *  the task queue by state (GET /api/tasks/counts). */
function AutomationWorkCard() {
  const wf = useQuery({ queryKey: ["dashboard", "workflow-statistics"], queryFn: getWorkflowStatistics, refetchInterval: POLL_MS, retry: false });
  const tasks = useQuery({ queryKey: ["dashboard", "task-counts"], queryFn: getTaskCounts, refetchInterval: POLL_MS, retry: false });
  const s = wf.data;
  const state: CardState = wf.isLoading || tasks.isLoading ? "loading" : wf.error && tasks.error ? "error" : "ready";
  const taskRows = Object.entries(tasks.data ?? {})
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => taskRank(a) - taskRank(b))
    .map(([k, n]) => ({
      key: k,
      label: titleCase(k.replace(/_/g, " ")),
      value: n,
      color: k === "failed" ? "var(--ecc-bad)" : k === "cancelled" ? "var(--ecc-unknown)" : k === "completed" || k === "recorded" ? "var(--ecc-good)" : "var(--ecc-accent)",
    }));
  return (
    <CardShell
      title="Automation & Work"
      info="Workflow executions by state and the operations task queue. A growing queue with nothing running means work is not being picked up."
      aside={<Link href="/automation">Open Automation →</Link>}
      state={state}
      onRetry={() => { wf.refetch(); tasks.refetch(); }}
      className="ecc-span-half"
    >
      {s && s.queued > 0 && s.running === 0 && (
        <p className="ecc-alert-line" role="status">
          {formatCount(s.queued)} workflow execution{s.queued === 1 ? " is" : "s are"} queued and none is running.
        </p>
      )}
      <div className="ecc-automation">
        {s ? (
          <dl className="ecc-minis ecc-minis--3">
            <MiniStat label="Executions" value={formatCount(s.total_executions)} />
            <MiniStat label="Queued" value={formatCount(s.queued)} tone={s.queued > 0 && s.running === 0 ? "warn" : undefined} />
            <MiniStat label="Awaiting approval" value={formatCount(s.waiting_approval)} tone={s.waiting_approval > 0 ? "warn" : undefined} />
            <MiniStat label="Running" value={formatCount(s.running)} />
            <MiniStat
              label="Success rate"
              value={s.success_rate === null ? NOT_MEASURED : `${Math.round(s.success_rate * 100)}%`}
              sub={`${s.succeeded} succeeded · ${s.failed + s.rolled_back} failed · ${s.cancelled} cancelled`}
              tone={s.success_rate !== null && s.success_rate < 0.8 ? "warn" : undefined}
            />
            <MiniStat
              label="Avg duration"
              value={s.avg_duration_seconds === null ? NOT_MEASURED : s.avg_duration_seconds < 60 ? `${Math.round(s.avg_duration_seconds)} s` : formatMinutes(s.avg_duration_seconds / 60)}
            />
          </dl>
        ) : (
          <p className="ecc-quiet__detail">{wf.error ? "Workflow statistics unavailable (needs workflows.read)." : ""}</p>
        )}
        <div>
          <h3 className="ecc-subhead">Task queue</h3>
          {tasks.error ? (
            <p className="ecc-quiet__detail">Task counts unavailable (needs tasks.read).</p>
          ) : (
            <BarList rows={taskRows} emptyText="No tasks yet." />
          )}
        </div>
      </div>
    </CardShell>
  );
}
