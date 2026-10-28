"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import type { IncidentAnalytics } from "@/lib/api";
import { getIncidentAnalytics } from "@/lib/api";

/**
 * useIncidentAnalytics — ONE tenant-scoped read
 * (GET /api/dashboard/incident-analytics) feeding the Executive Command Center
 * incident charts: trend, severity mix, aging, heatmap, resolution-time
 * distribution and top affected sites.
 *
 * The window (`days`) is part of the query key, so changing the range is a
 * distinct cache entry rather than a stale-key reuse, and switching back is
 * instant. Transport goes through the shared `apiFetch` client (auth + tenant
 * handling + timeout); `enabled` waits for an authenticated session so a
 * first render never fires an unauthenticated request.
 *
 * Honest states: loading, error and success are all surfaced to the caller.
 * Nothing is defaulted to zero here — an empty estate is a real empty payload.
 */
export function useIncidentAnalytics(
  days: number,
  filters: { siteId?: string | null; severity?: string | null } = {},
) {
  const { isAuthenticated } = useAuth();
  const siteId = filters.siteId ?? null;
  const severity = filters.severity ?? null;

  return useQuery<IncidentAnalytics>({
    // ECC-1: site/severity are part of the key, so each filter combination is
    // its own cache entry (the unfiltered key stays byte-identical).
    queryKey:
      siteId || severity
        ? ["dashboard", "incident-analytics", days, siteId, severity]
        : ["dashboard", "incident-analytics", days],
    queryFn: () => getIncidentAnalytics(days, { siteId, severity }),
    placeholderData: (previous) => previous,
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

export type IncidentAnalyticsQueryState = ReturnType<typeof useIncidentAnalytics>;
