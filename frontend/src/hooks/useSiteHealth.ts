"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import type { SiteHealth } from "@/lib/api";
import { getSiteHealth } from "@/lib/api";

/**
 * useSiteHealth — ONE tenant-scoped read (GET /api/dashboard/site-health)
 * feeding the Sites honeycomb and the dashboard's site filter list, so the
 * page does not issue a second sites request for the dropdown.
 */
export function useSiteHealth() {
  const { isAuthenticated } = useAuth();

  return useQuery<SiteHealth>({
    queryKey: ["dashboard", "site-health"],
    queryFn: getSiteHealth,
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
