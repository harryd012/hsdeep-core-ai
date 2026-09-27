"use client";

import { useEffect, useState } from "react";
import { getAutomationStatus, getDashboardSummary, AutomationStatus } from "@/lib/api";

export type CollectorStatus = "healthy" | "warning" | "critical" | "unknown";

export interface CollectorsData {
  /** null = the backend did not answer (renders N/A). 0 = a measured zero. */
  total: number | null;
  connected: number | null;
  /** null when the automation status is unreadable - never a 0% claim. */
  successRatePct: number | null;
  runsLast24h: number | null;
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
    total: null,
    connected: null,
    successRatePct: null,
    runsLast24h: null,
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

        // Canonical total: prefer the automation source, fall back to the
        // dashboard summary. It is NOT derived from `connected`: a "connected"
        // count is a different fact from a "configured" total, and inventing one
        // from the other manufactures a number the backend never reported.
        const total = auto?.sources_total ?? summary?.monitoring_sources ?? null;
        const connected = auto?.sources_connected ?? null;
        const successRatePct = auto?.success_rate_pct ?? null;
        const runsLast24h = auto?.runs_last_24h ?? null;

        // Status is only derived from measured inputs. Previously an unreachable
        // automation API produced successRatePct = 0 and therefore a fabricated
        // "critical" verdict for a fleet whose run history was simply unknown.
        let status: CollectorStatus = "unknown";
        if (total !== null && total > 0 && successRatePct !== null) {
          status = successRatePct >= 90 ? "healthy" : successRatePct >= 70 ? "warning" : "critical";
        }

        setData({
          total,
          connected,
          successRatePct,
          runsLast24h,
          status,
          loading: false,
          error: auto === null && summary === null ? "Collector data unavailable" : null,
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
