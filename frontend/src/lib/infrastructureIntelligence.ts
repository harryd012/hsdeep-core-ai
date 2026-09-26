
/**
 * Universal Infrastructure Intelligence API client.
 *
 * Thin, typed wrapper over the existing `apiFetch` client (lib/api.ts), so the
 * SAME auth/token handling every other panel uses applies here too.
 *
 * Contract notes that matter for the UI:
 * - A capability is reported as `supported` + `status` + `reason`. `supported`
 *   is `false` when the provider or the resource cannot supply it, and `reason`
 *   always explains why. "Not evaluated yet" is `support: "unknown"`.
 * - `measured` lists the metrics that actually carry a value, so the UI can
 *   render "not measured" instead of a misleading 0.
 * - Everything is tenant-scoped server-side from the JWT; this module never
 *   sends a tenant id.
 */

import { apiFetch } from "./api";

/* ------------------------------------------------------------------------ *
 * WAN Failover Intelligence (observed failover episodes)
 *
 * Contract notes that matter for the UI:
 * - An episode's status is a forward-only FSM: detected -> failed_over ->
 *   recovering -> recovered, with `aborted` (breach cleared, no path switch)
 *   and `verification_failed` (looked healthy, could not be confirmed) as
 *   honest terminal states. There is no "override to recovered" anywhere.
 * - `provenance` on every replay entry says whether it is `captured` history,
 *   a `derived` interpretation, or `context` joined from other tables. Render
 *   it - a `context` entry has a different collection cadence than a captured
 *   transition.
 * - `gaps` lists the signals that were NOT available. An empty routing section
 *   with a gap entry means "not captured", never "nothing happened".
 * - `null` means the value was not collected. Render "not measured"/"unknown",
 *   never 0.
 * ------------------------------------------------------------------------ */

export const FAILOVER_BASE = "/api/failover";

export type FailoverStatus =
  | "detected"
  | "failed_over"
  | "recovering"
  | "recovered"
  | "verification_failed"
  | "aborted";

export type EvidenceFinding = "supports" | "neutral" | "contradicts";
export type CheckStatus = "pass" | "fail" | "unknown";
export type ReplayProvenance = "captured" | "derived" | "context";

export interface FailoverPathOut {
  name: string;
  score: number;
  health: string;
  available: boolean;
}

export interface FailoverDecisionOut {
  selected_path?: string | null;
  score?: number;
  decision?: string;
  failover?: boolean;
  reason?: string;
  paths?: FailoverPathOut[];
}

export interface FailoverEventOut {
  id: string;
  sequence_no: number;
  status: FailoverStatus;
  status_changed_at: string | null;
  site_id: string | null;
  edge_device_id: string | null;
  scope_key: string;
  link_from_id: string | null;
  link_from_name: string | null;
  link_to_id: string | null;
  link_to_name: string | null;
  detected_at: string | null;
  failed_over_at: string | null;
  recovering_at: string | null;
  recovered_at: string | null;
  incident_id: string | null;
  is_open: boolean;
  blast_radius_total: number;
  blast_radius_critical: number;
  evidence_count: number;
  route_transition_count: number;
  bgp_transition_count: number;
  verification_status: string | null;
}

export interface FailoverTransitionOut {
  key?: string;
  kind?: string;
  field?: string;
  from?: unknown;
  to?: unknown;
  label?: string | null;
  at?: string | null;
}

export interface EvidenceItemOut {
  kind: string;
  claim: string;
  finding: EvidenceFinding;
  confidence: number;
  source: string;
  observed_at: string | null;
  detail: Record<string, unknown>;
}

export interface BlastRadiusNodeOut {
  resource_id: string;
  name: string | null;
  resource_type: string | null;
  site_id: string | null;
  severity: "critical" | "warning" | "info";
  hops: number;
  via: string[];
  path: string[];
}

