/**
 * NOC Event + SOC Analyst types and API functions.
 *
 * Mirrors the backend schemas in:
 *   backend/app/schemas/noc.py  (NocEvent*)
 *   backend/app/schemas/soc.py  (SocAnalyst*)
 *
 * SOC analysts are Agent rows (department="soc", role="soc_analyst") —
 * the SOC roster is derived from the existing Agent table, not a separate
 * model. These types wrap Agent read data with live NOC assignment counts.
 */

import { apiFetch, DEFAULT_TENANT_ID, withTenant } from "./api";

// ---------------------------------------------------------------------------
// NOC Events
// ---------------------------------------------------------------------------

export type NocEventSeverity = "critical" | "warning" | "info";
export type NocEventStatus = "open" | "acknowledged" | "resolved";
export type NocEventCategory =
  | "availability"
  | "performance"
  | "capacity"
  | "connectivity"
  | "configuration"
  | "security"
  | "other";

export interface NocEvent {
  id: string;
  tenant_id: string;
  site_id: string | null;
  device_id: string | null;
  event_signature: string | null;
  source: string;
  category: string;
  title: string;
  description: string | null;
  severity: NocEventSeverity;
  status: NocEventStatus;
  triggered_at: string;
  last_occurred_at: string;
  occurrence_count: number;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  assigned_analyst_id: string | null;
  source_agent_id: string | null;
  confidence_score: number | null;
  created_at: string;
  updated_at: string;
  site_name: string | null;
  device_name: string | null;
  assigned_analyst_name: string | null;
  source_agent_name: string | null;
}

export interface NocEventListItem {
  id: string;
  title: string;
  severity: NocEventSeverity;
  status: NocEventStatus;
  category: string;
  source: string;
  triggered_at: string;
  occurrence_count: number;
  site_name: string | null;
  device_name: string | null;
  assigned_analyst_name: string | null;
}

