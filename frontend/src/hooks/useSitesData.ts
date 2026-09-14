"use client";

import { useEffect, useState } from "react";
import { getNetworkStatus, getDashboardSummary, NetworkStatus, DashboardSummary } from "@/lib/api";

export type SiteStatus = "healthy" | "warning" | "critical" | "unknown";

export interface SitesData {
  total: number;
  online: number;
  healthPct: number;
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
    total: 0,
    online: 0,
    healthPct: 0,
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

        // Canonical total: prefer network status, fall back to dashboard summary
        const total = net?.sites_total ?? summary?.sites ?? 0;
        const online = net?.sites_online ?? 0;
        const healthPct = total > 0 ? Math.round((online / total) * 100) : 0;

        let status: SiteStatus;
        if (total === 0) {
          status = "unknown";
        } else if (healthPct >= 90) {
          status = "healthy";
        } else if (healthPct >= 70) {
          status = "warning";
        } else {
          status = "critical";
        }

        setData({
          total,
          online,
          healthPct,
          status,
          loading: false,
          error: null,
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
