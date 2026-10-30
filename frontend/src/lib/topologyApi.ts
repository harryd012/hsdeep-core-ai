/**
 * Infrastructure topology API -- the node/edge graph read model.
 *
 * Transport comes from ./api; this module owns the graph contract only.
 * Every edge in the response comes from a real resource_relationships row:
 * when none exist the backend reports status "not_discovered" rather than
 * inventing edges from subnets, hostnames or device types.
 */
import { apiFetch, DEFAULT_TENANT_ID, withTenant } from "./api";

// ---------------------------------------------------------------------------
// Topology graph (Phase 3) — nodes + edges from real rows only
// ---------------------------------------------------------------------------

export type TopologyHealth = "healthy" | "warning" | "critical" | "unknown";
export type TopologyGraphStatus = "ready" | "partial" | "not_discovered";
export type TopologyNodeType = "device" | "site" | "monitoring_source";

export interface TopologyGraphNode {
  id: string;
  type: TopologyNodeType;
  name: string;
  /** Canonical Phase 2 health bucket. Null for site/source nodes, whose health
   *  the canonical overview owns. */
  health: TopologyHealth | null;
  status: string | null;
  site_id: string | null;
  /** Canonical device type (`unclassified` when unrecognised). */
  device_type?: string;
  /** Raw stored spelling, kept for provenance. */
  device_type_raw?: string | null;
  is_network_device?: boolean;
  /** Monitoring-source nodes only. */
  source_type?: string;
  /** Site nodes only. */
  code?: string | null;
  region?: string | null;
  site_type?: string | null;
  monitoring_source?: { id: string; name: string; source_type: string } | null;
  metadata?: Record<string, unknown>;
  counts?: Record<string, number>;
  /** Site nodes only. Only ever present when real coordinates exist. */
  latitude?: number | null;
  longitude?: number | null;
  has_coordinates?: boolean;
}

export interface TopologyGraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type: string;
  /** Which verified source produced this edge. */
  evidence: string;
  /** Real NOT NULL column, returned as stored — never synthesised. */
  confidence?: number | null;
  is_inferred?: boolean;
  discovered_by?: string;
  /** Operator- or collector-supplied label for the link, when recorded. */
  label?: string | null;
  /** True when the recorded type is outside the model's declared vocabulary. */
  unknown_relationship_type?: boolean;
  status?: string | null;
  metadata?: Record<string, unknown>;
}

export interface TopologyGraph {
  tenant_id: string;
  generated_at: string;
  status: TopologyGraphStatus;
  nodes: TopologyGraphNode[];
  edges: TopologyGraphEdge[];
  summary: {
    nodes_total: number;
    edges_total: number;
    devices_total: number;
    sites_total: number;
    monitoring_sources_total: number;
    interfaces_total: number;
    sensors_total: number;
  };
  /** Why the graph is incomplete. Never empty when the graph is empty, so the
   *  UI can explain rather than show a blank canvas. */
  limitations: string[];
}

/**
 * Node/edge topology graph. Every edge comes from a real
 * `resource_relationships` row; when none exist the response reports
 * `status: "not_discovered"` rather than inventing edges from subnets,
 * hostnames or device types.
 */
export function getTopologyGraph(
  tenantId: string = DEFAULT_TENANT_ID,
  timeoutMs?: number,
): Promise<TopologyGraph> {
  return apiFetch<TopologyGraph>(
    `/api/infrastructure/topology/graph?${withTenant(tenantId).toString()}`,
    undefined,
    timeoutMs,
  );
}

// ---------------------------------------------------------------------------
// Topology quick summary (Task 3 — fast, count-only endpoint)
// ---------------------------------------------------------------------------

export interface TopologyQuickSummary {
  tenant_id: string;
  generated_at: string;
  devices_total: number;
  sites_total: number;
  monitoring_sources_total: number;
  relationships_total: number;
  /** Devices with at least one recorded relationship or topology link. */
  devices_linked?: number;
  interfaces_total: number;
  sensors_total: number;
  by_health: Record<string, number>;
  /** "ok" | "empty" */
  status: string;
}

/**
 * Fast aggregate topology summary — 6 COUNT queries on the backend,
 * no row materialisation. Use this to show stat cards immediately while
 * the full graph is still loading.
 */