export interface BlastRadiusOut {
  seed: string[];
  total: number;
  by_severity: Record<string, number>;
  nodes: BlastRadiusNodeOut[];
  truncated: boolean;
  truncated_reason?: string | null;
  source?: string;
  link_name?: string;
}

export interface VerificationCheckOut {
  name: string;
  status: CheckStatus;
  detail: string;
  source: string;
  observed?: unknown;
}

export interface VerificationOut {
  status: CheckStatus;
  summary: string;
  method?: string;
  checked_at?: string | null;
  counts?: Record<string, number>;
  checks: VerificationCheckOut[];
  event_id?: string;
  link_to?: string | null;
  required_samples?: number;
  trigger?: string;
}

export interface FailoverEventDetailOut extends FailoverEventOut {
  trigger: Record<string, unknown>;
  decision: FailoverDecisionOut;
  transition_log: { status: FailoverStatus; at: string; reason: string | null }[];
  route_transitions: FailoverTransitionOut[];
  bgp_transitions: FailoverTransitionOut[];
  blast_radius: BlastRadiusOut;
  evidence: EvidenceItemOut[];
  verification: VerificationOut | null;
  attributes: Record<string, unknown>;
}

export interface ReplayTimelineEntryOut {
  at: string | null;
  kind: string;
  title: string;
  provenance: ReplayProvenance;
  source: string;
  detail: Record<string, unknown>;
}

export interface FailoverReplayOut {
  event_id: string;
  status: FailoverStatus;
  link_from: string | null;
  link_to: string | null;
  timeline: ReplayTimelineEntryOut[];
  gaps: string[];
  phases: {
    from: string | null;
    to: string | null;
    started_at: string;
    ended_at: string;
    seconds: number;
  }[];
  coverage: {
    from: string | null;
    to: string | null;
    duration_seconds: number | null;
    still_open: boolean;
    is_partial: boolean;
  };
  provenance_summary: Record<string, number>;
}

export interface FailoverEventDetailResponse {
  event: FailoverEventDetailOut;
  replay: FailoverReplayOut | null;
}

export const UNIVERSAL_BASE = "/api/infrastructure/intelligence";

export interface CapabilityAssertion {
  capability: string;
  supported: boolean;
  support: "supported" | "unsupported" | "unknown";
  status: string;
  reason: string | null;
  provider: string | null;
}

export interface CapabilityReport {
  found?: boolean;
  resource_id: string | null;
  resource_type?: string;
  family?: string;
  provider?: string | null;
  supported?: string[];
  unsupported?: { capability: string; reason: string | null }[];
  unknown?: { capability: string; reason: string | null }[];
  assertions?: Record<string, CapabilityAssertion>;
  counts?: Record<string, number>;
  telemetry_capabilities?: string[];
}

export interface ResourceHealthOut {
  resource_id: string;
  health: string;
  score: number;
  raw_score: number;
  status: string;
  reasons: { reason: string; detail?: string }[];
  dependencies: {
    resource_id: string;
    name?: string | null;
    relationship_type?: string;
    health: string;
    status?: string;
    reason?: string;
  }[];
  in_maintenance: boolean;
  telemetry_quality: string;
  telemetry_freshness_seconds: number | null;
  last_telemetry_at: string | null;
}

export interface HealthRollupOut {
  scope: string;
  scope_id: string | null;
  health: string;
  score: number;
  members: number;
  counts: Record<string, number>;
  maintenance_members: number;
  child_rollups: {
    site_id: string;
    name: string;
    health: string;
    score: number;
    members: number;
  }[];
  reasons: { reason: string; detail?: string }[];
}

export interface UniversalResourceOut {
  resource_id: string;
  site_id: string | null;
  parent_resource_id: string | null;
  resource_type: string;
  resource_subtype: string | null;
  family: string;
  provider: string | null;
  provider_resource_id: string | null;
  name: string;
  location: Record<string, unknown>;
  status: string;
  status_raw: string;
  health: string;
  health_reason: string | null;
  lifecycle_state: string;
  last_telemetry_at: string | null;
  metadata: Record<string, unknown>;
  health_detail?: ResourceHealthOut | null;
  //: Phase 7 operational state. Present on list + detail responses.
  operational_state?: OperationalStateOut | null;
}

