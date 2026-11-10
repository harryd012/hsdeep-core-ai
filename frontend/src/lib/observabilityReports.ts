/**
 * Observability reports API (observability plan P11 slice 1): availability,
 * sensor statistics and interface traffic over a period, computed from what
 * was recorded. Windows up to 90 days.
 */
import { apiFetch } from "@/lib/api";

export interface Period {
  since: string;
  until: string;
}

export interface AvailabilityRow {
  device_id: string;
  device: string;
  device_type: string | null;
  address: string | null;
  criticality: string | null;
  samples: number;
  up_samples: number;
  down_samples: number;
  availability_pct: number;
  first_sample: string;
  last_sample: string;
}

export interface AvailabilityReport extends Period {
  summary: {
    devices_measured: number;
    devices_not_measured: number;
    fleet_availability_pct: number | null;
    devices_below_99_9: number;
    devices_below_99: number;
    samples: number;
  };
  items: AvailabilityRow[];
}

export interface SensorRow {
  sensor_id: string;
  sensor: string;
  metric_type: string;
  unit: string | null;
  status: string;
  device: string | null;
  samples: number;
  avg: number | null;
  min: number | null;
  max: number | null;
  p95: number | null;
  alerts: number;
  alert_minutes: number;
}

export interface SensorReport extends Period {
  summary: { sensors: number; with_readings: number; with_alerts: number; alert_minutes: number; period_minutes: number };
  items: SensorRow[];
}

export interface InterfaceRow {
  interface_id: string;
  interface: string;
  device_id: string;
  device: string;
  speed_mbps: number | null;
  samples: number;
  rx_avg_bps: number | null;
  rx_peak_bps: number | null;
  tx_avg_bps: number | null;
  tx_peak_bps: number | null;
  utilization_avg_pct: number | null;
  utilization_peak_pct: number | null;
  errors_in_avg_per_s: number | null;
  errors_in_peak_per_s: number | null;
  errors_out_avg_per_s: number | null;
  errors_out_peak_per_s: number | null;
}

export interface InterfaceReport extends Period {
  summary: { interfaces: number; busy_over_80: number; with_errors: number };
  items: InterfaceRow[];
}

const qs = (p: Period, extra: Record<string, string | null | undefined> = {}) => {
  const q = new URLSearchParams({ since: p.since, until: p.until });
  for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
  return q.toString();
};

export function getAvailabilityReport(p: Period): Promise<AvailabilityReport> {
  return apiFetch<AvailabilityReport>(`/api/reports/availability?${qs(p)}`);
}

export function getSensorReport(p: Period, metricType?: string | null): Promise<SensorReport> {
  return apiFetch<SensorReport>(`/api/reports/sensors?${qs(p, { metric_type: metricType })}`);
}

export function getInterfaceReport(p: Period): Promise<InterfaceReport> {
  return apiFetch<InterfaceReport>(`/api/reports/interfaces?${qs(p)}`);
}
