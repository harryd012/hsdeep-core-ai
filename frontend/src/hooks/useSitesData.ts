"use client";

import { useEffect, useState } from "react";
import { getNetworkStatus, getDashboardSummary, NetworkStatus, DashboardSummary } from "@/lib/api";

export type SiteStatus = "healthy" | "warning" | "critical" | "unknown";

export interface SitesData {
  /** null = the backend did not answer (renders N/A). 0 = a measured zero. */
  total: number | null;
  online: number | null;
  /** null when either part of the ratio is unavailable - never a 0% claim. */
  healthPct: number | null;
  status: SiteStatus;
  loading: boolean;
  error: string | null;
}

const REFRESH_MS = 60_000;

/**
 * Canonical source of truth for site-level data.
 *
 * Every widget that displays site counts or site health MUST read from this
 * hook so that no two widgets can disagree about the same underlying number.
 *
 * Primary source: getNetworkStatus() — provides sites_total + sites_online.
 * Fallback: getDashboardSummary() — provides sites (total count only).
 */
export function useSitesData(refreshMs = REFRESH_MS): SitesData {
  const [data, setData] = useState<SitesData>({
    total: null,
    online: null,
    healthPct: null,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [net, summary] = await Promise.all([
          getNetworkStatus().catch(() => null),
          getDashboardSummary().catch(() => null),
        ]);

        if (cancelled) return;

        // Canonical total: prefer network status, fall back to dashboard summary.
        // A source that did not answer yields null, never 0: "no data" and
        // "zero sites" are different operational facts.
        const total = net?.sites_total ?? summary?.sites ?? null;
        const online = net?.sites_online ?? null;
        // A percentage needs BOTH parts; without them it is unavailable, not 0%.
        const healthPct =
          total !== null && total > 0 && online !== null
            ? Math.round((online / total) * 100)
            : null;

        let status: SiteStatus = "unknown";
        if (total !== null && total > 0 && healthPct !== null) {
          status = healthPct >= 90 ? "healthy" : healthPct >= 70 ? "warning" : "critical";
        }

        setData({
          total,
          online,
          healthPct,
          status,
          loading: false,
          error: net === null && summary === null ? "Site data unavailable" : null,
        });
      } catch (err) {
        if (!cancelled) {
          setData((prev) => ({
            ...prev,
            loading: false,
            error: err instanceof Error ? err.message : "Failed to load sites data",
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
