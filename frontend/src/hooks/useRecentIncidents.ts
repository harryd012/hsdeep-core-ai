"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import type { Incident, IncidentSeverity } from "@/lib/api";
import { getTenantId, listIncidents } from "@/lib/api";

/**
 * useRecentIncidents — the Command Center's "Recent Incidents" table, read
 * from the existing GET /api/incidents list (no new endpoint). Honours the
 * dashboard's site / severity filters.
 */
export function useRecentIncidents(
  filters: { siteId?: string | null; severity?: IncidentSeverity | null },
  limit = 8,
) {
  const { isAuthenticated } = useAuth();
  const siteId = filters.siteId ?? null;
  const severity = filters.severity ?? null;

  return useQuery<Incident[]>({
    queryKey: ["dashboard", "recent-incidents", siteId, severity, limit],
    queryFn: () =>
      listIncidents({
        tenantId: getTenantId(),
        siteId: siteId ?? undefined,
        severity: severity ?? undefined,
        limit,
      }),
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    placeholderData: (previous) => previous,
    retry: false,
  });
}
