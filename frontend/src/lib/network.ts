/**
 * Network Monitoring Center API (observability plan P4 slice 1): interface
 * health and load, busiest links, errors, VPN tunnels, and per-interface
 * history. Rates come only from two real counter samples; `fresh` is false
 * when the last sample is older than 30 minutes.
 */
import { apiFetch } from "@/lib/api";

export interface NetworkInterface {
  id: string;
  device_id: string;
  device_name: string;
  device_type: string | null;
  name: string;
  description: string | null;
  interface_type: string | null;
  admin_status: string;
  oper_status: string;
  speed_mbps: number | null;
  rx_bps: number | null;
  tx_bps: number | null;
  throughput_bps: number | null;
  utilization_pct: number | null;
  in_errors: number | null;
  out_errors: number | null;
  last_telemetry_at: string | null;
  last_status_change_at: string | null;
  fresh: boolean;
}

export interface VpnTunnel {
  id: string;
  device_id: string;
  device_name: string;
  name: string;
  tunnel_type: string;
  remote_gateway: string | null;
  status: string;
  phase2_up: number | null;
  phase2_total: number | null;
  bytes_in: number | null;
  bytes_out: number | null;
  last_change_at: string | null;
  last_seen_at: string;
}

export interface NetworkOverview {
  as_of: string;
  interfaces: {
    total: number;
    up: number;
    down: number;
    unexpected_down: number;
    admin_down: number;
    fresh: number;
    measured: number;
    devices: number;
    last_telemetry_at: string | null;
  };
  top_utilization: NetworkInterface[];
  top_throughput: NetworkInterface[];
  unexpected_down: NetworkInterface[];
  top_errors: (NetworkInterface & { errors_per_s_1h: number })[];
  tunnels: { total: number; up: number; down: number; items: VpnTunnel[] };
}

export type InterfaceStatusFilter = "up" | "down" | "unexpected_down" | "admin_down";
export type InterfaceSort = "name" | "utilization" | "throughput" | "status";

export interface NetworkInterfaceDetail extends NetworkInterface {
  mac_address: string | null;
  vlan_id: number | null;
  history: Record<string, { samples: number; last_recorded_at: string | null }>;
}

export interface InterfaceHistory {
  hours: number;
  bucket_seconds: number;
  series: Record<"rx_bps" | "tx_bps" | "interface_utilization_pct" | "errors_in" | "errors_out", { t: string; avg: number; max: number }[]>;
}

export function getNetworkOverview(): Promise<NetworkOverview> {
  return apiFetch<NetworkOverview>("/api/network/overview");
}

export function getNetworkInterfaces(params: {
  status?: InterfaceStatusFilter | null;
  q?: string;
  sort?: InterfaceSort;
  limit?: number;
  offset?: number;
  deviceId?: string;
}): Promise<{ items: NetworkInterface[]; total: number }> {
  const qs = new URLSearchParams({ sort: params.sort ?? "status", limit: String(params.limit ?? 100), offset: String(params.offset ?? 0) });
  if (params.status) qs.set("status", params.status);
  if (params.q?.trim()) qs.set("q", params.q.trim());
  if (params.deviceId) qs.set("device_id", params.deviceId);
  return apiFetch(`/api/network/interfaces?${qs.toString()}`);
}

export function getNetworkInterface(id: string): Promise<NetworkInterfaceDetail> {
  return apiFetch<NetworkInterfaceDetail>(`/api/network/interfaces/${encodeURIComponent(id)}`);
}

export function getInterfaceHistory(id: string, hours: number): Promise<InterfaceHistory> {
  return apiFetch<InterfaceHistory>(`/api/network/interfaces/${encodeURIComponent(id)}/history?hours=${hours}`);
}

/** Bits per second in the largest fitting unit. */
export function formatBps(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"];
  let n = v;
  let u = 0;
  while (n >= 1000 && u < units.length - 1) {
    n /= 1000;
    u += 1;
  }
  return `${n.toLocaleString("en-US", { maximumFractionDigits: n < 10 ? 2 : 1 })} ${units[u]}`;
}

export function formatBytes(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = v;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u += 1;
  }
  return `${n.toLocaleString("en-US", { maximumFractionDigits: 1 })} ${units[u]}`;
}