export function getTopologySummary(
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<TopologyQuickSummary> {
  return apiFetch<TopologyQuickSummary>(
    `/api/infrastructure/topology/summary?${withTenant(tenantId).toString()}`,
  );
}

// ---------------------------------------------------------------------------
// Live operational topology (Phase 9.5) — the SAME graph plus interface and
// telemetry facts, composed by the backend in ONE response so the UI never
// fans out into a per-device interface/telemetry request.
//
// Every added value is a stored column, and anything never measured is `null`.
// The UI must render an absent value as unavailable, never as 0.
// ---------------------------------------------------------------------------

export interface LiveInterface {
  id: string;
  name: string;
  interface_type: string | null;
  oper_status: string | null;
  admin_status: string | null;
  vlan_id: number | null;
  mac_address: string | null;
  speed_bps: number | null;
  rx_bps: number | null;
  tx_bps: number | null;
  utilization_pct: number | null;
  in_errors: number | null;
  out_errors: number | null;
  discards_in: number | null;
  discards_out: number | null;
  last_telemetry_at: string | null;
}

export interface LiveInterfaceSummary {
  total: number;
  up: number;
  down: number;
  /** False means "never measured": the totals below are then null, not 0. */
  has_telemetry: boolean;
  rx_bps: number | null;
  tx_bps: number | null;
  last_telemetry_at: string | null;
}

export interface LiveFreshness {
  /** fresh | aging | stale | expired | never_collected | unavailable */
  state: string;
  age_seconds: number | null;
  last_telemetry_at: string | null;
}

export interface LiveRecognition {
  /** identified | unknown — reuse of the platform's existing rule. */
  status: string;
  device_type: string | null;
}

export interface LiveIdentity {
  vendor: string | null;
  model: string | null;
  device_type: string | null;
  device_type_raw: string | null;
  ip: string | null;
  hostname: string | null;
  serial: string | null;
}

export interface LiveNodeBlock {
  /** up | down | degraded | unknown (canonical operational state). */
  operational_state: string;
  status_raw: string | null;
  lifecycle_state: string | null;
  site_id: string | null;
  recognition: LiveRecognition;
  identity: LiveIdentity;
  freshness: LiveFreshness;
  interfaces: LiveInterfaceSummary;
  relationship_count: number;
}

export interface TopologyLiveNode extends TopologyGraphNode {
  /** Null for site and monitoring-source nodes. */
  live: LiveNodeBlock | null;
}

export interface LiveLink {
  capacity_bps: number | null;
  utilization_pct: number | null;
  /** recorded | computed | null — a derived percentage is labelled as derived. */
  utilization_source: string | null;
  has_telemetry: boolean;
  rx_bps: number | null;
  tx_bps: number | null;
  observed_at: string | null;
  /** Freshness of the link's telemetry, classified by the backend. */
  freshness: LiveFreshness;
}

export interface LiveEdgeBlock {
  local_interface_name: string | null;
  remote_interface_name: string | null;
  local_interface: LiveInterface | null;
  remote_interface: LiveInterface | null;
  link: LiveLink;
  status_raw: string | null;
  /** active | passive | api | null — never re-derived in the frontend. */
  classification: string | null;
  is_inferred: boolean;
}

export interface TopologyLiveEdge extends TopologyGraphEdge {
  live: LiveEdgeBlock;
}

export interface TopologyLiveSummary {
  devices_total: number;
  sites_total: number;
  monitoring_sources_total: number;
  links_total: number;
  unidentified_devices_total: number;
  by_health: Record<string, number>;
  by_operational_state: Record<string, number>;
  by_freshness: Record<string, number>;
  interfaces_total: number;
  interfaces_up: number;
  interfaces_down: number;
  traffic_measured: boolean;
  rx_bps_total: number | null;
  tx_bps_total: number | null;
}

export interface TopologyLive {
  tenant_id: string;
  generated_at: string;
  status: TopologyGraphStatus | string | null;
  scope: {
    site_id: string | null;
    filtered: boolean;
    nodes_hidden: number;
    edges_hidden: number;
  };
  summary: TopologyLiveSummary;
  telemetry_freshness: LiveFreshness & { devices_reporting: number };
  nodes: TopologyLiveNode[];
  edges: TopologyLiveEdge[];
  limitations: string[];
}

/**
 * Live operational topology: the graph plus interface/telemetry facts, in one
 * response. Optionally bounded to a single site.
 */
export function getLiveTopology(
  options: { siteId?: string | null; tenantId?: string } = {}
): Promise<TopologyLive> {
  const tenantId = options.tenantId ?? DEFAULT_TENANT_ID;
  const query = withTenant(tenantId);
  if (options.siteId) query.set("site_id", options.siteId);
  return apiFetch<TopologyLive>(`/api/infrastructure/topology/graph/live?${query.toString()}`);
}


// ---------------------------------------------------------------------------
// L3 topology - /api/infrastructure/topology/l3
// ---------------------------------------------------------------------------

/** A synthetic L3 node: a subnet "pipe" (3+ devices) or a next-hop diamond. */
export interface L3Node {
  id: string;
  kind: "subnet" | "next_hop";
  label: string;
  vrf: string;
  site_id: string | null;
  network: string | null;
  member_count: number | null;
  address: string | null;
  route_count: number | null;
}

export interface L3Edge {
  id: string;
  source: string;
  target: string;
  kind: "subnet" | "route";
  network: string | null;
  vrf: string;
  source_address: string | null;
  source_interface: string | null;
  target_address: string | null;
  target_interface: string | null;
  destinations: string[];
}

export interface L3Address {
  address: string;
  prefix_length: number;
  network: string;
  interface: string | null;
  vrf: string;
  is_primary: boolean;
}

export interface L3Route {
  destination: string;
  next_hop: string | null;
  interface: string | null;
  protocol: string;
  metric: number | null;
  vrf: string;
  next_hop_node: string | null;
}

export interface L3Device {
  name: string | null;
  device_type: string | null;
  addresses: L3Address[];
  static_routes: L3Route[];
  default_route: L3Route | null;
  routes_by_protocol: Record<string, number>;
}

export interface TopologyL3 {
  nodes: L3Node[];
  edges: L3Edge[];
  /** Keyed by the live graph's device node id (`device:<uuid>`). */
  devices: Record<string, L3Device>;
  /** Preferred roots: default-route exits, most used first. */
  roots: string[];
  unlinked: string[];
  coverage: {
    devices_total: number;
    devices_with_addresses: number;
    devices_with_routes: number;
    subnets: number;
    providers: string[];
  };
  site_id: string | null;
  generated_at: string;
}

export function getL3Topology(
  options: { siteId?: string | null; tenantId?: string } = {}
): Promise<TopologyL3> {
  const tenantId = options.tenantId ?? DEFAULT_TENANT_ID;
  const query = withTenant(tenantId);
  if (options.siteId) query.set("site_id", options.siteId);
  return apiFetch<TopologyL3>(`/api/infrastructure/topology/l3?${query.toString()}`);
}
