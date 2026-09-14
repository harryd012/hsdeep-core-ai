"use client";

import { memo } from "react";
import { timeAgo } from "@/lib/alert-dedup";
import type { AlertSummaryData, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * RecentAlertTimeline — PRESENTATIONAL (Zone 1, below the KPI grid).
 *
 * Fills the space under the Operational Health KPI tiles with the most
 * recent REAL critical/warning open alerts. Data comes from the same
 * `listAlerts({ status: "open" })` payload that powers AlertSummaryPanel
 * (already polled every 15s) — no new fetch, no fabricated content.
 *
 * Only re-sorted by recency here; every value shown is real.
 */
const RecentAlertTimeline = memo(function RecentAlertTimeline({
  data,
  state,
}: {
  data: AlertSummaryData;
  state: SectionState;
}) {
  const recent = [...data.topAlerts]
    .filter((a) => a.severity === "critical" || a.severity === "warning" || a.severity === "info")
    .sort((a, b) => new Date(b.lastSeen).getTime() - new Date(a.lastSeen).getTime())
    .slice(0, 5);

  return (
    <section className="recent-alerts" aria-label="Most recent alerts">
      <header className="recent-alerts__header">RECENT ALERT EVENTS</header>

      {state === "loading" && <p className="recent-alerts__empty">Loading recent alerts…</p>}
      {state === "error" && <p className="recent-alerts__empty">Recent alert data unavailable.</p>}
      {state !== "loading" && state !== "error" && recent.length === 0 && (
        <p className="recent-alerts__empty">No recent alert events.</p>
      )}

      {recent.length > 0 && (
        <ul className="recent-alerts__list">
          {recent.map((a) => (
            <li
              key={a.fingerprint}
              className={`recent-alerts__row recent-alerts__row--${a.severity}`}
              title={`${a.title} — ${a.occurrenceCount} occurrence(s)`}
            >
              <i className={`recent-alerts__indicator recent-alerts__indicator--${a.severity}`} aria-hidden="true" />
              <span className="recent-alerts__title">{a.title}</span>
              {a.occurrenceCount > 1 && (
                <span className="recent-alerts__count">{a.occurrenceCount}×</span>
              )}
              <time className="recent-alerts__time">{timeAgo(a.lastSeen)}</time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
});

export default RecentAlertTimeline;