/* ------------------------------------------------------------------------ *
 * Operational state (Phase 7)
 *
 * Four SEPARATE answers, deliberately not collapsed into one "status":
 *   identity_stage      - how far have we got in understanding the resource?
 *   monitoring.binding  - is a collector responsible for its telemetry?
 *   freshness           - is the telemetry we hold still current?
 *   topology            - do we hold adjacency evidence for it?
 *
 * `blockers` names the missing evidence that holds the resource at its stage.
 * ------------------------------------------------------------------------ */

export type IdentityStage =
  | "discovered"
  | "identified"
  | "classified"
  | "capability_resolved"
  | "collectable"
  | "monitored"
  | "stale"
  | "retired";

export type FreshnessState =
  | "fresh"
  | "aging"
  | "stale"
  | "expired"
  | "never_collected"
  | "unavailable";

export type BindingState = "bound" | "unbound" | "ambiguous";

export type TopologyState =
  | "active"
  | "stale"
  | "removed"
  | "no_relationship_evidence";

export interface OperationalStateOut {
  resource_id: string | null;
  resource_type: string | null;
  resource_name: string | null;
  lifecycle_state: string | null;
  identity_stage: IdentityStage;
  identity_reason: string;
  is_operational_resource: boolean;
  freshness: {
    state: FreshnessState;
    is_current: boolean;
    age_seconds: number | null;
    last_telemetry_at: string | null;
    reason: string | null;
    detail: string;
  };
  binding: {
    state: BindingState;
    is_bound: boolean;
    monitoring_source_id: string | null;
    monitoring_source_name: string | null;
    monitoring_source_type: string | null;
    monitoring_source_active: boolean | null;
    provider: string | null;
    provider_resource_id: string | null;
    detail: string;
  };
  topology: {
    state: TopologyState;
    relationship_count: number;
    current: number;
    stale: number;
    detail: string;
  };
  blockers: string[];
  evidence_sources: string[];
}

export interface PageOut<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  resource_type?: string;
}

export interface InterfaceOut {
  interface_id: string;
  name: string;
  interface_type: string;
  description: string | null;
  vlan_id: number | null;
  admin_state: string;
  oper_state: string;
  rx_bps: number | null;
  tx_bps: number | null;
  utilization_pct: number | null;
  errors_in: number | null;
  errors_out: number | null;
  discards_in: number | null;
  discards_out: number | null;
  packet_loss_pct: number | null;
  latency_ms: number | null;
  jitter_ms: number | null;
  measured: string[];
}

export interface InterfacesOut {
  found: boolean;
  resource_id: string;
  items: InterfaceOut[];
  total: number;
  up: number;
  down: number;
  unknown: number;
  supported: boolean;
  reason: string | null;
}

export interface TelemetryRecordOut {
  capability: string;
  metric_name: string;
  timestamp: string | null;
  numeric_value: number | null;
  text_value: string | null;
  boolean_value: boolean | null;
  unit: string | null;
  status: string;
  collection_status: string;
  quality: string;
  has_value: boolean;
}

export interface SeriesSummaryOut {
  metric_name: string;
  capability: string | null;
  unit: string | null;
  samples: number;
  minimum: number | null;
  maximum: number | null;
  average: number | null;
  latest_value: number | null;
  latest_timestamp: string | null;
  quality: string;
}

