"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import type { HostsExplorer, HostsExplorerParams } from "@/lib/api";
import { getExplorerHosts } from "@/lib/api";

/**
 * useExplorerHosts — ONE tenant-scoped read (GET /api/explorer/hosts) returning
 * the filtered host page AND the facet counts for every filter dimension.
 *
 * The full parameter object is part of the query key, so every filter/search/
 * page change is a distinct cache entry (no stale-key reuse). `placeholderData:
 * keepPreviousData` keeps the previous table and facet counts on screen while a
 * new filter combination loads, so the sidebar does not flash empty on every
 * click; `isPlaceholderData` tells the caller the visible data is not yet the
 * answer to the current filters.
 *
 * Transport goes through the shared `apiFetch` client (auth, tenant, timeout);
 * `enabled` waits for an authenticated session.
 */
export function useExplorerHosts(params: HostsExplorerParams) {
  const { isAuthenticated } = useAuth();

  return useQuery<HostsExplorer>({
    queryKey: ["explorer", "hosts", params],
    queryFn: () => getExplorerHosts(params),
    enabled: isAuthenticated,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

export type ExplorerHostsQueryState = ReturnType<typeof useExplorerHosts>;
