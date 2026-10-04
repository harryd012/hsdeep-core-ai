/**
 * liveInfraIntelModel.ts — semantic model for the Command Center's
 * LIVE INFRASTRUCTURE INTELLIGENCE zone.
 *
 * Same contract as `dashboardDataAdapter.ts`: this module is PURE. It owns no
 * fetching and invents nothing. Every field is either
 *   (a) read from the existing dashboard data model (`ocData`), or
 *   (b) read from an EXISTING backend response (dependency/impact, topology
 *       graph, HSI-GNCE correlation context) passed in.
 *
 * Two rules that are load-bearing for this zone:
 *   - `null` means "the backend did not report this"; it is rendered as
 *     unavailable and never collapsed into a measured 0.
 *   - Nothing is derived that the backend already aggregates (see
 *     `DependencyImpactSummary`), so the dashboard can never disagree with the
 *     topology workspace about the same fact.
 */
import type { DependencyHop, DependencyImpactOut } from "@/lib/dependencyImpact";
import type { TopologyGraph } from "@/lib/topologyApi";
import type {
  ActivityItem,
  AttentionEvidence,
  AttentionIncidentRow,
  DashboardData,
} from "./dashboardModels";

/* ---------------------------------------------------------------------------
 * 1. Focus — which single resource this zone is about
 * ------------------------------------------------------------------------ */

/**
 * The resource the zone is pinned to. `reason` is static copy explaining the
 * selection, never a measured value.
 */
export interface InfraFocus {
  id: string;
  name: string;
  type: string | null;
  state: string;
  /** How the operator should read why this resource is in focus. */
  reason: string;
  /** The attention incident behind the focus, when there is one. */
  incident: AttentionIncidentRow | null;
}

/**
 * Choose the focus from EXISTING dashboard data, in the documented priority
 * order. Returns null when the estate reports no active resource — the zone then
 * shows its truthful empty state rather than a placeholder node.
 */
export function selectFocus(nocData: DashboardData): InfraFocus | null {
  const devices = nocData.infrastructure.devices;
  const incidents = nocData.attention.incidents;

  // 1. An active critical incident whose asset resolves to a known device.
  //    (`AttentionIncidentRow.asset` is the device name, so the id is resolved
  //    against the device list the dashboard already holds.)
  const critical = incidents.find(
    (row) => row.severity?.toLowerCase() === "critical" && row.asset,
  );
  if (critical?.asset) {
    const match = devices.find((device) => device.name === critical.asset);
    if (match) {
      return {
        id: match.id,
        name: match.name,
        type: match.type,
        state: match.status,
        reason: "Active critical incident",
        incident: critical,
      };
    }
  }

  // 2. Highest-priority degraded device (already ordered by the adapter).
  const degraded = nocData.attention.degradedDevices[0];
  if (degraded) {
    return {
      id: degraded.id,
      name: degraded.name,
      type: degraded.type,
      state: degraded.status,
      reason: "Highest-priority degraded device",
      incident: null,
    };
  }

  // 3. Any open incident that resolves to a device, so the zone can still give
  //    context without inventing a resource.
  const withAsset = incidents.find((row) => row.asset);
  if (withAsset?.asset) {
    const match = devices.find((device) => device.name === withAsset.asset);
    if (match) {
      return {
        id: match.id,
        name: match.name,
        type: match.type,
        state: match.status,
        reason: "Open incident resource",
        incident: withAsset,
      };
    }
  }

  // 4. No authoritative resource: the caller renders the empty state.
  return null;
}

/* ---------------------------------------------------------------------------
 * 2. Relationship context (LEFT panel)
 * ------------------------------------------------------------------------ */

/** One recorded relationship, as the canonical graph reports it. */
export interface InfraRelationRow {
  id: string;
  direction: "upstream" | "downstream";
  fromName: string;
  relationshipType: string;
  toName: string | null;
  depth: number;
  kind: string;
  discoveredBy: string | null;
  confidence: number | null;
  inferred: boolean;
  freshness: string | null;
  /** False when the far endpoint did not resolve to a real resource. */
  resolved: boolean;
}

const MAX_RELATION_ROWS = 10;

function toRow(hop: DependencyHop, direction: InfraRelationRow["direction"]): InfraRelationRow {
  return {
    id: hop.relationship_id,
    direction,
    fromName: hop.from_name ?? "Unresolved resource",
    relationshipType: hop.relationship_type,
    toName: hop.to_name,
    depth: hop.depth,
    kind: hop.kind,
    discoveredBy: hop.discovered_by,
    confidence: hop.confidence,
    inferred: hop.is_inferred,
    freshness: hop.freshness,
    resolved: hop.resolution === null || hop.resolution === "resolved",
  };
}

