"use client";

import { useEffect, useState } from "react";

import { DataTable, Panel, Pill, ago, type Column } from "@/components/ui/PageKit";
import { apiFetch } from "@/lib/api";
import {
  PROMETHEUS_TARGETS_PATH,
  formatSeconds,
  prometheusProblem,
  scrapedAt,
  type PrometheusTarget,
  type PrometheusTargets,
} from "@/lib/observability/platformMetrics";

/**
 * Prometheus's own view of every HSDEEP scrape target (api / worker /
 * scheduler and Prometheus itself), read server-side through the
 * superuser-only API. This is what tells you a worker is down, which the
 * in-process summary cannot see. Refreshes every 30 s.
 */

const REFRESH_MS = 30_000;

function columns(now: number): Column<PrometheusTarget>[] {
  return [
    { key: "job", header: "Job", render: (t) => (
        <>
          <span className="pk-cell-main">{t.job}</span>
          <span className="pk-cell-sub">{t.component ? `role ${t.component}` : "—"}</span>
        </>
      ), sortValue: (t) => t.job },
    { key: "instance", header: "Target", render: (t) => <span className="pk-mono">{t.instance}</span>, sortValue: (t) => t.instance },
    { key: "health", header: "State", width: 110, sortValue: (t) => t.health, render: (t) =>
        t.health === "up" ? <Pill tone="good">Up</Pill> : t.health === "down" ? <Pill tone="bad">Down</Pill> : <Pill tone="neutral">Unknown</Pill> },
    { key: "last", header: "Last scrape", width: 160, render: (t) => (
        <>
          <span className="pk-cell-main">{ago(scrapedAt(t.last_scrape), now)}</span>
          <span className="pk-cell-sub">{scrapedAt(t.last_scrape) ? `took ${formatSeconds(t.last_scrape_seconds)}` : "Not scraped yet"}</span>
        </>
      ) },
    { key: "error", header: "Last error", render: (t) => t.last_error
        ? <span className="pk-truncate" title={t.last_error} style={{ maxWidth: 320 }}>{t.last_error}</span>
        : <span className="pk-muted">—</span> },
  ];
}

export default function PrometheusTargetsPanel() {
  const [data, setData] = useState<PrometheusTargets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await apiFetch<PrometheusTargets>(PROMETHEUS_TARGETS_PATH);
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
  }, []);

  if (!data) {
    return (
      <Panel title="Prometheus scrape targets" state={error ? "error" : "loading"}
        errorText={error ? `Could not read Prometheus targets: ${error}` : undefined} />
    );
  }

  const problem = prometheusProblem(data);
  const down = data.targets.filter((t) => t.health !== "up").length;
  const aside = problem
    ? <Pill tone={data.configured ? "bad" : "neutral"}>{data.configured ? "Unreachable" : "Not configured"}</Pill>
    : down
      ? <Pill tone="bad">{down} of {data.targets.length} down</Pill>
      : <Pill tone="good">All {data.targets.length} up</Pill>;

  return (
    <Panel
      title="Prometheus scrape targets"
      info="Prometheus's own view of each HSDEEP process it scrapes, across every role. Read server-side; the Prometheus address never reaches the browser."
      aside={aside}
      state={problem ? "empty" : data.targets.length ? "ready" : "empty"}
      emptyText={problem ?? "Prometheus has no active targets"}
    >
      <DataTable caption="Prometheus scrape targets" columns={columns(now)} rows={data.targets}
        rowKey={(t) => `${t.job} ${t.instance}`} maxHeight={320} />
      <p className="pk-muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
        {error ? "Refresh failed, showing last reading. " : ""}Checked {ago(data.checked_at, now)}.
      </p>
    </Panel>
  );
}
