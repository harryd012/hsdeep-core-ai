"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  getDevice,
  getDeviceAlerts,
  getDeviceEvents,
  getDeviceInterfaces,
  getDeviceSensors,
  getSensorMetrics,
} from "@/lib/api";

/**
 * useHostDetail — the reads behind the Host page, one query per facet of the
 * host so each tab loads (and fails) on its own. All are device-nested routes
 * that 404 for another tenant's device. Refreshes every minute while open.
 */
export function useHostDetail(deviceId: string) {
  const { isAuthenticated } = useAuth();
  const enabled = isAuthenticated && !!deviceId;
  const common = { enabled, refetchInterval: 60_000, staleTime: 30_000 } as const;
  return {
    // A 404 or quota refusal will not change on retry.
    device: useQuery({
      queryKey: ["host", deviceId, "device"],
      queryFn: () => getDevice(deviceId),
      ...common,
      retry: (n, e) => !(e instanceof ApiError && (e.status === 404 || e.status === 429)) && n < 1,
    }),
    sensors: useQuery({ queryKey: ["host", deviceId, "sensors"], queryFn: () => getDeviceSensors(deviceId), ...common }),
    alerts: useQuery({ queryKey: ["host", deviceId, "alerts"], queryFn: () => getDeviceAlerts(deviceId), ...common }),
    interfaces: useQuery({ queryKey: ["host", deviceId, "interfaces"], queryFn: () => getDeviceInterfaces(deviceId), ...common }),
    events: useQuery({ queryKey: ["host", deviceId, "events"], queryFn: () => getDeviceEvents(deviceId), ...common }),
  };
}

/**
 * Metric history for one sensor over the chosen window (newest first from the
 * API). When the window holds no readings — a sensor that stopped reporting —
 * it falls back to the most recent readings and says so via `fallback`, so
 * the chart shows the last known behaviour instead of an empty box.
 */
export function useSensorHistory(sensorId: string | null, windowHours: number) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ["sensor-history", sensorId, windowHours],
    queryFn: async () => {
      const points = await getSensorMetrics(sensorId!, {
        since: new Date(Date.now() - windowHours * 3_600_000).toISOString(),
        limit: 2000,
      });
      if (points.length) return { points, fallback: false };
      return { points: await getSensorMetrics(sensorId!, { limit: 300 }), fallback: true };
    },
    enabled: isAuthenticated && !!sensorId,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}
