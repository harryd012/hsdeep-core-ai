"use client";

import { useEffect, useState } from "react";
import { getSensors, getDashboardSummary, SensorOut } from "@/lib/api";

export type SensorStatus = "healthy" | "warning" | "critical" | "unknown";

export interface SensorsData {
  /** null = the backend did not answer (renders N/A). 0 = a measured zero. */
  total: number | null;
  healthy: number | null;
  warning: number | null;
  down: number | null;
  /** null when either part of the ratio is unavailable - never a 0% claim. */
  healthPct: number | null;
  status: SensorStatus;
  loading: boolean;
  error: string | null;
}

const REFRESH_MS = 60_000;

/**
 * Canonical source of truth for sensor data.
 *
 * Every widget that displays sensor counts or sensor health MUST read from
 * this hook so that no two widgets can disagree about the same underlying
 * number.
 *
 * Primary source: getSensors() — provides the full list with individual
 *   statuses (ok/healthy, warning/warn, down).
 * Fallback: getDashboardSummary() — provides sensors (total count only).
 */
export function useSensorsData(refreshMs = REFRESH_MS): SensorsData {
  const [data, setData] = useState<SensorsData>({
    total: null,
    healthy: null,
    warning: null,
    down: null,
    healthPct: null,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [sensors, summary] = await Promise.all([
          getSensors().catch(() => null),
          getDashboardSummary().catch(() => null),
        ]);

        if (cancelled) return;

        // `sensors === null` means the list call failed - NOT "no sensors", and a
        // failed call must not turn every sensor into a 0 or a "down".
        const total = sensors ? sensors.length : (summary?.sensors ?? null);
        const healthy = sensors
          ? sensors.filter((s) => s.status === "ok" || s.status === "healthy").length
          : null;
        const warning = sensors
          ? sensors.filter((s) => s.status === "warning" || s.status === "warn").length
          : null;
        // "down" counts a REPORTED failing state only: the backend's default
        // "unknown" status is not an outage and must not be counted as one.
        const down = sensors
          ? sensors.filter(
              (s) => !["ok", "healthy", "warning", "warn", "unknown", ""].includes((s.status || "").toLowerCase()),
            ).length
          : null;
        // A percentage needs BOTH parts; without them it is unavailable, not 0%.
        const healthPct =
          total !== null && total > 0 && healthy !== null
            ? Math.round((healthy / total) * 100)
            : null;

        let status: SensorStatus = "unknown";
        if (total !== null && total > 0 && healthPct !== null) {
          status = healthPct >= 90 ? "healthy" : healthPct >= 70 ? "warning" : "critical";
        }

        setData({
          total,
          healthy,
          warning,
          down,
          healthPct,
          status,
          loading: false,
          error: sensors === null && summary === null ? "Sensor data unavailable" : null,
        });
      } catch (err) {
        if (!cancelled) {
          setData((prev) => ({
            ...prev,
            loading: false,
            error: err instanceof Error ? err.message : "Failed to load sensors data",
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