/**
 * The focus resource's recorded relationships, upstream first (what it depends
 * on) then downstream (what depends on it). Bounded: the dashboard shows the
 * nearest hops only, and `impactSummary.truncated` reports when the backend had
 * more than the window.
 */
export function buildRelationRows(impact: DependencyImpactOut | undefined): InfraRelationRow[] {
  if (!impact) return [];
  const seen = new Set<string>();
  const rows: InfraRelationRow[] = [];
  const take = (hops: DependencyHop[], direction: InfraRelationRow["direction"]) => {
    for (const hop of [...hops].sort((a, b) => a.depth - b.depth)) {
      if (rows.length >= MAX_RELATION_ROWS) return;
      if (seen.has(hop.relationship_id)) continue;
      seen.add(hop.relationship_id);
      rows.push(toRow(hop, direction));
    }
  };
  take(impact.dependencies ?? [], "upstream");
  take(impact.impact ?? [], "downstream");
  return rows;
}

/* ---------------------------------------------------------------------------
 * 3. Impact (RIGHT panel)
 * ------------------------------------------------------------------------ */

/**
 * The backend's own impact aggregates, reshaped for display. Every number is a
 * field of `DependencyImpactSummary` — none is recomputed here.
 */
export interface InfraImpact {
  /** Direct blast radius: resources whose recorded hop depth is 1. */
  direct: number;
  /** Everything beyond the first hop that the backend traversed. */
  transitive: number;
  impactedTotal: number;
  directDependencies: number;
  dependenciesTotal: number;
  unresolved: number;
  stale: number;
  conflicts: number;
  cycles: number;
}

export function buildImpact(impact: DependencyImpactOut | undefined): InfraImpact | null {
  if (!impact) return null;
  const s = impact.summary;
  const direct = s.direct_dependents;
  return {
    direct,
    transitive: Math.max(0, s.impacted_total - direct),
    impactedTotal: s.impacted_total,
    directDependencies: s.direct_dependencies,
    dependenciesTotal: s.dependencies_total,
    unresolved: s.unresolved,
    stale: s.stale,
    conflicts: s.conflicts,
    cycles: s.cycles_detected,
  };
}

/** One step of the recorded dependency chain, nearest hop first. */
export interface InfraPathStep {
  depth: number;
  fromName: string;
  relationshipType: string;
  toName: string | null;
  evidence: string | null;
  inferred: boolean;
}

const MAX_PATH_STEPS = 6;

/**
 * The dependency path for the focus resource, built ONLY from the recorded
 * upstream hops and ordered by the depth the backend assigned. No hop is
 * synthesised: a chain the graph does not contain is simply short.
 */
export function buildPath(impact: DependencyImpactOut | undefined): InfraPathStep[] {
  if (!impact?.dependencies?.length) return [];
  return [...impact.dependencies]
    .sort((a, b) => a.depth - b.depth)
    .slice(0, MAX_PATH_STEPS)
    .map((hop) => ({
      depth: hop.depth,
      fromName: hop.from_name ?? "Unresolved resource",
      relationshipType: hop.relationship_type,
      toName: hop.to_name,
      evidence: hop.discovered_by,
      inferred: hop.is_inferred,
    }));
}

/* ---------------------------------------------------------------------------
 * 4. Correlation (RIGHT panel) — HSI-GNCE + the focused incident
 * ------------------------------------------------------------------------ */

/**
 * The states the zone may show. UNAVAILABLE and NO DATA are kept apart: "we
 * could not ask" and "the backend said nothing correlated" are different facts,
 * and neither is a zero.
 */
export type InfraCorrelationState = "loading" | "unavailable" | "no_data" | "correlated";

export interface InfraCorrelation {
  state: InfraCorrelationState;
  /** How many resources the correlation actually looked at. */
  resourceCount: number | null;
  /** Correlation groups the backend produced. */
  groupCount: number | null;
  basis: string[];
  limitations: string[];
  /**
   * The recorded root-cause summary of the focused incident
   * (`AttentionIncidentRow.rca`). Null means none was recorded — the zone then
   * states "Not established" and never claims an inference.
   */
  rootCause: string | null;
  /** True when the focused incident carries more than one member alert. */
  correlated: boolean;
  memberAlerts: number | null;
}

