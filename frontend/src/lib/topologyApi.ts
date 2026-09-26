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
export function getTopologyGraph(tenantId: string = DEFAULT_TENANT_ID): Promise<TopologyGraph> {
  return apiFetch<TopologyGraph>(`/api/infrastructure/topology/graph?${withTenant(tenantId).toString()}`);
}

