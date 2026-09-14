"use client";

import { memo, useMemo, useState } from "react";
import { Activity, AlertTriangle, PlayCircle, Server } from "lucide-react";
import { timeAgo } from "@/lib/alert-dedup";
import type { ActivityItem, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * ActivityFeed — PRESENTATIONAL (Zone 4 "Activity").
 *
 * Semantic purpose: "What recently happened in the system?"
 * Items are built by the dashboard data adapter from real Event rows,
 * CollectorRun rows and Device updates (same payloads as every other
 * dashboard section). No fetching, no fabricated activity.
 *
 * Filtering is client-side over the already-fetched data — no extra requests.
 */

const TYPE_FILTERS = [
  { key: "all", label: "ALL" },
  { key: "alert", label: "ALERTS" },
  { key: "incident", label: "INCIDENTS" },
  { key: "device", label: "DEVICES" },
  { key: "collector", label: "COLLECTORS" },
] as const;

type FilterKey = (typeof TYPE_FILTERS)[number]["key"];

function typeIcon(type: ActivityItem["type"]) {
  switch (type) {
    case "alert": return <AlertTriangle size={13} />;
    case "incident": return <AlertTriangle size={13} />;
    case "device": return <Server size={13} />;
    case "collector": return <PlayCircle size={13} />;
    default: return <Activity size={13} />;
  }
}

function typeColor(type: ActivityItem["type"], severity?: string): string {
  if (severity === "critical" || severity === "error") return "var(--red)";
  if (severity === "warning") return "var(--amber)";
  switch (type) {
    case "alert": return "var(--amber)";
    case "incident": return "var(--red)";
    case "device": return "var(--cyan)";
    case "collector": return "var(--green)";
    default: return "var(--muted)";
  }
}

const ActivityFeed = memo(function ActivityFeed({
  items,
  state,
}: {
  items: ActivityItem[];
  state: SectionState;
}) {
  const [filter, setFilter] = useState<FilterKey>("all");

  const filtered = useMemo(() => {
    if (filter === "all") return items;
    return items.filter((i) => i.type === filter);
  }, [items, filter]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length };
    for (const item of items) c[item.type] = (c[item.type] ?? 0) + 1;
    return c;
  }, [items]);

  return (
    <div className="hud-panel activity-feed">
      <div className="panel-header">
        <div className="panel-title-group"><h2>ACTIVITY FEED</h2></div>
        <div className="panel-indicators">
          <span className="panel-status-dot" style={{ backgroundColor: "var(--green)" }} aria-hidden="true" />
        </div>
      </div>
      {state === "success" && items.length > 1 && (
        <div className="activity-feed__filters">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`activity-feed__filter ${filter === f.key ? "activity-feed__filter--active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              {counts[f.key] !== undefined && (
                <span className="activity-feed__filter-count">{counts[f.key]}</span>
              )}
            </button>
          ))}
        </div>
      )}
      <div className="panel-body">
        {state === "loading" && <div className="activity-feed__empty">Loading activity…</div>}
        {state === "error" && <div className="activity-feed__error">Activity data unavailable.</div>}
        {state === "empty" && <div className="activity-feed__empty">No recent activity.</div>}
        {state === "success" && filtered.length === 0 && (
          <div className="activity-feed__empty">No {filter !== "all" ? filter : ""} activity.</div>
        )}
        {state === "success" && filtered.length > 0 && (
          <ul className="activity-feed__list">
            {filtered.map((item) => (
              <li key={item.id} className="activity-feed__item">
                <span className="activity-feed__icon" style={{ color: typeColor(item.type, item.severity) }}>
                  {typeIcon(item.type)}
                </span>
                <span className="activity-feed__content">
                  <span className="activity-feed__title">{item.title}</span>
                  {item.resource && <span className="activity-feed__resource">{item.resource}</span>}
                  <span className="activity-feed__desc" title={item.description}>{item.description}</span>
                </span>
                <time className="activity-feed__time">{timeAgo(item.timestamp)}</time>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
});

export default ActivityFeed;