export interface TelemetryOut {
  found: boolean;
  resource_id: string;
  current: TelemetryRecordOut[];
  series: SeriesSummaryOut[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  quality: string;
}

export interface NetworkSectionOut {
  supported: boolean;
  status: string;
  reason: string | null;
  items: Record<string, unknown>[];
  total: number;
  by_protocol?: Record<string, number>;
  established?: number;
  available?: boolean;
  evaluation?: Record<string, unknown>;
  not_evaluated?: Record<string, unknown>[];
}

export interface ResourceNetworkOut {
  found: boolean;
  resource_id?: string;
  wan?: NetworkSectionOut;
  sdwan?: NetworkSectionOut;
  vpn?: NetworkSectionOut;
  routing?: NetworkSectionOut;
  bgp?: NetworkSectionOut;
}

export interface ResourceDetailOut {
  resource: UniversalResourceOut;
  health: ResourceHealthOut;
  capabilities: CapabilityReport;
}

export interface CorrelationContextOut {
  tenant_id: string;
  generated_at?: string;
  resources: Record<string, unknown>[];
  groups: Record<string, unknown>[];
  correlation_basis: string[];
  limitations: { reason: string; detail?: string }[];
}

export interface ProviderOut {
  provider: string;
  kind: string;
  wired: boolean;
  declared: boolean;
  source_types: string[];
  capabilities: string[];
  capability_count: number;
  resource_types: string[];
}

export interface ProvidersOut {
  providers: ProviderOut[];
  wired: string[];
  declared_count: number;
  wired_count: number;
  source_type_map: Record<string, string>;
}

export interface TenantOverviewOut {
  tenant_id: string;
  generated_at: string;
  health: HealthRollupOut;
  sites: { total: number };
  resources: {
    total: number;
    by_resource_type: Record<string, number>;
    by_family: Record<string, number>;
    by_provider: Record<string, number>;
    by_lifecycle_state: Record<string, number>;
    by_operational_state: Record<string, number>;
  };
  interfaces: { total: number };
  network: {
    routing_entries: number;
    routing_by_protocol: Record<string, number>;
    bgp_neighbors: number;
    bgp_by_state: Record<string, number>;
  };
  cloud: { resources: number; by_resource_type: Record<string, number> };
  capabilities: {
    asserted: number;
    by_support: Record<string, number>;
    supported_by_capability: Record<string, number>;
  };
  incidents: { open: number };
  telemetry: {
    newest_telemetry_at: string | null;
    freshness_seconds: number | null;
    quality: string;
    records_by_capability: Record<string, number>;
  };
  providers: { wired: string[]; in_use: string[] };
}

export interface SiteOverviewOut {
  found: boolean;
  site?: {
    site_id: string;
    name: string;
    code: string;
    criticality: string | null;
    region: string | null;
  };
  health?: HealthRollupOut;
  resources?: {
    total: number;
    by_resource_type: Record<string, number>;
    by_family: Record<string, number>;
  };
  interfaces?: { total: number };
  wan?: NetworkSectionOut;
  sdwan?: NetworkSectionOut;
  vpn?: NetworkSectionOut;
  incidents?: { open: number };
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

export interface ResourceListParams {
  siteId?: string;
  resourceType?: string;
  family?: string;
  provider?: string;
  health?: string;
  lifecycleState?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export function getTenantOverview(): Promise<TenantOverviewOut> {
  return apiFetch<TenantOverviewOut>(`${UNIVERSAL_BASE}/tenant/overview`);
}

export function getTenantHealth(): Promise<HealthRollupOut> {
  return apiFetch<HealthRollupOut>(`${UNIVERSAL_BASE}/tenant/health`);
}

export function getSiteOverview(siteId: string): Promise<SiteOverviewOut> {
  return apiFetch<SiteOverviewOut>(`${UNIVERSAL_BASE}/sites/${siteId}/overview`);
}

export function getProviders(): Promise<ProvidersOut> {
  return apiFetch<ProvidersOut>(`${UNIVERSAL_BASE}/providers`);
}

export function listResources(
  params: ResourceListParams = {},
): Promise<PageOut<UniversalResourceOut>> {
  return apiFetch<PageOut<UniversalResourceOut>>(
    `${UNIVERSAL_BASE}/resources${query({
      site_id: params.siteId,
      resource_type: params.resourceType,
      family: params.family,
      provider: params.provider,
      health: params.health,
      lifecycle_state: params.lifecycleState,
      search: params.search,
      limit: params.limit,
      offset: params.offset,
    })}`,
  );
}

export function getResource(resourceId: string): Promise<ResourceDetailOut> {
  return apiFetch<ResourceDetailOut>(`${UNIVERSAL_BASE}/resources/${resourceId}`);
}

export function getResourceCapabilities(
  resourceId: string,
): Promise<CapabilityReport> {
  return apiFetch<CapabilityReport>(
    `${UNIVERSAL_BASE}/resources/${resourceId}/capabilities`,
  );
}

export function getResourceInterfaces(resourceId: string): Promise<InterfacesOut> {
  return apiFetch<InterfacesOut>(
    `${UNIVERSAL_BASE}/resources/${resourceId}/interfaces`,
  );
}

export function getResourceTelemetry(
  resourceId: string,
  params: { capability?: string; metricName?: string; limit?: number } = {},
): Promise<TelemetryOut> {
  return apiFetch<TelemetryOut>(
    `${UNIVERSAL_BASE}/resources/${resourceId}/telemetry${query({
      capability: params.capability,
      metric_name: params.metricName,
      limit: params.limit,
    })}`,
  );
}

export function getResourceNetwork(resourceId: string): Promise<ResourceNetworkOut> {
  return apiFetch<ResourceNetworkOut>(
    `${UNIVERSAL_BASE}/resources/${resourceId}/network`,
  );
}

/**
 * Operational state: identity stage, monitoring binding, telemetry freshness and
 * topology evidence, with the blockers that hold the resource where it is.
 */
export function getResourceOperationalState(
  resourceId: string,
): Promise<OperationalStateOut> {
  return apiFetch<OperationalStateOut>(
    `${UNIVERSAL_BASE}/resources/${resourceId}/operational-state`,
  );
}

export function listCloudResources(
  params: ResourceListParams = {},
): Promise<PageOut<UniversalResourceOut>> {
  return apiFetch<PageOut<UniversalResourceOut>>(
    `${UNIVERSAL_BASE}/cloud${query({
      family: params.family,
      site_id: params.siteId,
      provider: params.provider,
      search: params.search,
      limit: params.limit,
      offset: params.offset,
    })}`,
  );
}

export function postCorrelationContext(
  resourceIds: string[],
  includeDependencies = true,
): Promise<CorrelationContextOut> {
  return apiFetch<CorrelationContextOut>(`${UNIVERSAL_BASE}/correlation/context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      resource_ids: resourceIds,
      include_dependencies: includeDependencies,
    }),
  });
}

/* ------------------------------------------------------------------------ *
 * WAN Failover Intelligence — response shapes and calls
 * ------------------------------------------------------------------------ */

export interface FailoverSummaryOut {
  total: number;
  open: number;
  by_status: Record<string, number>;
  open_episodes: FailoverEventOut[];
  terminal_statuses: string[];
}

export interface FailoverEventListOut {
  total: number;
  limit: number;
  offset: number;
  events: FailoverEventOut[];
}

export interface FailoverVerificationResponse {
  event_id: string;
  status: FailoverStatus;
  stored: VerificationOut | null;
  current: VerificationOut;
}

export interface FailoverVerifyResponse {
  event_id: string;
  status: FailoverStatus;
  incident_id: string | null;
  verification: VerificationOut;
}

export interface FailoverEvaluateScopeOut {
  scope: string;
  site_id: string | null;
  skipped: string | null;
  evaluated_at: string;
  policies: number;
  candidate_paths: { link_id: string; name: string; available: boolean }[];
  duplicate_link_names: string[];
  decision: FailoverDecisionOut;
  action: string;
  action_reason: string;
  next_status: string | null;
  path_failure: boolean;
  path_switched: boolean;
  engine_failover_flag: boolean;
  critical: boolean;
  required_samples: number;
  consecutive_breaches: number;
  consecutive_clear: number;
  breaches: EvidenceItemOut[];
  link_from: string | null;
  link_to: string | null;
  route_transitions: FailoverTransitionOut[];
  bgp_transitions: FailoverTransitionOut[];
  link_transitions: FailoverTransitionOut[];
  open_event_id: string | null;
  open_event_status: FailoverStatus | null;
}

export interface FailoverEvaluateOut {
  dry_run: boolean;
  scopes_evaluated: number;
  scopes_truncated: boolean;
  would_open: number;
  scopes: FailoverEvaluateScopeOut[];
}

export interface FailoverSimulationOut {
  scope: string;
  site_id: string | null;
  evaluated_at: string;
  note: string;
  skipped?: string;
  changed: boolean;
  baseline: FailoverDecisionOut;
  simulated: FailoverDecisionOut;
  delta: {
    selected_path: { before: string | null; after: string | null };
    score: { before: number | null; after: number | null };
    failover: { before: boolean; after: boolean };
    paths_lost: string[];
    paths_gained: string[];
    path_switched: boolean;
    no_path_left: boolean;
  };
  unresolved_targets: string[];
  applied: { target: string; changes: Record<string, unknown> }[];
  candidate_paths: {
    link_id: string;
    name: string;
    available: boolean;
    link_type: string | null;
    redundancy_group: string | null;
  }[];
  impact_if_scenario_holds?: number[];
}

export type FailoverPolicyScope = "all_wan" | "site" | "link";

export interface FailoverPolicyOut {
  id: string;
  name: string;
  description: string | null;
  scope: FailoverPolicyScope;
  scope_id: string | null;
  metric: string;
  warning_threshold: number | null;
  critical_threshold: number | null;
  clear_threshold: number | null;
  min_consecutive_samples: number;
  is_active: boolean;
  attributes: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * Create/update payload.
 *
 * Threshold levels are validated by the backend against the metric's direction
 * (latency/jitter/loss/utilisation breach when HIGH; availability when LOW), so
 * a contradictory pair is refused rather than stored.
 */
export interface FailoverPolicyInput {
  name: string;
  metric: string;
  scope?: FailoverPolicyScope;
  scope_id?: string | null;
  description?: string | null;
  warning_threshold?: number | null;
  critical_threshold?: number | null;
  clear_threshold?: number | null;
  min_consecutive_samples?: number;
  is_active?: boolean;
  attributes?: Record<string, unknown>;
}

export interface FailoverPoliciesOut {
  total: number;
  policies: FailoverPolicyOut[];
}


export function getFailoverSummary(): Promise<FailoverSummaryOut> {
  return apiFetch<FailoverSummaryOut>(`${FAILOVER_BASE}/summary`);
}

export function listFailoverEvents(
  params: {
    status?: FailoverStatus;
    openOnly?: boolean;
    siteId?: string | null;
    edgeDeviceId?: string | null;
    since?: string | null;
    until?: string | null;
    limit?: number;
    offset?: number;
  } = {},
): Promise<FailoverEventListOut> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.openOnly) query.set("open_only", "true");
  if (params.siteId) query.set("site_id", params.siteId);
  if (params.edgeDeviceId) query.set("edge_device_id", params.edgeDeviceId);
  if (params.since) query.set("since", params.since);
  if (params.until) query.set("until", params.until);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<FailoverEventListOut>(`${FAILOVER_BASE}/events${suffix}`);
}

export function getFailoverEvent(
  eventId: string,
): Promise<FailoverEventDetailResponse> {
  return apiFetch<FailoverEventDetailResponse>(`${FAILOVER_BASE}/events/${eventId}`);
}

export function getFailoverVerification(
  eventId: string,
): Promise<FailoverVerificationResponse> {
  return apiFetch<FailoverVerificationResponse>(
    `${FAILOVER_BASE}/events/${eventId}/verification`,
  );
}

/**
 * Manually verify recovery against live telemetry.
 *
 * Requires `incidents.write`. `incidentId` only LINKS an existing incident; it
 * never creates one. The recorded result is whatever the checks returned.
 */
export function postFailoverVerify(
  eventId: string,
  incidentId?: string | null,
): Promise<FailoverVerifyResponse> {
  return apiFetch<FailoverVerifyResponse>(`${FAILOVER_BASE}/events/${eventId}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incident_id: incidentId ?? null }),
  });
}