export interface NocEventPagination {
  items: NocEventListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface NocEventSeverityCounts {
  critical: number;
  warning: number;
  info: number;
}

export interface NocEventStatusCounts {
  open: number;
  acknowledged: number;
  resolved: number;
}

export interface NocEventCategoryCounts {
  availability: number;
  performance: number;
  capacity: number;
  connectivity: number;
  configuration: number;
  security: number;
  other: number;
}

export interface NocEventSummary {
  total: number;
  by_severity: NocEventSeverityCounts;
  by_status: NocEventStatusCounts;
  by_category: NocEventCategoryCounts;
  unassigned_open: number;
  avg_resolution_minutes: number | null;
}

export interface NocEventStatistics extends NocEventSummary {
  mttr_minutes: number | null;
  mttr_by_severity: Record<string, number | null>;
  open_over_30min: number;
  top_sources: Array<{ source: string; count: number }>;
}

// ---------------------------------------------------------------------------
// SOC Analysts
// ---------------------------------------------------------------------------

export interface SocAnalyst {
  id: string;
  agent_id: string;
  name: string;
  role: string;
  department: string;
  level: string;
  manager_id: string | null;
  status: string;
  availability: string;
  is_active: boolean;
  current_workload: number;
  max_workload: number;
  skills: string[];
  supported_task_types: string[];
  success_rate: number;
  tasks_completed: number;
  tasks_failed: number;
  avg_resolution_minutes: number | null;
  created_at: string;
  updated_at: string;
  open_noc_events: number;
  acknowledged_noc_events: number;
  total_assigned_events: number;
  resolved_today: number;
  last_assigned_at: string | null;
}

export interface SocAnalystDetail extends SocAnalyst {
  assigned_events: Array<{
    id: string;
    title: string;
    severity: NocEventSeverity;
    status: NocEventStatus;
    triggered_at: string | null;
  }>;
}

export interface SocAnalystSummary {
  total_analysts: number;
  available_analysts: number;
  busy_analysts: number;
  offline_analysts: number;
  total_open_events: number;
  unassigned_open_events: number;
  avg_success_rate: number;
}

// ---------------------------------------------------------------------------
// Query parameter types
// ---------------------------------------------------------------------------

export interface NocEventListParams {
  site_id?: string;
  device_id?: string;
  severity?: NocEventSeverity;
  status?: NocEventStatus;
  category?: string;
  source?: string;
  assigned_analyst_id?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export function getNocEvents(
  params: NocEventListParams = {},
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEventPagination> {
  const qs = withTenant(tenantId);
  if (params.site_id) qs.set("site_id", params.site_id);
  if (params.device_id) qs.set("device_id", params.device_id);
  if (params.severity) qs.set("severity", params.severity);
  if (params.status) qs.set("status", params.status);
  if (params.category) qs.set("category", params.category);
  if (params.source) qs.set("source", params.source);
  if (params.assigned_analyst_id) qs.set("assigned_analyst_id", params.assigned_analyst_id);
  if (params.since) qs.set("since", params.since);
  if (params.until) qs.set("until", params.until);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<NocEventPagination>(`/api/noc/events?${qs.toString()}`);
}

export function getNocEvent(
  eventId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events/${eventId}?${withTenant(tenantId).toString()}`);
}

export function getNocEventSummary(
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEventSummary> {
  return apiFetch<NocEventSummary>(`/api/noc/events/summary?${withTenant(tenantId).toString()}`);
}

export function getNocEventStatistics(
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEventStatistics> {
  return apiFetch<NocEventStatistics>(`/api/noc/events/statistics?${withTenant(tenantId).toString()}`);
}

export function acknowledgeNocEvent(
  eventId: string,
  acknowledgedBy: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events/${eventId}/acknowledge?${withTenant(tenantId).toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ acknowledged_by: acknowledgedBy }),
  });
}

export function resolveNocEvent(
  eventId: string,
  resolvedBy: string | null,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events/${eventId}/resolve?${withTenant(tenantId).toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolved_by: resolvedBy }),
  });
}

export function assignNocEvent(
  eventId: string,
  analystId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events/${eventId}/assign?${withTenant(tenantId).toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ analyst_id: analystId }),
  });
}

// -- Create / Update --

export function createNocEvent(
  payload: {
    title: string;
    severity: NocEventSeverity;
    source: string;
    category?: NocEventCategory;
    description?: string | null;
    event_signature?: string | null;
    triggered_at?: string;
    site_id?: string | null;
    device_id?: string | null;
    source_agent_id?: string | null;
    assigned_analyst_id?: string | null;
    confidence_score?: number | null;
  },
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events?${withTenant(tenantId).toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateNocEvent(
  eventId: string,
  payload: {
    title?: string;
    description?: string | null;
    severity?: NocEventSeverity;
    category?: NocEventCategory;
    assigned_analyst_id?: string | null;
    status?: NocEventStatus;
  },
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<NocEvent> {
  return apiFetch<NocEvent>(`/api/noc/events/${eventId}?${withTenant(tenantId).toString()}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// -- SOC --

export function getSocAnalysts(
  params: { availability?: string; level?: string; limit?: number; offset?: number } = {},
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<SocAnalyst[]> {
  const qs = withTenant(tenantId);
  if (params.availability) qs.set("availability", params.availability);
  if (params.level) qs.set("level", params.level);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<SocAnalyst[]>(`/api/soc/analysts?${qs.toString()}`);
}

export function getSocAnalystSummary(
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<SocAnalystSummary> {
  return apiFetch<SocAnalystSummary>(`/api/soc/analysts/summary?${withTenant(tenantId).toString()}`);
}

export function getSocAnalystDetail(
  analystId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<SocAnalystDetail> {
  return apiFetch<SocAnalystDetail>(`/api/soc/analysts/${analystId}?${withTenant(tenantId).toString()}`);
}
