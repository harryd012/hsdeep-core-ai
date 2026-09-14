"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  getUsageSummary,
  getUsageEvents,
  UsageSummary,
  UsageBucket,
} from "@/lib/usage-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ConsumptionTrackerProps {
  tenantId?: string;
  days?: number;
  refreshIntervalMs?: number;
}

interface TierLimit {
  label: string;
  eventType: string;
  limit: number;
  color: string;
}

const DEFAULT_TIER_LIMITS: TierLimit[] = [
  { label: "API Calls", eventType: "api.call", limit: 100_000, color: "#3b82f6" },
  { label: "Alerts Ingested", eventType: "alert.ingested", limit: 50_000, color: "#ef4444" },
  { label: "Metrics Ingested", eventType: "metric.ingested", limit: 1_000_000, color: "#8b5cf6" },
  { label: "Collector Runs", eventType: "collector.run", limit: 10_000, color: "#10b981" },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ConsumptionTracker({
  tenantId,
  days = 30,
  refreshIntervalMs = 60_000,
}: ConsumptionTrackerProps) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [events, setEvents] = useState<UsageBucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch on mount and poll at the given interval
  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const [s, e] = await Promise.all([
          getUsageSummary({ tenantId, days }),
          getUsageEvents({ tenantId, days }),
        ]);
        if (active) {
          setSummary(s);
          setEvents(e.events);
          setError(null);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load usage data");
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    load();
    const interval = setInterval(load, refreshIntervalMs);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [tenantId, days, refreshIntervalMs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  const retry = () => {
    setLoading(true);
    setError(null);
    // Re-run the effect by forcing a re-mount via key change is not
    // practical here, so we inline the load logic.
    (async () => {
      try {
        const [s, e] = await Promise.all([
          getUsageSummary({ tenantId, days }),
          getUsageEvents({ tenantId, days }),
        ]);
        setSummary(s);
        setEvents(e.events);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load usage data");
      } finally {
        setLoading(false);
      }
    })();
  };

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-red-700 text-sm">{error}</p>
        <button
          onClick={retry}
          className="mt-2 text-sm text-red-600 hover:text-red-800 underline"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!summary) return null;

  // Compute threshold breaches
  const breaches = DEFAULT_TIER_LIMITS.map((tier) => {
    const current = getEventCount(summary, tier.eventType);
    const pct = tier.limit > 0 ? Math.round((current / tier.limit) * 100) : 0;
    return { ...tier, current, pct };
  });

  // Aggregate events by day for chart
  const dailyTotals = aggregateByDay(events);

  return (
    <div className="space-y-6">
      {/* Executive Summary */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">
          Consumption Overview
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          {summary.period_start.split("T")[0]} &ndash;{" "}
          {summary.period_end.split("T")[0]}
        </p>

        {/* KPI Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <KPICard
            label="API Calls"
            value={formatNumber(summary.total_api_calls)}
            subtitle={`${summary.total_api_calls > 0 ? "Total" : "No data"}`}
            color="blue"
          />
          <KPICard
            label="Alerts"
            value={formatNumber(summary.total_alerts_ingested)}
            subtitle={`${summary.total_alerts_ingested > 0 ? "Ingested" : "No data"}`}
            color="red"
          />
          <KPICard
            label="Metrics"
            value={formatNumber(summary.total_metrics_ingested)}
            subtitle={`${summary.total_metrics_ingested > 0 ? "Records" : "No data"}`}
            color="purple"
          />
          <KPICard
            label="Collector Runs"
            value={formatNumber(summary.total_collector_runs)}
            subtitle={`${summary.total_collector_runs > 0 ? "Executions" : "No data"}`}
            color="green"
          />
        </div>
      </div>

      {/* Tier Limit Progress Bars */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h3 className="text-md font-semibold text-gray-900 mb-4">
          Subscription Tier Limits
        </h3>
        <div className="space-y-4">
          {breaches.map((tier) => (
            <TierProgressBar key={tier.eventType} {...tier} />
          ))}
        </div>
      </div>

      {/* Daily Usage Chart */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h3 className="text-md font-semibold text-gray-900 mb-4">
          Daily Usage Trend
        </h3>
        <div className="h-48 flex items-end space-x-1">
          {dailyTotals.length > 0 ? (
            dailyTotals.map((day, i) => (
              <Bar
                key={i}
                height={day.count}
                max={Math.max(...dailyTotals.map((d) => d.count), 1)}
                label={day.label}
              />
            ))
          ) : (
            <p className="text-gray-400 text-sm">No usage data available</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function KPICard({
  label,
  value,
  subtitle,
  color,
}: {
  label: string;
  value: string;
  subtitle: string;
  color: "blue" | "red" | "purple" | "green";
}) {
  const colorMap: Record<string, string> = {
    blue: "text-blue-600",
    red: "text-red-600",
    purple: "text-purple-600",
    green: "text-green-600",
  };
  return (
    <div className="bg-gray-50 rounded-lg p-4">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        {label}
      </p>
      <p className={`text-2xl font-bold mt-1 ${colorMap[color]}`}>{value}</p>
      <p className="text-xs text-gray-400 mt-1">{subtitle}</p>
    </div>
  );
}

function TierProgressBar({
  label,
  current,
  limit,
  pct,
  color,
}: TierLimit & { current: number; pct: number }) {
  const breached = current >= limit;
  const barColor = breached ? "#ef4444" : color;

  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="font-medium text-gray-700">{label}</span>
        <span className={`${breached ? "text-red-600 font-semibold" : "text-gray-500"}`}>
          {formatNumber(current)} / {formatNumber(limit)}
          {breached && " (EXCEEDED)"}
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2.5">
        <div
          className="h-2.5 rounded-full transition-all duration-500"
          style={{
            width: `${Math.min(pct, 100)}%`,
            backgroundColor: barColor,
          }}
        />
      </div>
    </div>
  );
}

function Bar({
  height,
  max,
  label,
}: {
  height: number;
  max: number;
  label: string;
}) {
  const pct = max > 0 ? (height / max) * 100 : 0;
  return (
    <div className="flex-1 flex flex-col items-center justify-end h-full">
      <div
        className="w-full bg-blue-500 rounded-t"
        style={{ height: `${Math.max(pct, 1)}%`, minHeight: "2px" }}
        title={`${label}: ${height}`}
      />
      <span className="text-[10px] text-gray-400 mt-1 truncate w-full text-center">
        {label}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function getEventCount(summary: UsageSummary, eventType: string): number {
  const map: Record<string, number> = {
    "api.call": summary.total_api_calls,
    "alert.ingested": summary.total_alerts_ingested,
    "metric.ingested": summary.total_metrics_ingested,
    "collector.run": summary.total_collector_runs,
    "sop.executed": summary.total_sop_executions,
    "workflow.executed": summary.total_workflow_executions,
    "auth.login": summary.total_auth_logins,
  };
  return map[eventType] ?? 0;
}

function aggregateByDay(
  events: UsageBucket[],
): { label: string; count: number }[] {
  const days: Record<string, number> = {};
  for (const e of events) {
    const day = e.bucket_start.split("T")[0];
    days[day] = (days[day] ?? 0) + e.count;
  }
  return Object.entries(days)
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-30)
    .map(([label, count]) => ({ label: label.slice(5), count }));
}