export function buildCorrelation(args: {
  focus: InfraFocus | null;
  loading: boolean;
  failed: boolean;
  context:
    | {
        resources?: Record<string, unknown>[];
        groups?: Record<string, unknown>[];
        correlation_basis?: string[];
        limitations?: { reason: string; detail?: string }[];
      }
    | undefined;
}): InfraCorrelation {
  const incident = args.focus?.incident ?? null;
  const common = {
    rootCause: incident?.rca ?? null,
    correlated: incident?.correlated ?? false,
    memberAlerts: incident ? incident.memberAlerts : null,
  };

  if (args.loading) {
    return { state: "loading", resourceCount: null, groupCount: null, basis: [], limitations: [], ...common };
  }
  if (args.failed || !args.context) {
    return { state: "unavailable", resourceCount: null, groupCount: null, basis: [], limitations: [], ...common };
  }

  const resources = args.context.resources ?? [];
  const groups = args.context.groups ?? [];
  const basis = args.context.correlation_basis ?? [];
  const limitations = (args.context.limitations ?? []).map((item) =>
    item.detail ? `${item.reason} — ${item.detail}` : item.reason,
  );

  return {
    state: groups.length > 0 || resources.length > 0 ? "correlated" : "no_data",
    resourceCount: resources.length,
    groupCount: groups.length,
    basis,
    limitations,
    ...common,
  };
}

/* ---------------------------------------------------------------------------
 * 5. Evidence strip — telemetry, graph evidence, collection, activity
 * ------------------------------------------------------------------------ */

/** Reused from the existing dashboard data (no request). */
export interface InfraTelemetry {
  /** Sensors the dashboard already reports as healthy, or null. */
  healthy: number | null;
  /** Estate sensor total as the API reported it, or null. */
  total: number | null;
  /** Most recent sensor observation the dashboard holds, or null. */
  newestObservationAt: string | null;
}

export function buildTelemetry(nocData: DashboardData): InfraTelemetry {
  const sensors = nocData.infrastructure.sensors;
  let newest: string | null = null;
  for (const sensor of sensors) {
    if (!sensor.lastValueAt) continue;
    if (!newest || sensor.lastValueAt > newest) newest = sensor.lastValueAt;
  }
  return {
    healthy: nocData.operationalHealth.sensorsHealthy.value,
    total: nocData.infrastructure.summary?.sensors ?? null,
    newestObservationAt: newest,
  };
}

/** The canonical graph's own counts + status. Every value is the API's. */
export interface InfraGraphEvidence {
  status: string;
  relationships: number | null;
  devices: number | null;
  nodes: number | null;
  /** Edges the backend flagged `is_inferred`, counted from the same payload. */
  inferred: number | null;
  limitations: string[];
}

export function buildGraphEvidence(graph: TopologyGraph | undefined): InfraGraphEvidence | null {
  if (!graph) return null;
  const edges = graph.edges ?? [];
  return {
    status: graph.status,
    relationships: graph.summary?.edges_total ?? null,
    devices: graph.summary?.devices_total ?? null,
    nodes: graph.summary?.nodes_total ?? null,
    inferred: edges.length ? edges.filter((edge) => edge.is_inferred).length : null,
    limitations: graph.limitations ?? [],
  };
}

/**
 * Collection pipeline health — the authoritative PHASE 3.1 contract, already
 * computed by `/api/incidents/collector-health` and already present in the
 * dashboard data. Reused as-is so the zone can never disagree with Attention
 * Required, and UNKNOWN is never reported as failure.
 */
export type InfraCollection = AttentionEvidence;

export function buildCollection(evidence: AttentionEvidence): InfraCollection {
  return evidence;
}

/** Recent activity — the dashboard's existing real-events feed, bounded. */
const MAX_ACTIVITY_ROWS = 3;

export function buildActivityRows(nocData: DashboardData): ActivityItem[] {
  return nocData.activity.slice(0, MAX_ACTIVITY_ROWS);
}

/* ---------------------------------------------------------------------------
 * 6. The zone view model
 * ------------------------------------------------------------------------ */

export interface LiveInfraIntel {
  state: "loading" | "success" | "empty" | "error";
  focus: InfraFocus | null;
  relations: InfraRelationRow[];
  impact: InfraImpact | null;
  path: InfraPathStep[];
  correlation: InfraCorrelation;
  graph: InfraGraphEvidence | null;
  telemetry: InfraTelemetry;
  collection: InfraCollection;
  activity: ActivityItem[];
  /** True when a focus exists but its dependency read failed or is pending. */
  impactUnavailable: boolean;
}


