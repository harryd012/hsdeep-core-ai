"use client";

import { useQuery } from "@tanstack/react-query";
import { getSystemHealth, type SystemHealth } from "@/lib/api";

/**
 * Shared system-health hook. Polls GET /health (the authoritative platform
 * state) every 60s and shares one cache entry, so the TopBar, masthead and any
 * other status surface never fetch (or disagree about) health separately.
 */
export function useSystemHealth() {
  return useQuery<SystemHealth>({
    queryKey: ["system-health"],
    queryFn: () => getSystemHealth(),
    refetchInterval: 60_000,
    staleTime: 15_000,
  });
}
