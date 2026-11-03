/**
 * Sensor library API (observability plan P3 slice 1): templates built from
 * what the collectors emit, the tenant's sensors per template, bulk threshold
 * application, and per-host coverage recommendations.
 */
import { apiFetch } from "@/lib/api";

export interface SensorTemplate {
  key: string;
  name: string;
  category: string;
  description: string;
  source_types: string[];
  metric_types: string[];
  unit: string | null;
  /** above: higher is worse; below: lower is worse; null: state sensor. */
  direction: "above" | "below" | null;
  thresholds_applicable: boolean;
  default_warning: number | null;
  default_critical: number | null;
  default_origin: string | null;
  sensors: number;
  by_status: Record<string, number>;
  user_thresholds: number;
  reporting_source_types: string[];
}

export interface SensorLibrary {
  templates: SensorTemplate[];
  uncatalogued: { metric_type: string; total: number; source_types: string[] }[];
}

export interface ApplyTemplateRequest {
  thresholds?: { warning: number | null; critical: number | null; direction: "above" | "below" };
  reset?: boolean;
  device_ids?: string[];
  dry_run?: boolean;
}

export interface ApplyTemplateResult {
  template: string;
  matched: number;
  changed: number;
  status_changed: number;
  not_found: string[];
  dry_run: boolean;
}

export interface RecommendationRule {
  key: string;
  title: string;
  description: string;
  action: string;
  hosts: number;
}

export interface RecommendedHost {
  device_id: string;
  name: string;
  device_type: string | null;
  address: string | null;
  vendor: string | null;
  sensors: number;
  interfaces: number;
  rules: string[];
}

export interface Recommendations {
  rules: RecommendationRule[];
  hosts: RecommendedHost[];
  total: number;
  truncated: boolean;
}

export function getSensorLibrary(): Promise<SensorLibrary> {
  return apiFetch<SensorLibrary>("/api/monitoring/sensor-library");
}

export function applySensorTemplate(key: string, body: ApplyTemplateRequest): Promise<ApplyTemplateResult> {
  return apiFetch<ApplyTemplateResult>(`/api/monitoring/sensor-library/templates/${encodeURIComponent(key)}/apply`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getSensorRecommendations(rule: string | null, limit = 100, offset = 0): Promise<Recommendations> {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (rule) qs.set("rule", rule);
  return apiFetch<Recommendations>(`/api/monitoring/sensor-library/recommendations?${qs.toString()}`);
}
