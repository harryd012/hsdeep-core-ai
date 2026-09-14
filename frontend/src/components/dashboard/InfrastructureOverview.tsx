"use client";

import { memo, useState } from "react";
import { timeAgo } from "@/lib/alert-dedup";
import type { InfrastructureOverviewData, SectionState } from "@/lib/dashboard/dashboardModels";
import InfrastructureHealth from "./InfrastructureHealth";
import InfrastructureMap from "./InfrastructureMap";

/**
 * InfrastructureOverview — Zone 3 (presentational, tabbed).
 *
 * Semantic purpose: "What is the current state of my infrastructure?"
 *
 * Tabs expose only functionality that already exists:
 *   Overview   — InfrastructureHealth + InfrastructureMap + Recent Events
 *   Map        — InfrastructureMap (existing component, reused once)
 *   Devices    — real Device rows (GET /api/infrastructure/devices)
 *   Sensors    — real Sensor rows (GET /api/monitoring/sensors)
 *   Collectors — real collector source health (GET /api/automation/status)
 *
 * Recent Events: real Event rows (GET /api/monitoring/events) — every event
 * carries timestamp, severity, type, resource and message from the backend.
 */

const TABS = ["overview", "map", "devices", "sensors", "collectors"] as const;
type Tab = (typeof TABS)[number];

function statusClass(status: string): string {
  const s = status.toLowerCase();
  if (["up", "online", "operational", "success", "connected", "healthy"].includes(s)) return "infra-table__badge--ok";
  if (["warning", "partial", "degraded"].includes(s)) return "infra-table__badge--warn";
  if (["down", "error", "failed", "offline"].includes(s)) return "infra-table__badge--bad";
  return "infra-table__badge--muted";
}

const InfrastructureOverview = memo(function InfrastructureOverview({
  data,
  state,
}: {
  data: InfrastructureOverviewData;
  state: SectionState;
}) {
  const [tab, setTab] = useState<Tab>("overview");

  return (
    <section className="infra-overview" aria-label="Infrastructure Overview">
      <div className="infra-overview__tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={`infra-overview__tab ${tab === t ? "infra-overview__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "map" ? "TOPOLOGY / MAP" : t.toUpperCase()}
          </button>
        ))}
      </div>

      {state === "loading" && <div className="infra-overview__note">Loading infrastructure data…</div>}
      {state === "error" && <div className="infra-overview__note">Infrastructure data unavailable.</div>}
      {state === "empty" && <div className="infra-overview__note">No infrastructure registered for this tenant yet.</div>}

      {state === "success" && (
        <>
          {tab === "overview" && (
            <div className="infra-overview__grid">
              <InfrastructureHealth rows={data.health.rows} />
              <InfrastructureMap summary={data.summary} />
              <div className="hud-panel recent-events">
                <div className="panel-header"><div className="panel-title-group"><h2>RECENT EVENTS</h2></div></div>
                <div className="panel-body">
                  {data.recentEvents.length === 0 && <div className="recent-events__empty">No recent events.</div>}
                  <ul className="recent-events__list">
                    {data.recentEvents.map((event) => (
                      <li key={event.id} className="recent-events__item">
                        <i className={`recent-events__sev recent-events__sev--${event.severity}`} />
                        <span className="recent-events__type">{event.type}</span>
                        <span className="recent-events__msg" title={event.message}>{event.message}</span>
                        <time className="recent-events__time">{timeAgo(event.timestamp)}</time>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}

          {tab === "map" && <InfrastructureMap summary={data.summary} />}

          {tab === "devices" && (
            <DeviceTable rows={data.devices} />
          )}

          {tab === "sensors" && (
            <SensorTable rows={data.sensors} />
          )}

          {tab === "collectors" && (
            <CollectorTable rows={data.collectors} />
          )}
        </>
      )}
    </section>
  );
});

export default InfrastructureOverview;

/* ---- Tab tables (real backend rows only, honest empty states) ---- */

type DeviceTableProps = { rows: InfrastructureOverviewData["devices"] };
function DeviceTable({ rows }: DeviceTableProps) {
  return (
    <div className="infra-table-wrap">
      <table className="infra-table">
        <thead>
          <tr><th>Device</th><th>Type</th><th>Status</th><th>Last Seen</th></tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>{d.name}</td>
              <td>{d.type}</td>
              <td><span className={`infra-table__badge ${statusClass(d.status)}`}>{d.status}</span></td>
              <td>{d.lastSeen ? timeAgo(d.lastSeen) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="infra-overview__note">No devices registered.</div>}
    </div>
  );
}

type SensorTableProps = { rows: InfrastructureOverviewData["sensors"] };
function SensorTable({ rows }: SensorTableProps) {
  return (
    <div className="infra-table-wrap">
      <table className="infra-table">
        <thead>
          <tr><th>Sensor</th><th>Status</th><th>Last Value</th></tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{s.name}</td>
              <td><span className={`infra-table__badge ${statusClass(s.status)}`}>{s.status}</span></td>
              <td>{s.lastValueAt ? timeAgo(s.lastValueAt) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="infra-overview__note">No sensors registered.</div>}
    </div>
  );
}

type CollectorTableProps = { rows: InfrastructureOverviewData["collectors"] };
function CollectorTable({ rows }: CollectorTableProps) {
  return (
    <div className="infra-table-wrap">
      <table className="infra-table">
        <thead>
          <tr><th>Source</th><th>Status</th><th>Last Run</th><th>Result</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td><span className={`infra-table__badge ${statusClass(c.status)}`}>{c.status}</span></td>
              <td>{c.lastRunAt ? timeAgo(c.lastRunAt) : "—"}</td>
              <td>{c.lastRunStatus ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="infra-overview__note">No collectors configured.</div>}
    </div>
  );
}
