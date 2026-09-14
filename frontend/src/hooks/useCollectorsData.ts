"use client";

import { useEffect, useState } from "react";
import { getAutomationStatus, getDashboardSummary, AutomationStatus } from "@/lib/api";

export type CollectorStatus = "healthy" | "warning" | "critical" | "unknown";

export interface CollectorsData {
  total: number;
  connected: number;
  successRatePct: number;
  runsLast24h: number;
  status: CollectorStatus;
  loading: boolean;
  error: string | null;
}

const REFRESH_MS = 60_000;

/**
 * Canonical source of truth for collector/monitoring-source data.
 *
 * Every widget that displays collector counts or collector success rates MUST
 * read from this hook so that no two widgets can disagree about the same
 * underlying number.
 *
 * Primary source: getAutomationStatus() — provides sources_connected,
 *   success_rate_pct, runs_last_24h.
 * Fallback: getDashboardSummary() — provides monitoring_sources (total count).
 */
export function useCollectorsData(refreshMs = REFRESH_MS): CollectorsData {
  const [data, setData] = useState<CollectorsData>({
    total: 0,
    connected: 0,
    successRatePct: 0,
    runsLast24h: 0,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [auto, summary] = await Promise.all([
          getAutomationStatus().catch(() => null),
          getDashboardSummary().catch(() => null),
        ]);

        if (cancelled) return;

        // Canonical total: prefer automation sources_connected, fall back to dashboard summary
        const connected = auto?.sources_connected ?? 0;
        const total = auto?.sources_total ?? summary?.monitoring_sources ?? connected;
        const successRatePct = auto?.success_rate_pct ?? 0;
        const runsLast24h = auto?.runs_last_24h ?? 0;

        let status: CollectorStatus;
        if (total === 0) {
          status = "unknown";
        } else if (successRatePct >= 90) {
          status = "healthy";
        } else if (successRatePct >= 70) {
          status = "warning";
        } else {
          status = "critical";
        }

        setData({
          total,
          connected,
          successRatePct,
          runsLast24h,
          status,
          loading: false,
          error: null,
        });
      } catch (err) {
        if (!cancelled) {
          setData((prev) => ({
            ...prev,
            loading: false,
            error: err instanceof Error ? err.message : "Failed to load collectors data",
          }));
        }
      }
    }

    load();
    const timer = setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refreshMs]);

  return data;
}
