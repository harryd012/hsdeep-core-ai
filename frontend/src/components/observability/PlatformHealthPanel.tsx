"use client";

import { useEffect, useState } from "react";

import { Panel, Pill, StatStrip, StatTile, ago } from "@/components/ui/PageKit";
import { apiFetch } from "@/lib/api";
import {
  HEALTH_TONE,
  PLATFORM_HEALTH_PATH,
  healthCounts,
  type PlatformHealth,
} from "@/lib/observability/platformMetrics";

/**
 * Unified platform health: one status per HSDEEP service, computed server-side
 * from Prometheus queries and the Grafana / Alertmanager health endpoints.
 * Every status shows its reason and, on demand, the rule that produced it.
 * UNKNOWN means "no evidence"; it is never shown as healthy.
 */

const REFRESH_MS = 30_000;

export default function PlatformHealthPanel() {
  const [data, setData] = useState<PlatformHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await apiFetch<PlatformHealth>(PLATFORM_HEALTH_PATH);
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
    return <Panel title="Platform health" state={error ? "error" : "loading"}
      errorText={error ? `Could not read platform health: ${error}` : undefined} />;
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <StatStrip>
        <StatTile label="Overall" value={data.overall} tone={HEALTH_TONE[data.overall]} alarm={data.overall === "CRITICAL"}
          info="The worst status among services with evidence. UNKNOWN services do not count as healthy or unhealthy."
          sub={`Checked ${ago(data.checked_at, now)}`} />
        {healthCounts(data.services).map(({ status, count }) => (
          <StatTile key={status} label={status} value={String(count)} tone={count ? HEALTH_TONE[status] : "neutral"}
            info={`Services currently ${status}.`} />
        ))}
      </StatStrip>

      <Panel title="Services" state="ready"
        info="Statuses, worst first: CRITICAL > WARNING > DEGRADED > HEALTHY. UNKNOWN = no evidence. Open a row for its rule."
        aside={error ? <Pill tone="warn">Refresh failed, showing last reading</Pill> : <Pill tone="info">Refreshes every 30 s</Pill>}>
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 8 }}>
          {data.services.map((s) => (
            <li key={s.key} style={{ border: "1px solid var(--ecc-border)", borderRadius: 8, padding: "8px 12px" }}>
              <button type="button" onClick={() => setOpen(open === s.key ? null : s.key)} aria-expanded={open === s.key}
                style={{ all: "unset", cursor: "pointer", display: "grid", gridTemplateColumns: "110px minmax(0, 1fr) auto",
                         gap: 12, alignItems: "center", width: "100%" }}>
                <Pill tone={HEALTH_TONE[s.status]}>{s.status}</Pill>
                <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                  <strong style={{ fontSize: 13 }}>{s.name}</strong>
                  <span className="pk-cell-sub" style={{ whiteSpace: "normal" }}>{s.reason}</span>
                </span>
                <span className="pk-muted" style={{ fontSize: 12 }}>{open === s.key ? "Hide rule" : "Why?"}</span>
              </button>
              {open === s.key && (
                <ul style={{ margin: "8px 0 0 122px", padding: 0, fontSize: 12, lineHeight: 1.6 }} className="pk-muted">
                  {s.rules.map((r) => <li key={r}>{r}</li>)}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Not available" state={data.not_available.length ? "ready" : "empty"}
        info="Signals that need an exporter this deployment does not run. Shown as NOT AVAILABLE rather than assumed healthy.">
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 6, fontSize: 13 }}>
          {data.not_available.map((n) => (
            <li key={n.key} style={{ display: "grid", gridTemplateColumns: "130px minmax(0, 1fr)", gap: 10 }}>
              <Pill tone="neutral">NOT AVAILABLE</Pill>
              <span style={{ overflowWrap: "anywhere" }}><strong>{n.name}</strong> <span className="pk-muted">needs {n.needs}</span></span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
