"use client";

import { useEffect, useState } from "react";

import { DataTable, Panel, Pill, ago, type Column } from "@/components/ui/PageKit";
import { apiFetch } from "@/lib/api";
import {
  ALERT_RULES_PATH,
  prometheusProblem,
  type AlertRule,
  type AlertRules,
} from "@/lib/observability/platformMetrics";

/**
 * The platform alert rules (deployment/observability/prometheus/rules) and
 * their live state from Prometheus. Prometheus deduplicates: one alert per
 * distinct label set. No Alertmanager is deployed, so firing alerts are shown
 * here and in Prometheus but are not sent anywhere.
 */

const REFRESH_MS = 30_000;
const STATE_TONE = { firing: "bad", pending: "warn", inactive: "good" } as const;
const SEVERITY_TONE: Record<string, "bad" | "warn" | "neutral"> = { critical: "bad", warning: "warn" };

function columns(now: number): Column<AlertRule>[] {
  return [
    { key: "name", header: "Alert", render: (r) => (
        <>
          <span className="pk-cell-main pk-mono">{r.name}</span>
          <span className="pk-cell-sub" style={{ whiteSpace: "normal" }}>{r.summary ?? r.description ?? "—"}</span>
        </>
      ), sortValue: (r) => r.name },
    { key: "severity", header: "Severity", width: 110, sortValue: (r) => r.severity,
      render: (r) => <Pill tone={SEVERITY_TONE[r.severity ?? ""] ?? "neutral"}>{r.severity ?? "—"}</Pill> },
    { key: "state", header: "State", width: 110, sortValue: (r) => r.state,
      render: (r) => <Pill tone={STATE_TONE[r.state]}>{r.state}</Pill> },
    { key: "active", header: "Active since", width: 150, render: (r) => {
        const first = r.alerts.map((a) => a.active_at).filter(Boolean).sort()[0] ?? null;
        return first ? (
          <>
            <span className="pk-cell-main">{ago(first, now)}</span>
            <span className="pk-cell-sub">{r.alerts.length} instance{r.alerts.length === 1 ? "" : "s"}</span>
          </>
        ) : <span className="pk-muted">—</span>;
      } },
    { key: "labels", header: "Affected", render: (r) => r.alerts.length
        ? <span className="pk-mono" style={{ fontSize: 11, overflowWrap: "anywhere" }}>
            {r.alerts.map((a) => Object.entries(a.labels).filter(([k]) => !["severity", "team"].includes(k))
              .map(([k, v]) => `${k}=${v}`).join(", ")).join(" | ")}
          </span>
        : <span className="pk-muted">—</span> },
    { key: "group", header: "Group", width: 140, render: (r) => <span className="pk-muted">{r.group}</span>, sortValue: (r) => r.group },
  ];
}

export default function AlertRulesPanel() {
  const [data, setData] = useState<AlertRules | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await apiFetch<AlertRules>(ALERT_RULES_PATH);
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
    return <Panel title="Alert rules" state={error ? "error" : "loading"}
      errorText={error ? `Could not read alert rules: ${error}` : undefined} />;
  }

  const problem = prometheusProblem({ ...data, targets: [], jobs: [] });
  const aside = problem ? <Pill tone={data.configured ? "bad" : "neutral"}>{data.configured ? "Unreachable" : "Not configured"}</Pill>
    : data.firing ? <Pill tone="bad">{data.firing} firing</Pill>
    : data.pending ? <Pill tone="warn">{data.pending} pending</Pill>
    : <Pill tone="good">None firing</Pill>;

  return (
    <Panel title="Alert rules" aside={aside}
      info="Rules from deployment/observability/prometheus/rules, evaluated by Prometheus. Pending = condition true but its `for:` window has not passed."
      state={problem ? "empty" : data.rules.length ? "ready" : "empty"}
      emptyText={problem ?? "Prometheus has no alerting rules loaded"}>
      <DataTable caption="Alert rules" columns={columns(now)} rows={data.rules} rowKey={(r) => `${r.group}/${r.name}`} maxHeight={520} />
      <p className="pk-muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
        No Alertmanager is deployed, so firing alerts are not sent anywhere yet. History beyond the current state lives in
        Prometheus (the ALERTS series). {error ? "Refresh failed, showing last reading. " : ""}Checked {ago(data.checked_at, now)}.
      </p>
    </Panel>
  );
}
