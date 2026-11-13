"use client";

import { useEffect, useState } from "react";

import { BarList, DataTable, Panel, Pill, StatStrip, StatTile, ago, type Column } from "@/components/ui/PageKit";
import { apiFetch } from "@/lib/api";
import {
  NOT_MEASURED,
  SUMMARY_PATH,
  THRESHOLDS,
  averagePerMinute,
  formatRatio,
  formatSeconds,
  formatUptime,
  toneHigherWorse,
  toneLowerWorse,
  type PlatformMetricsSummary,
  type RouteStat,
  type SourceTypeStat,
} from "@/lib/observability/platformMetrics";

/**
 * Live platform metrics for THIS backend process, read from its own
 * Prometheus registry via the superuser-only summary API. Laid out by the RED
 * method for the API (rate, errors, duration) and by outcome for the
 * collection pipeline and scheduler. Cumulative since the process started;
 * windows, rates over time and other processes belong in Grafana.
 */

const REFRESH_MS = 30_000;

const routeColumns: Column<RouteStat>[] = [
  { key: "route", header: "Route", render: (r) => (
      <>
        <span className="pk-cell-main pk-mono">{r.route}</span>
        <span className="pk-cell-sub">{r.method}</span>
      </>
    ), sortValue: (r) => r.route },
  { key: "requests", header: "Requests", numeric: true, render: (r) => r.requests.toLocaleString("en-US"), sortValue: (r) => r.requests },
  { key: "errors", header: "5xx", numeric: true, render: (r) => r.errors ? <Pill tone="bad">{r.errors}</Pill> : "0", sortValue: (r) => r.errors },
  { key: "p95", header: "p95", numeric: true, render: (r) => formatSeconds(r.p95_seconds), sortValue: (r) => r.p95_seconds },
];

const sourceColumns: Column<SourceTypeStat>[] = [
  { key: "type", header: "Source type", render: (s) => <span className="pk-mono">{s.source_type}</span>, sortValue: (s) => s.source_type },
  { key: "ok", header: "Succeeded", numeric: true, render: (s) => s.success.toLocaleString("en-US"), sortValue: (s) => s.success },
  { key: "err", header: "Failed", numeric: true, render: (s) => s.error ? <Pill tone="bad">{s.error}</Pill> : "0", sortValue: (s) => s.error },
  { key: "rate", header: "Success", numeric: true, render: (s) => formatRatio(s.success_ratio, 1), sortValue: (s) => s.success_ratio },
  { key: "p95", header: "p95 run", numeric: true, render: (s) => formatSeconds(s.p95_seconds), sortValue: (s) => s.p95_seconds },
];

