"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getNocEvents,
  getNocEvent,
  getNocEventSummary,
  getNocEventStatistics,
  getSocAnalysts,
  getSocAnalystSummary,
  getSocAnalystDetail,
  createNocEvent,
  updateNocEvent,
  type NocEventListParams,
  type NocEventPagination,
  type NocEvent,
  type NocEventSummary,
  type NocEventStatistics,
  type SocAnalyst,
  type SocAnalystSummary,
  type SocAnalystDetail,
} from "@/lib/noc";

/**
 * NOC Event list — paginated, filterable.
 */
export function useNocEvents(params: NocEventListParams = {}, limit = 50, offset = 0) {
  return useQuery<NocEventPagination>({
    queryKey: ["noc-events", params, limit, offset],
    queryFn: () => getNocEvents({ ...params, limit, offset }),
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

/**
 * Single NOC event detail.
 */
export function useNocEvent(eventId: string | null) {
  return useQuery<NocEvent>({
    queryKey: ["noc-event", eventId],
    queryFn: () => getNocEvent(eventId!),
    enabled: !!eventId,
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

/**
 * NOC event summary (severity/status/category counts).
 */
export function useNocEventSummary() {
  return useQuery<NocEventSummary>({
    queryKey: ["noc-events-summary"],
    queryFn: () => getNocEventSummary(),
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

/**
 * NOC event statistics (MTTR, top sources, etc.).
 */
export function useNocEventStatistics() {
  return useQuery<NocEventStatistics>({
    queryKey: ["noc-events-statistics"],
    queryFn: () => getNocEventStatistics(),
    refetchInterval: 15_000,
    staleTime: 15_000,
    gcTime: 5 * 60 * 1000,
  });
}

// ---------------------------------------------------------------------------
// SOC Analysts
// ---------------------------------------------------------------------------

/**
 * SOC analyst roster with live NOC assignment counts.
 */
export function useSocAnalysts(params: { availability?: string; level?: string } = {}) {
  return useQuery<SocAnalyst[]>({
    queryKey: ["soc-analysts", params],
    queryFn: () => getSocAnalysts(params),
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

/**
 * SOC analyst summary (aggregate counts for roster header).
 */
export function useSocAnalystSummary() {
  return useQuery<SocAnalystSummary>({
    queryKey: ["soc-analysts-summary"],
    queryFn: () => getSocAnalystSummary(),
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

/**
 * Single SOC analyst detail with assigned NOC events.
 */
export function useSocAnalystDetail(analystId: string | null) {
  return useQuery<SocAnalystDetail>({
    queryKey: ["soc-analyst", analystId],
    queryFn: () => getSocAnalystDetail(analystId!),
    enabled: !!analystId,
    refetchInterval: 5_000,
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Create a new NOC event.
 */
export function useCreateNocEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Parameters<typeof createNocEvent>[0]) => createNocEvent(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["noc-events"] });
      queryClient.invalidateQueries({ queryKey: ["noc-events-summary"] });
      queryClient.invalidateQueries({ queryKey: ["noc-events-statistics"] });
    },
  });
}

/**
 * Update an existing NOC event.
 */
export function useUpdateNocEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { eventId: string; payload: Parameters<typeof updateNocEvent>[1] }) =>
      updateNocEvent(params.eventId, params.payload),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["noc-events"] });
      queryClient.invalidateQueries({ queryKey: ["noc-event", variables.eventId] });
      queryClient.invalidateQueries({ queryKey: ["noc-events-summary"] });
      queryClient.invalidateQueries({ queryKey: ["noc-events-statistics"] });
    },
  });
}
