"use client";

/**
 * useSocData — single React Query composition for the SOC dashboard.
 *
 * Reuses the SAME query keys as useDashboardData (["dashboard", …]) for
 * identical payloads so navigating between /dashboard views shares the cache
 * instead of issuing duplicate requests. Cadences mirror the existing
 * dashboard panels: alerts 15s, incidents 30s, events/devices 60s.
 * SOC-specific categories (trend, vectors, MITRE, map) come from the typed
 * fallback adapter until backend endpoints exist — see socAdapter.ts.
 */
import { useQuery } from "@tanstack/react-query";
import {
  getEvents,
  getDevices,
  listAlerts,
  listIncidents,
} from "@/lib/api";
import { buildSocData } from "@/lib/soc/socAdapter";
import type { SocData } from "@/lib/soc/socModels";

export function useSocData(): { data: SocData; isLoading: boolean; hasError: boolean } {
  const alerts = useQuery({
    queryKey: ["dashboard", "alerts-open"],
    queryFn: () => listAlerts({ status: "open", limit: 100 }),
    refetchInterval: 15_000,
  });
  const incidents = useQuery({
    queryKey: ["soc", "incidents-open"],
    queryFn: () => listIncidents({ limit: 50 }),
    refetchInterval: 30_000,
  });
  const events = useQuery({
    queryKey: ["dashboard", "events"],
    queryFn: () => getEvents({ limit: 100 }),
    refetchInterval: 60_000,
  });
  const devices = useQuery({
    queryKey: ["dashboard", "devices"],
    queryFn: () => getDevices(),
    refetchInterval: 60_000,
  });

  const isLoading =
    alerts.isLoading || incidents.isLoading || events.isLoading || devices.isLoading;
  const hasError = Boolean(alerts.error && incidents.error && events.error && devices.error);

  // "Settled without error" is the only state in which an empty payload really
  // means zero: while loading, or after a failed query, the adapter must report
  // an unavailable counter rather than a measured 0.
  const available = {
    alerts: !alerts.isLoading && !alerts.error,
    incidents: !incidents.isLoading && !incidents.error,
    devices: !devices.isLoading && !devices.error,
  };

  const data = buildSocData({
    alerts: alerts.data ?? [],
    incidents: incidents.data ?? [],
    events: events.data ?? [],
    devices: devices.data ?? [],
  }, available);

  if (alerts.error) data.states.alerts = "error";
  if (incidents.error) data.states.incidents = "error";
  if (events.error) data.states.events = "error";
  if (devices.error) data.states.devices = "error";
  if (!isLoading && (alerts.data ?? []).length === 0) data.states.alerts = "empty";

  return { data, isLoading, hasError };
}
