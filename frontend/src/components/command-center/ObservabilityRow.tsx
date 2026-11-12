"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { STATE_LABEL, STATE_TONE, getIntegrationCenter, integrationHref, type IntegrationState } from "@/lib/integrationCenter";
import { getNetworkOverview } from "@/lib/network";
import { getLogStats } from "@/lib/logs";
import { getTraceServices } from "@/lib/traces";
import { getAvailabilityReport } from "@/lib/observabilityReports";
import { NOT_MEASURED, formatAge, formatCount } from "@/lib/commandCenter/format";
import { CardShell, type CardState } from "./primitives";

/**
 * Executive Command Center observability row: the platform's own observability
 * modules on the main dashboard, each from the API its page already uses —
 *
 *   Integrations   GET /api/integration-center         (P12)
 *   Network        GET /api/network/overview           (P4)
 *   Logs & traces  GET /api/logs/stats, /api/traces/services, last 24 h (P7, P8)
 *   Availability   GET /api/reports/availability, last 7 days (P11)
 *
 * No numbers are computed here beyond sums of what the API returned; a failed
 * read shows the card's error state, never a zero. Every card drills into the
 * page that owns the data.
 */

const POLL_MS = 60_000;
const HOUR = 3_600_000;

function stateOf(q: { isLoading: boolean; isError: boolean }, empty = false): CardState {
  if (q.isError) return "error";
  if (q.isLoading) return "loading";
  return empty ? "empty" : "ready";
}

/** A rolling window, rounded to the minute so the query key stays stable between renders. */
function rollingWindow(hours: number) {
  const until = new Date(Math.floor(Date.now() / 60_000) * 60_000);
  return { since: new Date(until.getTime() - hours * HOUR).toISOString(), until: until.toISOString() };
}

type Tone = "good" | "warn" | "bad";

function Stat({ label, value, sub, tone, href }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; href?: string }) {
  const body = (
    <div className="ecc-mini">
      <dt>{label}</dt>
      <dd className={tone ? `is-${tone}` : undefined}>{value}</dd>
      {sub && <span className="ecc-mini__sub">{sub}</span>}
    </div>
  );
  return href ? <Link href={href} style={{ textDecoration: "none", color: "inherit" }}>{body}</Link> : body;
}

const minis2 = { gridTemplateColumns: "repeat(2, minmax(0, 1fr))" } as const;
const listStyle = { listStyle: "none", margin: "10px 0 0", padding: 0, display: "grid", gap: 4, fontSize: 12 } as const;
const rowStyle = { display: "flex", justifyContent: "space-between", gap: 8, minWidth: 0 } as const;
const ellipsis = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 } as const;
const TONE_COLOR: Record<string, string> = { good: "var(--ecc-good)", warn: "var(--ecc-warn)", bad: "var(--ecc-bad)", neutral: "var(--ecc-text-muted)" };
const ATTENTION: IntegrationState[] = ["failing", "degraded", "stale"];

