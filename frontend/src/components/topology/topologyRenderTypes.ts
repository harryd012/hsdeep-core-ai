/**
 * Render-model types for the live topology (Phase 9.5).
 *
 * These describe what the canvas draws. They are derived from the API payload by
 * the panel using the tested pure modules in `lib/topology`, and they carry the
 * evidence a node/edge must never lose: freshness, recognition status, relationship
 * provenance and the backend's evidence classification.
 */
import type { DeviceState, LinkState } from "../../lib/topology/status";
import type { TrafficDirection, UtilizationBand } from "../../lib/topology/traffic";
import type { VendorVisual } from "../../lib/topology/vendors";

export interface RenderNode {
  id: string;
  name: string;
  /** device | site | monitoring_source */
  kind: string;
  siteId: string | null;
  siteLabel: string | null;
  state: DeviceState;
  visual: VendorVisual;
  ip: string | null;
  /** True when the backend never identified this resource. */
  unidentified: boolean;
  /** Backend recognition status, shown verbatim in the drawer. */
  recognitionStatus: string | null;
  health: string | null;
  operationalState: string | null;
  statusRaw: string | null;
  lifecycleState: string | null;
  freshnessState: string | null;
  freshnessLabel: string;
  ageSeconds: number | null;
  interfaces: { total: number; up: number; down: number; hasTelemetry: boolean } | null;
  rxBps: number | null;
  txBps: number | null;
  relationshipCount: number | null;
  vendorLabel: string;
  deviceType: string | null;
  deviceTypeRaw: string | null;
  model: string | null;
  serial: string | null;
  hostname: string | null;
  /** Present only on monitoring-source nodes. */
  sourceType?: string | null;
  /** Present only on the L3 map's synthetic nodes: a subnet pipe or a next hop. */
  l3Kind?: "subnet" | "next_hop";
}

export interface RenderEdge {
  id: string;
  source: string;
  target: string;
  relationshipType: string;
  unknownRelationshipType: boolean;
  state: LinkState;
  label: string | null;
  direction: TrafficDirection;
  /** Measured throughput, already formatted, or null when not measured. */
  forwardLabel: string | null;
  reverseLabel: string | null;
  capacityLabel: string | null;
  utilization: { label: string | null; band: UtilizationBand | null; source: string | null };
  /** Evidence provenance, exactly as the backend recorded it. */
  evidence: string;
  discoveredBy: string | null;
  classification: string | null;
  confidence: number | null;
  inferred: boolean;
  observedAt: string | null;
  freshnessState: string | null;
  statusRaw: string | null;
  localInterfaceName: string | null;
  remoteInterfaceName: string | null;
  localInterface: {
    name: string;
    operStatus: string | null;
    speedBps: number | null;
    rxBps: number | null;
    txBps: number | null;
    utilizationPct: number | null;
    inErrors: number | null;
    outErrors: number | null;
    discardsIn: number | null;
    discardsOut: number | null;
    vlanId: number | null;
    lastTelemetryAt: string | null;
  } | null;
  remoteInterface: RenderEdge["localInterface"];
  /** Sum of both ends, used for the animation gate and the label colour. */
  totalBps: number | null;
}

export interface RenderSummary {
  devices: number;
  sites: number;
  links: number;
  unidentified: number;
  online: number;
  warning: number;
  offline: number;
  unknown: number;
  stale: number;
  maintenance: number;
  interfacesTotal: number;
  interfacesUp: number;
  interfacesDown: number;
  trafficMeasured: boolean;
  rxLabel: string | null;
  txLabel: string | null;
}

export interface TopologyGraphData {
  nodes: RenderNode[];
  edges: RenderEdge[];
}

export interface RenderModel extends TopologyGraphData {
  summary: RenderSummary;
  freshnessState: string | null;
  freshnessLabel: string;
  freshnessAgeSeconds: number | null;
  devicesReporting: number;
  generatedAt: string;
  graphStatus: string | null;
  limitations: string[];
  scopeHiddenNodes: number;
  scopeHiddenEdges: number;
  siteOptions: { id: string; label: string }[];
}

/** Everything a filter chip needs, derived from the data actually present. */
export interface FilterOptions {
  states: DeviceState[];
  vendors: { key: string; label: string }[];
  deviceTypes: string[];
  relationshipTypes: string[];
  evidenceSources: string[];
  classifications: string[];
}

export interface TopologyFilters {
  states: DeviceState[];
  vendors: string[];
  deviceTypes: string[];
  relationshipTypes: string[];
  evidence: ("observed" | "inferred" | "manual" | "api" | "passive" | "active" | "unclassified")[];
  /** Restrict to resources the backend never classified (recognition status). */
  unidentifiedOnly: boolean;
}