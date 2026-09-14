"use client";

import { useQuery } from "@tanstack/react-query";
import { getCommandCenterOverview, CommandCenterOverview } from "@/lib/api";

/**
 * P0-5 — shared React Query AI data layer.
 *
 * This is the single source of truth for the real AI operational payload
 * (`GET /api/command-center/overview`, `getCommandCenterOverview`).
 *
 * Both `/ai-copilot` and the dashboard `CommandCenterTopology` consume this
 * hook so they share ONE cached payload and ONE polling cadence instead of
 * every consumer mounting its own duplicated fetch + `useAutoRefresh` timer.
 *
 * Polling preserved from the original implementation (both consumers polled
 * the same endpoint every 5s). `staleTime: 5_000` lets a consumer that mounts
 * within the poll window reuse the already-fresh cache instead of issuing an
 * immediate duplicate request; `refetchInterval: 5_000` keeps the exact 5s
 * cadence. No other query layers / providers are introduced here.
 */
export function useCommandCenterOverview() {
  return useQuery<CommandCenterOverview>({
    queryKey: ["command-center-overview"],
    queryFn: () => getCommandCenterOverview(),
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}