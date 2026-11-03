/**
 * Server fleet resources (observability plan P5): CPU, memory, fullest disk,
 * load and uptime for every server that reports them, worst first. Values
 * are the collectors' own readings; the backend never fills a gap.
 */
import { apiFetch } from "./api";

export type ServerPlatform = "windows" | "linux" | "vmware" | "other";

export interface ResourceReading {
  sensor_id: string;
  /** The sensor's name (a volume for disks, a core for CPU). */
  name: string;
  value: number | null;
  unit: string | null;
  /** The sensor's own status, from its thresholds. */
  status: string;
  recorded_at: string | null;
}

export interface ServerResources {
  device_id: string;
  name: string;
  ip_address: string | null;
  platform: ServerPlatform;
  os_version: string | null;
  device_status: string;
  cpu: ResourceReading | null;
  memory: ResourceReading | null;
  /** The fullest volume; `disks` counts the host's volumes. */
  disk: ResourceReading | null;
  disks: number;
  load: ResourceReading | null;
  uptime: ResourceReading | null;
  last_reading_at: string | null;
  /** No reading for over an hour: values are the last ones received. */
  stale: boolean;
  critical: number;
  warning: number;
  peak_pct: number | null;
}

export interface FleetSummary {
  servers: number;
  by_platform: Record<ServerPlatform, number>;
  critical: number;
  warning: number;
  stale: number;
  disk_over_90: number;
  cpu_over_90: number;
}

export interface ServerFleet {
  summary: FleetSummary;
  servers: ServerResources[];
}

export function getServerFleet(platform?: ServerPlatform): Promise<ServerFleet> {
  const q = platform ? `?platform=${platform}` : "";
  return apiFetch<ServerFleet>(`/api/explorer/hosts/resources${q}`);
}
