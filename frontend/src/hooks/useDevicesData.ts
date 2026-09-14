"use client";

import { useEffect, useState } from "react";
import { getDevices, getNetworkStatus, Device } from "@/lib/api";

export type DeviceStatus = "healthy" | "warning" | "critical" | "unknown";

export interface DevicesData {
  total: number;
  online: number;
  offline: number;
  healthPct: number;
  status: DeviceStatus;
  loading: boolean;
  error: string | null;
}

const REFRESH_MS = 60_000;

/**
 * Canonical source of truth for device data.
 *
 * Every widget that displays device counts or device health MUST read from
 * this hook so that no two widgets can disagree about the same underlying
 * number.
 *
 * Primary source: getDevices() — provides the full list with individual
 *   statuses (active/online vs. others).
 * Fallback: getNetworkStatus() — provides devices_total, devices_up,
 *   devices_down.
 */
export function useDevicesData(refreshMs = REFRESH_MS): DevicesData {
  const [data, setData] = useState<DevicesData>({
    total: 0,
    online: 0,
    offline: 0,
    healthPct: 0,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [devices, net] = await Promise.all([
          getDevices().catch(() => []),
          getNetworkStatus().catch(() => null),
        ]);

        if (cancelled) return;

        // Canonical counts from the full device list
        const online = devices.filter((d) => d.status === "active" || d.status === "online").length;
        const total = (devices.length || net?.devices_total) ?? 0;
        const offline = total - online;
        const healthPct = total > 0 ? Math.round((online / total) * 100) : 0;

        let status: DeviceStatus;
        if (total === 0) {
          status = "unknown";
        } else if (healthPct >= 90) {
          status = "healthy";
        } else if (healthPct >= 70) {
          status = "warning";
        } else {
          status = "critical";
        }

        setData({
          total,
          online,
          offline,
          healthPct,
          status,
          loading: false,
          error: null,
        });
      } catch (err) {
        if (!cancelled) {
          setData((prev) => ({
            ...prev,
            loading: false,
            error: err instanceof Error ? err.message : "Failed to load devices data",
          }));
        }
      }
    }

    load();
    const timer = setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refreshMs]);

  return data;
}
