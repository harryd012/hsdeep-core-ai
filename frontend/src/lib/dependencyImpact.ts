/**
 * dependencyImpact.ts — dependency + impact contract for ONE resource.
 *
 * Owns `GET /api/infrastructure/dependencies/{resource_id}`, the read model
 * backed by `DependencyImpactService` over the canonical `resource_relationships`
 * + `devices` rows of the caller's tenant.
 *
 * It is deliberately NOT the legacy `/api/assets/...` graph
 * (`AssetRelationship`): this contract is the canonical recorded evidence, and
 * every hop carries its own provenance — `discovered_by`, `confidence`,
 * `is_inferred`, `freshness`, `resolution`, `conflict`, `evidence_sources` — so a
 * rendered dependency can be justified rather than asserted.
 *
 * Tenant isolation: the route resolves the tenant from the authenticated
 * principal (`get_tenant_id`), so no tenant id is sent or trusted here.
 *
 * Nothing is derived in the browser. Every count below is the backend's own
 * summary, and an unavailable value stays `null` rather than becoming a 0.
 */
import { apiFetch } from "./api";

/** operational | physical | monitoring | unclassified — the backend's classification. */
export type HopKind = "operational" | "physical" | "monitoring" | "unclassified" | string;

export interface DependencyHop {
  relationship_id: string;
  from_resource_id: string;
  to_resource_id: string;
  /** Null when the far endpoint is not a named device in this tenant. */
  from_name: string | null;
  to_name: string | null;
  relationship_type: string;
  relationship_subtype: string | null;
  kind: HopKind;
  discovered_by: string | null;
  confidence: number | null;
  is_inferred: boolean;
  status: string | null;
  /** fresh | stale | unknown (opaque: rendered verbatim). */
  freshness: string | null;
  /** Whether the recorded far endpoint resolved to a real resource. */
  resolution: string | null;
  conflict: boolean | null;
  evidence_sources: string[] | null;
  local_interface: string | null;
  remote_interface: string | null;
  depth: number;
}

/**
 * The backend's own aggregates for one resource. `unresolved`, `stale`,
 * `conflicts` and `cycles_detected` are exactly the fields the impact panel
 * reports — none of them is recomputed from the hop list to avoid a second,
 * disagreement-prone source of truth.
 */
export interface DependencyImpactSummary {
  direct_dependencies: number;
  direct_dependents: number;
  dependencies_total: number;
  dependents_total: number;
  /** Blast radius: every hop that depends on this resource, at any depth. */
  impacted_total: number;
  evidence_records_seen: number;
  unresolved: number;
  unresolved_evidence_total: number;
  stale: number;
  conflicts: number;
  cycles_detected: number;
  /** True when `max_nodes` cut the traversal short — never hidden. */
  truncated: boolean;
  /** True when the traversal stopped at `max_depth`. */
  max_depth_hit: boolean;
}

export interface DependencyImpactOut {
  tenant_id: string;
  generated_at: string;
  max_depth: number;
  max_nodes: number;
  resource: {
    id: string;
    name: string | null;
    device_type: string | null;
    status: string | null;
  };
  /** Upstream — what this resource depends on (the root-cause direction). */
  dependencies: DependencyHop[];
  dependents: DependencyHop[];
  /** Downstream — what a failure here affects (the blast radius). */
  impact: DependencyHop[];
  summary: DependencyImpactSummary;
  evidence_sources: Record<string, unknown>;
  freshness: { fresh?: number; stale?: number; unknown?: number; worst?: string };
  unresolved_evidence: Record<string, unknown>[];
  /** The backend's own reasons for an incomplete answer. Rendered verbatim. */
  limitations: string[];
}

/**
 * Dependencies + impact for ONE resource, in a SINGLE bounded request.
 *
 * `max_depth` / `max_nodes` are the service's own bounds (defaults 3 / 200, hard
 * ceiling 6 / 1000). A command-centre load deliberately asks for the smallest
 * useful window so it never walks the estate and never fans out per node.
 */
export function getResourceDependencies(
  resourceId: string,
  options: { maxDepth?: number; maxNodes?: number } = {},
): Promise<DependencyImpactOut> {
  const qs = new URLSearchParams();
  if (options.maxDepth !== undefined) qs.set("max_depth", String(options.maxDepth));
  if (options.maxNodes !== undefined) qs.set("max_nodes", String(options.maxNodes));
  const suffix = qs.toString();
  return apiFetch<DependencyImpactOut>(
    `/api/infrastructure/dependencies/${encodeURIComponent(resourceId)}${suffix ? `?${suffix}` : ""}`,
  );
}
