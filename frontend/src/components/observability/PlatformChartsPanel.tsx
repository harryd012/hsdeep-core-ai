"use client";

import { useEffect, useState } from "react";

import { Banner, Panel, Pill, Tabs, ago } from "@/components/ui/PageKit";
import SeriesChart from "@/components/observability/SeriesChart";
import { apiFetch } from "@/lib/api";
import {
  SERIES_THRESHOLDS,
  TIMESERIES_PATH,
  TIMESERIES_RANGES,
  formatUnit,
  prometheusProblem,
  type Timeseries,
  type TimeseriesRange,
} from "@/lib/observability/platformMetrics";

/**
 * Native platform charts drawn in HSDEEP from Prometheus history (every
 * process, 1 h to 7 d), so the platform's latency, errors, collection and
 * scheduler trends live in this app rather than only in Grafana. The backend
 * runs a fixed query catalogue; the browser only chooses the range.
 */

const REFRESH_MS = 60_000;
const GROUPS = [
  { key: "http", title: "API (rate, errors, duration)" },
  { key: "collection", title: "Collection pipeline" },
  { key: "scheduler", title: "Scheduler and database" },
  { key: "alerts", title: "Alerts" },
] as const;

export default function PlatformChartsPanel() {
  const [range, setRange] = useState<TimeseriesRange>("1h");
  const [data, setData] = useState<Timeseries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await apiFetch<Timeseries>(`${TIMESERIES_PATH}?range=${range}`);
        if (cancelled) return;
        setData(result);
        setError(null);
        setNow(Date.now());
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [range]);

  const rangeTabs = <Tabs label="Time range" value={range} onChange={setRange}
    tabs={TIMESERIES_RANGES.map((r) => ({ key: r, label: r }))} />;

  if (!data) {
    return <Panel title="Platform trends" aside={rangeTabs} state={error ? "error" : "loading"}
      errorText={error ? `Could not read platform history: ${error}` : undefined} />;
  }

  const problem = prometheusProblem({ ...data, error: data.error as "timeout" | "unreachable" | "invalid_url" | null, targets: [], jobs: [] });

  return (
    <section aria-label="Platform trends" style={{ display: "grid", gap: 16 }}>
      <div className="pk-toolbar" style={{ justifyContent: "space-between" }}>
        <span className="pk-count">
          From Prometheus, every scraped process · last {data.range} · updated {ago(data.checked_at, now)}
          {error ? " · refresh failed, showing last reading" : ""}
        </span>
        {rangeTabs}
      </div>
      {problem && <Banner tone={data.configured ? "bad" : "warn"}>NOT AVAILABLE: {problem}</Banner>}
      {!problem && GROUPS.map((g) => {
        const series = data.series.filter((s) => s.group === g.key);
        if (!series.length) return null;
        return (
          <div key={g.key} style={{ display: "grid", gap: 8 }}>
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{g.title}</h2>
            <div className="pk-grid">
              {series.map((s) => (
                <div key={s.key} className="pk-col-6">
                  <Panel title={s.label} state={s.error ? "error" : "ready"}
                    errorText={s.error ? `NOT AVAILABLE (${s.error})` : undefined}
                    aside={s.lines.some((l) => l.points.length) ? undefined : <Pill tone="neutral">No data</Pill>}>
                    <SeriesChart lines={s.lines} label={s.label} stepSeconds={data.step_seconds}
                      format={(v) => formatUnit(s.unit, v)} thresholds={SERIES_THRESHOLDS[s.key]} />
                  </Panel>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}
