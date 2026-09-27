"use client";

import { useEffect, useState } from "react";
import { getDevices, getNetworkStatus, Device } from "@/lib/api";

export type DeviceStatus = "healthy" | "warning" | "critical" | "unknown";

export interface DevicesData {
  /** null = the backend did not answer (renders N/A). 0 = a measured zero. */
  total: number | null;
  online: number | null;
  offline: number | null;
  /** null when either part of the ratio is unavailable - never a 0% claim. */
  healthPct: number | null;
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
    total: null,
    online: null,
    offline: null,
    healthPct: null,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [devices, net] = await Promise.all([
          getDevices().catch(() => null),
          getNetworkStatus().catch(() => null),
        ]);

        if (cancelled) return;

        // `devices === null` means the list call failed - NOT "no devices".
        // When it did fail, network status still carries measured totals, so the
        // fallback uses ITS up/down counters rather than deriving them.
        const total = devices ? devices.length : (net?.devices_total ?? null);
        const online = devices
          ? devices.filter((d) => d.status === "active" || d.status === "online").length
          : (net?.devices_up ?? null);
        const offline = devices
          ? (total !== null && online !== null ? total - online : null)
          : (net?.devices_down ?? null);
        // A percentage needs BOTH parts; without them it is unavailable, not 0%.
        const healthPct =
          total !== null && total > 0 && online !== null
            ? Math.round((online / total) * 100)
            : null;

        let status: DeviceStatus = "unknown";
        if (total !== null && total > 0 && healthPct !== null) {
          status = healthPct >= 90 ? "healthy" : healthPct >= 70 ? "warning" : "critical";
        }

        setData({
          total,
          online,
          offline,
          healthPct,
          status,
          loading: false,
          error: devices === null && net === null ? "Device data unavailable" : null,
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