export default function PlatformLiveMetrics() {
  const [data, setData] = useState<PlatformMetricsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const summary = await apiFetch<PlatformMetricsSummary>(SUMMARY_PATH);
        if (cancelled) return;
        setData(summary);
        setError(null);
        setNow(Date.now());
      } catch (err) {
        if (cancelled) return;
        // Keep the last good snapshot visible; say the refresh failed.
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!data) {
    return (
      <Panel
        title="Live platform metrics"
        state={error ? "error" : "loading"}
        errorText={error ? `Could not read platform metrics: ${error}` : undefined}
      />
    );
  }

  const { http, collection, scheduler, alerts, scope } = data;
  const rpm = averagePerMinute(http.requests_total, scope.uptime_seconds);
  const stale = scheduler.last_tick_age_seconds !== null && scheduler.last_tick_age_seconds > THRESHOLDS.tickStaleSeconds;
  const stages = Object.entries(collection.failures_by_stage);
  const severities = Object.entries(alerts.by_severity);
  const schedulerHere = scope.runtime_mode === "all" || scope.runtime_mode === "scheduler" || scope.runtime_mode === "worker";

  return (
    <section aria-label="Live platform metrics" style={{ display: "grid", gap: 16 }}>
      <div className="pk-toolbar" style={{ justifyContent: "space-between" }}>
        <span className="pk-count">
          This process: role <strong>{scope.runtime_mode}</strong> · v{scope.version} · up {formatUptime(scope.uptime_seconds)} ·
          updated {ago(data.generated_at, now)}
        </span>
        {error ? <Pill tone="warn">Refresh failed, showing last reading</Pill> : <Pill tone="info">Refreshes every 30 s</Pill>}
      </div>

      <StatStrip>
        <StatTile label="API requests" value={http.requests_total ? http.requests_total.toLocaleString("en-US") : NOT_MEASURED}
          info="Requests handled by this process since it started. Health probes, /metrics and CORS preflights (OPTIONS) are excluded."
          sub={rpm === null ? "Average rate needs a minute of uptime" : `${rpm.toFixed(1)} / min average`} />
        <StatTile label="5xx error rate" value={formatRatio(http.error_ratio)}
          tone={toneHigherWorse(http.error_ratio, THRESHOLDS.errorRatioWarn, THRESHOLDS.errorRatioBad)}
          alarm={toneHigherWorse(http.error_ratio, THRESHOLDS.errorRatioWarn, THRESHOLDS.errorRatioBad) === "bad"}
          info="Share of requests that ended in a 5xx or an unhandled exception. 4xx is a client outcome, not an error here."
          sub={`${http.errors_total.toLocaleString("en-US")} of ${http.requests_total.toLocaleString("en-US")}`} />
        <StatTile label="API latency p95" value={formatSeconds(http.p95_seconds)}
          tone={toneHigherWorse(http.p95_seconds, THRESHOLDS.p95WarnSeconds, THRESHOLDS.p95BadSeconds)}
          info="95th percentile request duration, estimated from histogram buckets the same way as PromQL histogram_quantile()."
          sub={`p50 ${formatSeconds(http.p50_seconds)} · p99 ${formatSeconds(http.p99_seconds)}`} />
        <StatTile label="Collection success" value={formatRatio(collection.success_ratio, 1)}
          tone={toneLowerWorse(collection.success_ratio, THRESHOLDS.collectionSuccessWarn, THRESHOLDS.collectionSuccessBad)}
          info="Collection runs that finished successfully in this process. Skipped runs (one already in progress) are not counted."
          sub={collection.runs_total ? `${collection.runs_total.toLocaleString("en-US")} runs · ${collection.skipped_total} skipped` : "No runs in this process"} />
        <StatTile label="Scheduler last tick" value={scheduler.last_tick_at ? ago(scheduler.last_tick_at, now) : NOT_MEASURED}
          tone={scheduler.last_tick_at ? (stale ? "bad" : "good") : "neutral"} alarm={stale}
          info="When this process last completed a scheduler tick. Stale after 3 minutes."
          sub={scheduler.last_tick_at
            ? `${scheduler.queue_depth ?? 0} due · ${scheduler.tick_failures_total} failed ticks`
            : schedulerHere ? "No tick yet" : `Not run in the ${scope.runtime_mode} role`} />
        <StatTile label="Alerts opened" value={alerts.created_total ? alerts.created_total.toLocaleString("en-US") : NOT_MEASURED}
          info="Alerts opened by the alert engine in this process since it started."
          sub={alerts.created_total ? severities.map(([k, v]) => `${v} ${k}`).join(" · ") : "None opened"} />
      </StatStrip>

      <div className="pk-grid">
        <div className="pk-col-7">
          <Panel title="Busiest routes" state={http.top_routes.length ? "ready" : "empty"} emptyText="No API traffic yet"
            info="By request count. Routes are templates, so one row covers every ID.">
            <DataTable caption="Busiest API routes" columns={routeColumns} rows={http.top_routes}
              rowKey={(r) => `${r.method} ${r.route}`} maxHeight={360} />
          </Panel>
        </div>
        <div className="pk-col-5">
          <Panel title="Slowest routes (p95)" state={http.slowest_routes.length ? "ready" : "empty"}
            emptyText="Needs at least 5 requests on a route" info="Routes with at least 5 requests, by p95 duration.">
            <BarList rows={http.slowest_routes.map((r) => ({
              key: `${r.method} ${r.route}`, label: `${r.method} ${r.route}`, title: `${r.method} ${r.route}`,
              value: r.p95_seconds ?? 0,
            }))} valueLabel={(v) => formatSeconds(v)} />
          </Panel>
        </div>

        <div className="pk-col-7">
          <Panel title="Collection by source type" state={collection.by_source_type.length ? "ready" : "empty"}
            emptyText={schedulerHere ? "No collection runs in this process yet" : `Collection runs in the worker, not the ${scope.runtime_mode} role`}>
            <DataTable caption="Collection runs by source type" columns={sourceColumns} rows={collection.by_source_type}
              rowKey={(s) => s.source_type} maxHeight={360} />
          </Panel>
        </div>
        <div className="pk-col-5">
          <Panel title="Where collections fail" state={collection.runs_total ? "ready" : "empty"} emptyText="No collection runs yet"
            info="Failed runs by the stage that failed, and the mean time each stage takes.">
            {stages.length ? (
              <BarList rows={stages.map(([stage, n]) => ({ key: stage, label: stage, value: n, color: "var(--ecc-bad, #d64545)" }))} />
            ) : (
              <p className="pk-muted" style={{ margin: 0, fontSize: 13 }}>No failed runs.</p>
            )}
            <dl style={{ margin: "12px 0 0", display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: 13 }}>
              {Object.entries(collection.phase_mean_seconds).map(([phase, s]) => (
                <div key={phase} style={{ display: "contents" }}>
                  <dt className="pk-muted">{phase}</dt>
                  <dd style={{ margin: 0 }}>{formatSeconds(s)} mean</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <p className="pk-muted" style={{ margin: 0, fontSize: 12 }}>{scope.note}</p>
    </section>
  );
}
