/**
 * Dynamic baselines API (observability plan P9 slice 2): a sensor learns its
 * usual value per hour of the day from its own history and raises a warning
 * when a reading falls far outside it.
 */
import { apiFetch } from "@/lib/api";

export type BaselineDirection = "both" | "above" | "below";
/** off | learning | normal | anomalous | stale | paused */
export type BaselineState = "off" | "learning" | "normal" | "anomalous" | "stale" | "paused";

export interface HourBand {
  hour: number;
  expected: number;
  low: number;
  high: number;
  samples: number;
  basis: "hour" | "overall";
}

export interface SensorBaseline {
  sensor_id: string;
  configured: boolean;
  enabled: boolean;
  direction: BaselineDirection;
  sensitivity: number;
  state: BaselineState;
  ready: boolean;
  samples: number;
  span_days: number;
  window_days: number;
  min_span_days: number;
  min_samples: number;
  last_value: number | null;
  last_expected: number | null;
  last_z: number | null;
  last_evaluated_at: string | null;
  computed_at: string | null;
  hours: HourBand[];
}

export function getSensorBaseline(sensorId: string): Promise<SensorBaseline> {
  return apiFetch<SensorBaseline>(`/api/monitoring/sensors/${encodeURIComponent(sensorId)}/baseline`);
}

export function putSensorBaseline(
  sensorId: string,
  body: { enabled: boolean; direction: BaselineDirection; sensitivity: number },
): Promise<SensorBaseline> {
  return apiFetch<SensorBaseline>(`/api/monitoring/sensors/${encodeURIComponent(sensorId)}/baseline`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
