"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { getCloudOperations } from "@/lib/api";

/**
 * useCloudOperations — fetches the cloud-operations KPI snapshot (6 cards).
 *
 * Reuses the shared React Query cache. The tenant is resolved server-side by
 * the backend via `Depends(get_tenant_id)` — the frontend never sends a
 * tenant_id, so it cannot be spoofed by the client.
 */
export function useCloudOperations() {
  const { isAuthenticated } = useAuth();

  return useQuery({
    queryKey: ["dashboard", "cloud-operations"],
    queryFn: getCloudOperations,
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
