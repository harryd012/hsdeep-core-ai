"use client";

import { memo, useState } from "react";
import type { TrendChart, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * ChartsPanel — PRESENTATIONAL (Zone 4 "Trends").
 *
 * Semantic purpose: "How is the environment changing over time?"
 * Chart series are built by the dashboard data adapter from REAL Event /
 * CollectorRun rows and passed in via props. Shows ONE chart at a time via
 * tabs (no five-simultaneous-cards wall). Bar rendering unchanged.
 */
const ChartsPanel = memo(function ChartsPanel({
  charts,
  state,
}: {
  charts: TrendChart[];
  state: SectionState;
}) {
  const [activeKey, setActiveKey] = useState(charts[0]?.key ?? "");
  const active = charts.find((c) => c.key === activeKey) ?? charts[0];

  return (
    <div className="hud-panel dashboard-charts">
      <div className="panel-header">
        <div className="panel-title-group"><h2>TRENDS &amp; ANALYTICS</h2></div>
      </div>
      <div className="panel-body">
        {state === "loading" && <div className="dashboard-charts__empty">Loading trend data…</div>}
        {state === "error" && <div className="dashboard-charts__error">Trend data unavailable.</div>}
        {state === "empty" && <div className="dashboard-charts__empty">No operational history yet.</div>}

        {state === "success" && active && (
          <>
            <div className="dashboard-charts__tabs" role="tablist">
              {charts.map((chart) => (
                <button
                  key={chart.key}
                  type="button"
                  role="tab"
                  aria-selected={chart.key === active.key}
                  className={`dashboard-charts__tab ${chart.key === active.key ? "dashboard-charts__tab--active" : ""}`}
                  onClick={() => setActiveKey(chart.key)}
                >
                  {chart.title}
                </button>
              ))}
            </div>
            <div className="dashboard-charts__single">
              <h3>{active.title}</h3>
              {active.data.length === 0 || active.data.every((d) => d.value === 0) ? (
                <div className="dashboard-charts__empty">No data points in this window yet.</div>
              ) : (
                <div className="dashboard-charts__bars">
                  {active.data.map((d, i) => {
                    const max = Math.max(...active.data.map((x) => x.value), 1);
                    const pct = (d.value / max) * 100;
                    return (
                      <div key={i} className="dashboard-charts__bar-col">
                        <div className="dashboard-charts__bar-fill" style={{ height: `${Math.max(pct, 2)}%`, backgroundColor: active.color }} title={`${d.label}: ${d.value}`} />
                        <small>{d.label}</small>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
});

export default ChartsPanel;
