/**
 * Saved dashboards API (observability plan P6 slice 2). A dashboard is a list
 * of registry widgets; each widget reads one existing API with the viewer's
 * own permissions. Personal by default; sharing needs devices.write.
 */
import { apiFetch } from "@/lib/api";

export type WidgetType =
  | "network_summary"
  | "busiest_links"
  | "interfaces_down"
  | "vpn_tunnels"
  | "sensor_chart"
  | "interface_chart"
  | "coverage_gaps"
  | "server_fleet"
  | "sensor_status";

export type WidgetSize = "small" | "medium" | "large";

export interface DashboardWidget {
  id: string;
  type: WidgetType;
  title: string | null;
  size: WidgetSize;
  config: Record<string, unknown>;
}

export interface Dashboard {
  id: string;
  name: string;
  description: string | null;
  widgets: DashboardWidget[];
  shared: boolean;
  owned: boolean;
  created_at: string;
  updated_at: string;
}

export interface DashboardSummary {
  id: string;
  name: string;
  description: string | null;
  widget_count: number;
  shared: boolean;
  owned: boolean;
  updated_at: string;
}

export interface WidgetTypeInfo {
  type: WidgetType;
  label: string;
  source: string;
  settings: Record<string, unknown>;
}

/** Widgets that need nothing but a click to add (charts are added from sensor and interface pages). */
export const SIMPLE_WIDGETS: WidgetType[] = [
  "network_summary", "busiest_links", "interfaces_down", "vpn_tunnels", "coverage_gaps", "server_fleet", "sensor_status",
];

export function getWidgetRegistry(): Promise<WidgetTypeInfo[]> {
  return apiFetch<WidgetTypeInfo[]>("/api/dashboards/widgets");
}

export function listDashboards(): Promise<DashboardSummary[]> {
  return apiFetch<DashboardSummary[]>("/api/dashboards");
}

export function getDashboard(id: string): Promise<Dashboard> {
  return apiFetch<Dashboard>(`/api/dashboards/${encodeURIComponent(id)}`);
}

export function createDashboard(body: { name: string; description?: string | null; widgets?: DashboardWidget[]; shared?: boolean }): Promise<Dashboard> {
  return apiFetch<Dashboard>("/api/dashboards", { method: "POST", body: JSON.stringify(body) });
}

export function updateDashboard(
  id: string,
  body: { name?: string; description?: string | null; widgets?: DashboardWidget[]; shared?: boolean },
): Promise<Dashboard> {
  return apiFetch<Dashboard>(`/api/dashboards/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteDashboard(id: string): Promise<void> {
  return apiFetch<void>(`/api/dashboards/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** A widget id unique within `existing`, derived from the type. */
export function newWidgetId(type: WidgetType, existing: DashboardWidget[]): string {
  const base = type.replace(/_/g, "-").slice(0, 30);
  const taken = new Set(existing.map((w) => w.id));
  let n = 1;
  while (taken.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}
