"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import type { CommandCenterBrief } from "@/lib/api";
import { getDashboardCommandCenterBrief } from "@/lib/api";

/**
 * useCommandCenterBrief — PHASE 6A / SPRINT P1.
 *
 * ONE tenant-scoped read (GET /api/dashboard/command-center-brief) for the
 * command center brief: attention items, infrastructure health buckets,
 * per-source collection health, response KPIs and hourly trends. Nothing here
 * fetches more than that single read, and nothing overlaps the
 * OperationalIntelligence hook's cache (different query key).
 *
 * Transport goes through the shared `apiFetch` client via
 * `getDashboardCommandCenterBrief` — a raw fetch here would silently drop
 * auth and the panel would never load.
 *
 * Honest states: loading, error and null (no data) are all surfaced to the
 * caller; each section renders its own empty state ("no data" / "not
 * configured" / "unknown"), never a fabricated number.
 */
export function useCommandCenterBrief() {
  const { isAuthenticated } = useAuth();

  return useQuery<CommandCenterBrief>({
    queryKey: ["dashboard", "command-center-brief"],
    queryFn: getDashboardCommandCenterBrief,
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

export type CommandCenterBriefQueryState = ReturnType<typeof useCommandCenterBrief>;