/** Dry run (writes nothing). Omit siteId to sweep the tenant's first scopes. */
export function postFailoverEvaluate(
  siteId?: string | null,
): Promise<FailoverEvaluateOut> {
  return apiFetch<FailoverEvaluateOut>(`${FAILOVER_BASE}/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site_id: siteId ?? null }),
  });
}

/** Counterfactual (read-only). Perturbations are applied to a virtual copy. */
export function postFailoverSimulate(
  siteId: string,
  perturbation: {
    links?: Record<string, Record<string, unknown>>;
    scale?: Record<string, number>;
    weights?: Record<string, number>;
  } = {},
): Promise<FailoverSimulationOut> {
  return apiFetch<FailoverSimulationOut>(`${FAILOVER_BASE}/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site_id: siteId, perturbation }),
  });
}

export function getFailoverBlastRadius(params: {
  resourceId?: string | null;
  linkId?: string | null;
  depth?: number;
  maxNodes?: number;
}): Promise<BlastRadiusOut> {
  const query = new URLSearchParams();
  if (params.resourceId) query.set("resource_id", params.resourceId);
  if (params.linkId) query.set("link_id", params.linkId);
  if (params.depth) query.set("depth", String(params.depth));
  if (params.maxNodes) query.set("max_nodes", String(params.maxNodes));
  return apiFetch<BlastRadiusOut>(
    `${FAILOVER_BASE}/blast-radius?${query.toString()}`,
  );
}

