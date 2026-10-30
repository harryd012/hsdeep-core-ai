"use client";

import { memo } from "react";

import type { CommandCenterBrief } from "@/lib/api";

/**
 * CommandCenterBrief — the PHASE 6A / SPRINT P1 command center panel.
 *
 * Renders ONE tenant-scoped read (GET /api/dashboard/command-center-brief):
 * attention items, infrastructure health buckets, per-source collection
 * health, response KPIs and hourly trends. Presentational only — no fetching,
 * no derived maths; the backend already aggregated every number.
 *
 * Honest states, per the platform truth rules:
 *   - loading      -> "Loading command center brief…"
 *   - error        -> "Command center brief unavailable."
 *   - empty item   -> "No items need attention."
 *   - null value   -> "N/A" (a missing rate/sample is NOT a fabricated 0 or 100)
 *   - trend empty  -> "No data in the selected window."
 */
const formatDateTime = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "N/A";

/** Trend bucket timestamp -> local label for the bucket tooltip. */
const formatTrendTs = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

type SectionState =
  | "loading"
  | "error"
  | "empty"
  | "stale"
  | "success";

function Section({
  state,
  title,
  subtitle,
  emptyMessage,
  action,
  children,
}: {
  state: SectionState;
  title: string;
  subtitle?: string;
  emptyMessage: string;
  /** Optional next action (e.g. a Retry button) rendered in the error/empty state. */
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="brief-section">
      <div className="brief-section__header">
        <h3>{title}</h3>
        {subtitle ? <p className="brief-section__subtitle">{subtitle}</p> : null}
      </div>
      <div className="brief-section__body">
        {state === "loading" ? (
          <div className="brief-section__skeleton" aria-label={`Loading ${title}`}>
            <span />
            <span />
            <span />
          </div>
        ) : state === "error" ? (
          <div className="op-health__status op-health__status--error">
            {title.toLowerCase()} unavailable.
            {action}
          </div>
        ) : state === "stale" ? (
          <div className="op-health__status op-health__status--stale">
            {title.toLowerCase()} data is stale.
          </div>
        ) : state === "empty" ? (
          <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
            {emptyMessage}
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

export default memo(function CommandCenterBrief({
  data,
  loading,
  error,
  stale,
  onRetry,
}: {
  data: CommandCenterBrief | undefined;
  loading: boolean;
  error: Error | null;
  stale: boolean;
  /** Manual retry for the error/empty state (never auto-retries endlessly). */
  onRetry?: () => void;
}) {
  if (loading && data === undefined) {
    return (
      <Section
        state="loading"
        title="Command Center Brief"
        emptyMessage="No brief to show yet."
      />
    );
  }
  if (error) {
    return (
      <Section
        state="error"
        title="Command Center Brief"
        emptyMessage="The brief could not be read. Check your connection and try again."
        action={
          onRetry ? (
            <button type="button" className="brief-retry" onClick={onRetry}>
              Retry
            </button>
          ) : undefined
        }
      />
    );
  }
  if (stale && data === undefined) {
    return (
      <Section
        state="stale"
        title="Command Center Brief"
        emptyMessage="The brief is stale — refreshed data should be available soon."
      />
    );
  }

  // A disabled query (signed-out) or one that has never resolved leaves `data`
  // undefined with no error and no loading flag. Render an honest empty state
  // instead of destructuring undefined and crashing the whole dashboard page.
  if (!data) {
    return (
      <Section
        state="empty"
        title="Command Center Brief"
        emptyMessage="No command center brief is available yet."
      />
    );
  }

  const {
    attention,
    infrastructure_health,
    collection_health,
    kpis,
    trends,
    window_hours,
  } = data;
  const { items: attentionItems = [], counts_by_severity: counts } = attention;

  return (
    <section
      className="command-center-brief"
      aria-labelledby="command-center-brief-title"
    >
      <header className="command-center-brief__header">
        <div>
          <h2 id="command-center-brief-title">COMMAND CENTER BRIEF</h2>
          <p className="command-center-brief__subtitle">
            {formatDateTime(data.generated_at)} · window {window_hours}h
            {stale ? " · refreshing…" : ""}
          </p>
        </div>
        {counts && (
          <div className="command-center-brief__counts">
            {Object.entries(counts).map(([severity, count]) => (
              <div key={severity} className="brief-count">
                <span
                  className={`brief-count__dot ${severity}`}
                  style={{ backgroundColor: severity === "critical" ? "var(--red)" : "var(--amber)" }}
                />
                <span>
                  {severity} {count}
                </span>
              </div>
            ))}
          </div>
        )}
      </header>

      {/* Attention */}
      <Section
        state={attentionItems.length === 0 ? "empty" : "success"}
        title="ATTENTION — WHAT NEEDS YOU NOW"
        emptyMessage="No items need attention right now."
      >
        <div className="brief-list">
          {attentionItems.map((item) => (
            <a
              key={item.id}
              className="brief-item"
              href={item.href ?? undefined}
            >
              <div className="brief-item__head">
                <span
                  className="brief-item__severity"
                  style={{ backgroundColor: item.severity === "critical" ? "var(--red)" : "var(--amber)" }}
                >
                  {item.severity}
                </span>
                <span className="brief-item__type">{item.type}</span>
              </div>
              <div className="brief-item__body">
                <span className="brief-item__resource">{item.resource}</span>
                <span className="brief-item__reason">{item.reason}</span>
              </div>
              <div className="brief-item__foot">
                <span className="brief-item__when">
                  since {formatDateTime(item.first_seen)}
                </span>
                <span className="brief-item__when">
                  until {formatDateTime(item.last_seen)}
                </span>
              </div>
            </a>
          ))}
        </div>
      </Section>

      {/* Infrastructure health buckets */}
      <Section
        state={
          infrastructure_health.devices.total === 0 &&
          infrastructure_health.sensors.total === 0 &&
          infrastructure_health.sources.total === 0 &&
          infrastructure_health.collectors.total === 0
            ? "empty"
            : "success"
        }
        title="INFRASTRUCTURE HEALTH"
        subtitle="Buckets are measured from persisted status columns — no second status system."
        emptyMessage="No devices, sensors or sources registered yet."
      >
        <div className="brief-rows">
          <div className="brief-row">
            <span>Devices</span>
            <strong>{infrastructure_health.devices.total}</strong>
            <span className="brief-row__note">
              {infrastructure_health.devices.healthy} healthy ·{" "}
              {infrastructure_health.devices.warning} warning ·{" "}
              {infrastructure_health.devices.critical} critical ·{" "}
              {infrastructure_health.devices.down} down
            </span>
          </div>
          <div className="brief-row">
            <span>Sensors</span>
            <strong>{infrastructure_health.sensors.total}</strong>
            <span className="brief-row__note">
              {infrastructure_health.sensors.healthy} healthy ·{" "}
              {infrastructure_health.sensors.stale} stale
            </span>
          </div>
          <div className="brief-row">
            <span>Sources</span>
            <strong>{infrastructure_health.sources.total}</strong>
            <span className="brief-row__note">
              {infrastructure_health.sources.healthy} healthy ·{" "}
              {infrastructure_health.sources.failed} failed
            </span>
          </div>
          <div className="brief-row">
            <span>Collectors</span>
            <strong>{infrastructure_health.collectors.total}</strong>
            <span className="brief-row__note">
              {infrastructure_health.collectors.healthy} healthy ·{" "}
              {infrastructure_health.collectors.degraded} degraded ·{" "}
              {infrastructure_health.collectors.failed} failed
            </span>
          </div>
        </div>
        <p className="brief-note">
          Sensor freshness window: {infrastructure_health.sensor_stale_after_seconds}s.
        </p>
      </Section>

      {/* Collection health */}
      <Section
        state={
          collection_health.sources.length === 0
            ? "empty"
            : "success"
        }
        title="COLLECTION HEALTH"
        emptyMessage="No active collection sources to report."
      >
        <p className="brief-note">{collection_health.distinction}</p>
        <div className="brief-list">
          {collection_health.sources.map((source) => (
            <div key={source.source_id} className="brief-item">
              <div className="brief-item__head">
                <span className="brief-item__type">
                  {source.collection_status}
                </span>
                <span className="brief-item__resource">{source.name}</span>
              </div>
              <div className="brief-item__body">
                <span className="brief-item__note">
                  provider: {source.provider}
                </span>
                {source.last_collection ? (
                  <span className="brief-item__note">
                    last collection: {formatDateTime(source.last_collection)}
                  </span>
                ) : null}
                {source.error ? (
                  <span className="brief-item__note">
                    error: {source.error}
                  </span>
                ) : null}
                {source.success_rate_pct !== null ? (
                  <span className="brief-item__note">
                    success rate: {source.success_rate_pct}%
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Section>


      {/* Response KPIs */}
      <Section
        state={
          kpis.open_incidents === 0 && kpis.incident_volume_24h === 0
            ? "empty"
            : "success"
        }
        title="RESPONSE KPIs"
        emptyMessage="No incident activity in the window."
      >
        <div className="brief-rows">
          <div className="brief-row">
            <span>Availability</span>
            <strong>
              {kpis.availability_pct === null ? "N/A" : `${kpis.availability_pct}%`}
            </strong>
            <span className="brief-row__note">
              {kpis.open_incidents} open · {kpis.critical_open_incidents} critical
            </span>
          </div>
          <div className="brief-row">
            <span>MTTR</span>
            <strong>
              {kpis.mttr_minutes === null ? "N/A" : `${kpis.mttr_minutes.toFixed(1)}m`}
            </strong>
            <span className="brief-row__note">
              {kpis.resolved_incidents_24h} resolved in the window
            </span>
          </div>
          <div className="brief-row">
            <span>MTTD (ack)</span>
            <strong>
              {kpis.mttd_minutes === null ? "N/A" : `${kpis.mttd_minutes.toFixed(1)}m`}
            </strong>
            <span className="brief-row__note">
              {kpis.incident_volume_24h} incidents in 24h
            </span>
          </div>
        </div>
      </Section>

      {/* Trends */}
      <Section
        state={
          trends.alerts.buckets.length === 0 &&
          trends.incidents.buckets.length === 0 &&
          trends.collection_failures.buckets.length === 0 &&
          trends.devices_down.buckets.length === 0 &&
          trends.stale_telemetry.buckets.length === 0
            ? "empty"
            : "success"
        }
        title={`TRENDS — LAST ${window_hours}H`}
        subtitle="Hourly buckets; empty means no data in the window."
        emptyMessage="No trend data for the selected window."
      >
        <div className="brief-trends">
          {Object.entries(trends).map(([key, series]) => {
            const buckets = series.buckets.slice(-24);
            // Bars scale against THIS series' own peak, so a series that never
            // exceeded 1 is drawn at full height instead of looking flat beside
            // a busy one. A zero count still draws zero height - no bar is
            // invented for an hour that had no data.
            const peak = Math.max(...buckets.map((bucket) => bucket.count), 1);
            return (
              <div key={key} className="brief-trend">
                <div className="brief-trend__header">
                  {key.replace(/_/g, " ")}
                  {series.available ? "" : " — no data"}
                </div>
                <div className="brief-trend__buckets">
                  {buckets.map((bucket) => (
                    <div
                      key={bucket.ts}
                      className="brief-trend__bucket"
                      title={`${formatTrendTs(bucket.ts)} — ${bucket.count}`}
                    >
                      <span
                        className="brief-trend__bar"
                        style={{
                          height: `${Math.round((bucket.count / peak) * 28)}px`,
                        }}
                      />
                      <span className="brief-trend__count">{bucket.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </Section>
    </section>
  );
});
