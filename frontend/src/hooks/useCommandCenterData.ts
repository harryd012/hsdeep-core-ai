"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { getAiCtoStatus, getTopology, listAssetDevices, listIncidents, getTenantId } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export function useDashboardDependencies() {
  const { isAuthenticated } = useAuth();
  const tenant = getTenantId();
  return useQuery({ queryKey: ["dashboard", tenant, "dependencies"], queryFn: getAiCtoStatus,
    enabled: isAuthenticated, staleTime: 30_000, refetchInterval: 60_000, retry: false });
}

export function useCommandCenterData() {
  const { isAuthenticated } = useAuth();
  const tenant = getTenantId();
  const options = { enabled: isAuthenticated, staleTime: 30_000, refetchInterval: 60_000, retry: false };
  const dependencies = useDashboardDependencies();
  const topology = useQuery({ ...options, queryKey: ["dashboard", tenant, "topology"], queryFn: () => getTopology(tenant) });
  const assets = useQuery({ ...options, queryKey: ["dashboard", tenant, "assets"], queryFn: () => listAssetDevices(tenant) });
  const incidents = useQuery({ ...options, queryKey: ["dashboard", tenant, "incidents"], queryFn: () => listIncidents({ tenantId: tenant, limit: 100 }) });
  const client = useQueryClient();
  const lock = useRef(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  async function refresh() {
    if (lock.current) return;
    lock.current = true;
    setRefreshing(true);
    try {
      await client.refetchQueries({ type: "active", predicate: q => ["dashboard", "command-center-overview"].includes(String(q.queryKey[0])) });
      setRefreshedAt(Date.now());
    } finally {
      setRefreshing(false);
      // Prevent repeated clicks without reloading the browser or session.
      setTimeout(() => { lock.current = false; }, 2000);
    }
  }
  return { dependencies, topology, assets, incidents, refresh, refreshing, refreshedAt };
}
export type CommandCenterData = ReturnType<typeof useCommandCenterData>;