export function listFailoverPolicies(): Promise<FailoverPoliciesOut> {
  return apiFetch<FailoverPoliciesOut>(`${FAILOVER_BASE}/policies`);
}

export function getFailoverPolicy(
  policyId: string,
): Promise<FailoverPolicyOut> {
  return apiFetch<FailoverPolicyOut>(`${FAILOVER_BASE}/policies/${policyId}`);
}

/**
 * Create an operational SLA policy.
 *
 * Requires `monitoring.update`: these thresholds decide what the detector calls
 * a breach, so retuning them is a configuration act, not an operational one.
 * A `422` means the policy cannot be evaluated as written (no level,
 * contradictory levels, a scope_id that is not in this tenant); a `409` means it
 * would duplicate an existing metric+scope. Both rejections carry the offending
 * `field` in the error detail.
 */
export function createFailoverPolicy(
  input: FailoverPolicyInput,
): Promise<FailoverPolicyOut> {
  return apiFetch<FailoverPolicyOut>(`${FAILOVER_BASE}/policies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

/**
 * Partially update a policy. Only the supplied keys change, and an explicit
 * `null` clears a level (`{ clear_threshold: null }`). The merged result is
 * re-validated server-side, so moving one level cannot leave a contradictory
 * pair behind; a rejected update writes nothing.
 */
export function updateFailoverPolicy(
  policyId: string,
  changes: Partial<FailoverPolicyInput>,
): Promise<FailoverPolicyOut> {
  return apiFetch<FailoverPolicyOut>(`${FAILOVER_BASE}/policies/${policyId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
}

/**
 * Soft delete. The policy stops being evaluated immediately and disappears from
 * the list, but the row is retained as an audit record. To disable a policy
 * while keeping it visible, PATCH `{ is_active: false }` instead.
 */
export function deleteFailoverPolicy(
  policyId: string,
): Promise<{ deleted: boolean; policy: FailoverPolicyOut }> {
  return apiFetch<{ deleted: boolean; policy: FailoverPolicyOut }>(
    `${FAILOVER_BASE}/policies/${policyId}`,
    { method: "DELETE" },
  );
}

