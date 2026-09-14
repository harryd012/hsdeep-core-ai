"use client";

import { useEffect, useState } from "react";
import { getSensors, getDashboardSummary, SensorOut } from "@/lib/api";

export type SensorStatus = "healthy" | "warning" | "critical" | "unknown";

export interface SensorsData {
  total: number;
  healthy: number;
  warning: number;
  down: number;
  healthPct: number;
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
    total: 0,
    healthy: 0,
    warning: 0,
    down: 0,
    healthPct: 0,
    status: "unknown",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [sensors, summary] = await Promise.all([
          getSensors().catch(() => []),
          getDashboardSummary().catch(() => null),
        ]);

        if (cancelled) return;

        // Canonical counts from the full sensor list
        const healthy = sensors.filter((s) => s.status === "ok" || s.status === "healthy").length;
        const warning = sensors.filter((s) => s.status === "warning" || s.status === "warn").length;
        const down = sensors.filter((s) => s.status !== "ok" && s.status !== "healthy" && s.status !== "warning" && s.status !== "warn").length;
        const total = (sensors.length || summary?.sensors) ?? 0;
        const healthPct = total > 0 ? Math.round((healthy / total) * 100) : 0;

        let status: SensorStatus;
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
          healthy,
          warning,
          down,
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