function IntegrationsCard() {
  const q = useQuery({ queryKey: ["dashboard", "integration-center"], queryFn: getIntegrationCenter, refetchInterval: POLL_MS, retry: false });
  const d = q.data;
  const s = d?.summary;
  const attention = s ? ATTENTION.reduce((n, k) => n + s[k], 0) : 0;
  const worst = (d?.items ?? []).filter((r) => r.state !== "healthy" && r.state !== "disabled").slice(0, 4);
  return (
    <CardShell
      title="Integrations"
      info="Every monitoring source and integration with one state derived from what was recorded (Integration Center). Failing: last collection failed. Stale: no success for three poll intervals."
      aside={<Link className="ecc-link" href="/monitoring/integrations">Integration Center →</Link>}
      state={stateOf(q, !!s && s.total === 0)}
      emptyText="No monitoring source or integration is configured."
      onRetry={() => q.refetch()}
      className="ecc-span-3"
    >
      {s && (
        <>
          <dl className="ecc-minis" style={minis2}>
            <Stat label="Need attention" value={formatCount(attention)} tone={attention ? "bad" : "good"}
              sub={`${s.failing} failing · ${s.degraded} degraded · ${s.stale} stale`} href="/monitoring/integrations" />
            <Stat label="Healthy" value={`${formatCount(s.healthy)} / ${formatCount(s.total)}`} tone={s.healthy === s.total ? "good" : undefined}
              sub={`${s.never_synced} never synced · ${s.disabled} disabled`} />
            <Stat label="Collector runs 24 h" value={formatCount(s.runs_24h)} sub={`${formatCount(s.failed_runs_24h)} failed`}
              tone={s.failed_runs_24h ? "warn" : undefined} />
            <Stat label="Sources" value={formatCount(s.sources)} sub={`${formatCount(s.integrations)} integrations`} />
          </dl>
          {worst.length > 0 && (
            <ul style={listStyle} aria-label="Integrations needing attention">
              {worst.map((r) => (
                <li key={`${r.kind}:${r.id}`} style={rowStyle}>
                  {integrationHref(r) ? (
                    <Link className="ecc-table__link" href={integrationHref(r)!} style={ellipsis} title={r.last_error ?? r.name}>{r.name}</Link>
                  ) : (
                    <span style={ellipsis} title={r.last_error ?? r.name}>{r.name}</span>
                  )}
                  <span className="ecc-pill" style={{ color: TONE_COLOR[STATE_TONE[r.state]] }}>{STATE_LABEL[r.state]}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </CardShell>
  );
}

function NetworkCard() {
  const q = useQuery({ queryKey: ["dashboard", "network-overview"], queryFn: getNetworkOverview, refetchInterval: POLL_MS, retry: false });
  const d = q.data;
  const i = d?.interfaces;
  const top = (d?.top_utilization ?? []).filter((x) => x.utilization_pct !== null).slice(0, 3);
  return (
    <CardShell
      title="Network"
      info="Interfaces and VPN tunnels from collected interface state and telemetry (Network Monitoring). Down while enabled: administratively up but operationally down."
      aside={<Link className="ecc-link" href="/network">Network →</Link>}
      state={stateOf(q, !!i && i.total === 0 && d!.tunnels.total === 0)}
      emptyText="No interfaces or VPN tunnels collected yet."
      onRetry={() => q.refetch()}
      className="ecc-span-3"
    >
      {d && i && (
        <>
          <dl className="ecc-minis" style={minis2}>
            <Stat label="Interfaces up" value={`${formatCount(i.up)} / ${formatCount(i.total)}`} sub={`${formatCount(i.devices)} devices · ${formatCount(i.admin_down)} admin down`} />
            <Stat label="Down while enabled" value={formatCount(i.unexpected_down)} tone={i.unexpected_down ? "bad" : "good"}
              href="/network" />
            <Stat label="VPN tunnels up" value={d.tunnels.total ? `${formatCount(d.tunnels.up)} / ${formatCount(d.tunnels.total)}` : NOT_MEASURED}
              tone={d.tunnels.down ? "bad" : undefined} sub={d.tunnels.total ? `${formatCount(d.tunnels.down)} down` : "No tunnels collected"} />
            <Stat label="Telemetry" value={i.last_telemetry_at ? formatAge(i.last_telemetry_at) : NOT_MEASURED}
              sub={`${formatCount(i.fresh)} fresh · ${formatCount(i.measured)} measured`} tone={i.last_telemetry_at ? undefined : "warn"} />
          </dl>
          {top.length > 0 && (
            <ul style={listStyle} aria-label="Busiest interfaces">
              {top.map((x) => (
                <li key={x.id} style={rowStyle}>
                  <Link className="ecc-table__link" href={`/network/interfaces/${x.id}`} style={ellipsis}>{x.device_name} · {x.name}</Link>
                  <strong className={x.utilization_pct! >= 80 ? "is-warn" : undefined}>{Math.round(x.utilization_pct!)} %</strong>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </CardShell>
  );
}

function LogsTracesCard() {
  const w = rollingWindow(24);
  const logs = useQuery({ queryKey: ["dashboard", "log-stats", w.since], queryFn: () => getLogStats(w), refetchInterval: POLL_MS, retry: false });
  const traces = useQuery({ queryKey: ["dashboard", "trace-services", w.since], queryFn: () => getTraceServices(w), refetchInterval: POLL_MS, retry: false });
  const sum = (k: "critical" | "warning" | "info") => (logs.data?.histogram ?? []).reduce((n, b) => n + b[k], 0);
  const lines = logs.data ? sum("critical") + sum("warning") + sum("info") : 0;
  const svc = traces.data ?? [];
  const spans = svc.reduce((n, x) => n + x.spans, 0);
  const errSpans = svc.reduce((n, x) => n + x.error_spans, 0);
  const worstSvc = [...svc].sort((a, b) => b.error_rate_pct - a.error_rate_pct)[0];
  const topHost = logs.data?.facets.host?.[0];
  const state: CardState = logs.isLoading || traces.isLoading ? "loading" : logs.isError && traces.isError ? "error" : "ready";
  return (
    <CardShell
      title="Logs & Traces"
      info="Log lines stored as events (syslog receiver and collectors) and OpenTelemetry spans, in the last 24 hours."
      aside={<><Link className="ecc-link" href="/logs">Logs</Link>{" · "}<Link className="ecc-link" href="/traces">Traces →</Link></>}
      state={state}
      onRetry={() => { logs.refetch(); traces.refetch(); }}
      className="ecc-span-3"
    >
      <dl className="ecc-minis" style={minis2}>
        {logs.isError ? (
          <Stat label="Log lines 24 h" value={NOT_MEASURED} sub="Logs unavailable" />
        ) : (
          <Stat label="Log lines 24 h" value={formatCount(lines)} href="/logs"
            sub={lines ? `${formatCount(sum("critical"))} critical · ${formatCount(sum("warning"))} warning` : "No log lines received"} />
        )}
        <Stat label="Top log source" value={topHost ? formatCount(topHost.count) : NOT_MEASURED} sub={topHost ? topHost.value : "—"} />
        {traces.isError ? (
          <Stat label="Traced services" value={NOT_MEASURED} sub="Traces unavailable" />
        ) : (
          <Stat label="Traced services" value={formatCount(svc.length)} href="/traces"
            sub={svc.length ? `${formatCount(spans)} spans` : "No spans received"} />
        )}
        <Stat label="Error spans" value={svc.length ? formatCount(errSpans) : NOT_MEASURED} tone={errSpans ? "bad" : svc.length ? "good" : undefined}
          sub={worstSvc && worstSvc.error_rate_pct > 0 ? `${worstSvc.service} ${worstSvc.error_rate_pct} %` : undefined} />
      </dl>
    </CardShell>
  );
}

function AvailabilityCard() {
  const w = rollingWindow(24 * 7);
  const q = useQuery({ queryKey: ["dashboard", "availability-7d", w.since.slice(0, 13)], queryFn: () => getAvailabilityReport(w), refetchInterval: 5 * POLL_MS, retry: false });
  const s = q.data?.summary;
  const worst = (q.data?.items ?? []).filter((r) => r.availability_pct < 99.9).sort((a, b) => a.availability_pct - b.availability_pct).slice(0, 3);
  const pct = s?.fleet_availability_pct;
  return (
    <CardShell
      title="Availability (7 days)"
      info="Share of recorded reachability checks that were up, per device and across the fleet. Devices with no check are not measured, never counted as available."
      aside={<Link className="ecc-link" href="/reports/availability">Report →</Link>}
      state={stateOf(q, !!s && s.devices_measured === 0 && s.devices_not_measured === 0)}
      emptyText="No devices to measure."
      onRetry={() => q.refetch()}
      className="ecc-span-3"
    >
      {s && (
        <>
          <dl className="ecc-minis" style={minis2}>
            <Stat label="Fleet availability" value={pct === null || pct === undefined ? NOT_MEASURED : `${pct} %`}
              tone={pct === null || pct === undefined ? undefined : pct >= 99.9 ? "good" : pct >= 99 ? "warn" : "bad"} href="/reports/availability" />
            <Stat label="Devices measured" value={formatCount(s.devices_measured)} sub={`${formatCount(s.devices_not_measured)} not measured`}
              tone={s.devices_measured === 0 ? "warn" : undefined} />
            <Stat label="Below 99.9 %" value={formatCount(s.devices_below_99_9)} tone={s.devices_below_99_9 ? "warn" : undefined} />
            <Stat label="Below 99 %" value={formatCount(s.devices_below_99)} tone={s.devices_below_99 ? "bad" : undefined} />
          </dl>
          {worst.length > 0 && (
            <ul style={listStyle} aria-label="Least available devices">
              {worst.map((r) => (
                <li key={r.device_id} style={rowStyle}>
                  <Link className="ecc-table__link" href={`/infrastructure/hosts/${r.device_id}`} style={ellipsis}>{r.device}</Link>
                  <strong className={r.availability_pct < 99 ? "is-bad" : "is-warn"}>{r.availability_pct} %</strong>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </CardShell>
  );
}

export default function ObservabilityRow() {
  return (
    <div className="ecc-row" aria-label="Observability">
      <IntegrationsCard />
      <NetworkCard />
      <LogsTracesCard />
      <AvailabilityCard />
    </div>
  );
}
