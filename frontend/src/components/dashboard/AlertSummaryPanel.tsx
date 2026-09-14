"use client";

import { memo, useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { timeAgo } from "@/lib/alert-dedup";
import type { AlertSummaryData, SectionState } from "@/lib/dashboard/dashboardModels";
import Panel from "./Panel";

/**
 * AlertSummaryPanel — PRESENTATIONAL (Zone 1).
 *
 * Semantic purpose: "How serious is the current alert situation?"
 * All data arrives via the `data` prop from useDashboardData (single source
 * of truth); this component performs NO fetching and shows honest
 * loading/empty/error states. Markup/classes unchanged from the original.
 */
const AlertSummaryPanel = memo(function AlertSummaryPanel({
  data,
  state,
}: {
  data: AlertSummaryData;
  state: SectionState;
}) {
  const [expandedFingerprint, setExpandedFingerprint] = useState<string | null>(null);

  const { critical, warning, info, total, rawEventCount } = data;

  return (
    <Panel title="ALERT SUMMARY" status={critical > 0 ? "warning" : "healthy"} className="alert-summary">
      {state === "loading" && <div className="alert-summary__empty">Loading alert data…</div>}
      {state === "error" && <div className="alert-summary__empty">Alert data unavailable.</div>}
      {state === "empty" && <div className="alert-summary__empty">No active alerts.</div>}
      {state === "success" && (
        <>
          <div className="alert-summary__donut">
            <div className="alert-summary__total">
              <b>{total}</b>
              <small>TOTAL</small>
            </div>
          </div>
          <ul className="alert-summary__legend">
            <li><i className="alert-summary__dot alert-summary__dot--red" /> {critical} Critical</li>
            <li><i className="alert-summary__dot alert-summary__dot--amber" /> {warning} Warning</li>
            <li><i className="alert-summary__dot alert-summary__dot--blue" /> {info} Info</li>
          </ul>
          {rawEventCount !== null && rawEventCount !== total && (
            <small className="alert-summary__raw-count" style={{ display: "block", fontSize: 12, color: "#8aa6b2", marginTop: 4 }}>
              {rawEventCount} raw events (deduplicated to {total})
            </small>
          )}
          <div className="alert-summary__top">
            <h3>TOP ALERTS</h3>
            <div className="alert-summary__list">
              {data.topAlerts.length === 0 && (
                <div className="alert-summary__empty">No active alerts.</div>
              )}
              {data.topAlerts.map((alert) => (
                <div key={alert.fingerprint} className="alert-summary__row-group">
                  <div
                    className="alert-summary__row"
                    style={{ cursor: "pointer" }}
                    onClick={() => setExpandedFingerprint(
                      expandedFingerprint === alert.fingerprint ? null : alert.fingerprint,
                    )}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setExpandedFingerprint(
                          expandedFingerprint === alert.fingerprint ? null : alert.fingerprint,
                        );
                      }
                    }}
                    aria-expanded={expandedFingerprint === alert.fingerprint}
                  >
                    <i className={`alert-summary__indicator alert-summary__indicator--${alert.severity}`} />
                    <span className="alert-summary__text">
                      {alert.title}
                      {alert.occurrenceCount > 1 && (
                        <span
                          className="alert-summary__occurrence-badge"
                          style={{
                            display: "inline-block",
                            marginLeft: 6,
                            padding: "2px 7px",
                            fontSize: 11,
                            fontWeight: 700,
                            backgroundColor: "var(--amber)",
                            color: "#000",
                            borderRadius: 4,
                          }}
                        >
                          {alert.occurrenceCount}×
                        </span>
                      )}
                    </span>
                    <time className="alert-summary__time">{timeAgo(alert.lastSeen)}</time>
                    <ChevronDown
                      size={14}
                      style={{
                        marginLeft: 8,
                        transition: "transform 0.2s",
                        transform: expandedFingerprint === alert.fingerprint ? "rotate(180deg)" : "rotate(0)",
                      }}
                    />
                  </div>

                  {expandedFingerprint === alert.fingerprint && (
                    <div className="alert-summary__drilldown" style={{ paddingLeft: 20, paddingRight: 10, marginBottom: 4 }}>
                      <small style={{ display: "block", fontSize: 12, color: "#8aa6b2", marginBottom: 4 }}>
                        First seen: {new Date(alert.firstSeen).toLocaleString()}
                      </small>
                      <small style={{ display: "block", fontSize: 12, color: "#8aa6b2" }}>
                        Occurrences: {alert.occurrenceCount} · Last seen: {timeAgo(alert.lastSeen)}
                      </small>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <a className="alert-summary__view-all" href="/alerts">
              View All Alerts <ChevronRight size={14} />
            </a>
          </div>
        </>
      )}
    </Panel>
  );
});

export default AlertSummaryPanel;
