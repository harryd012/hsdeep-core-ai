/**
 * L3 render model: joins the `/topology/l3` structure onto the live topology's
 * device render nodes, so the L3 map shows the same device state, icon and name
 * as every other view. Subnet pipes and next-hop diamonds become synthetic
 * render nodes; L3 links become render edges whose "ports" are addresses.
 */
import { buildL3Graph, l3DeviceRows, l3SyntheticRows } from "../../lib/topology/l3";
import type { TopologyL3 } from "../../lib/topologyApi";
import type { RenderEdge, RenderNode } from "./topologyRenderTypes";
import { displayName } from "./TopologyTreeCanvas";

export interface L3RenderGraph {
  /** Every filtered device (linked or not) plus the synthetic L3 nodes. */
  nodes: RenderNode[];
  edges: RenderEdge[];
  rootOrder: string[];
  hoverRows: (id: string) => [string, string][] | null;
}

function syntheticNode(node: TopologyL3["nodes"][number]): RenderNode {
  return {
    id: node.id,
    name: node.label,
    kind: node.kind,
    siteId: node.site_id,
    siteLabel: null,
    state: "UNKNOWN",
    visual: { vendorKey: "unknown", vendorLabel: "", iconKey: "unknown", deviceType: null, source: "generic" },
    ip: node.address,
    unidentified: false,
    recognitionStatus: null,
    health: null,
    operationalState: null,
    statusRaw: null,
    lifecycleState: null,
    freshnessState: null,
    freshnessLabel: "",
    ageSeconds: null,
    interfaces: null,
    rxBps: null,
    txBps: null,
    relationshipCount: null,
    vendorLabel: "",
    deviceType: null,
    deviceTypeRaw: null,
    model: null,
    serial: null,
    hostname: null,
    l3Kind: node.kind,
  };
}

function l3Edge(edge: ReturnType<typeof buildL3Graph>["edges"][number]): RenderEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    relationshipType: edge.kind === "route" ? "l3_route" : "l3_subnet",
    unknownRelationshipType: false,
    // Reachability is not measured on L3 links; they are drawn as present.
    state: "UP",
    label: edge.title,
    direction: "none",
    forwardLabel: null,
    reverseLabel: null,
    capacityLabel: null,
    utilization: { label: null, band: null, source: null },
    evidence: edge.kind === "route" ? "routing_table" : "interface_addresses",
    discoveredBy: null,
    classification: null,
    confidence: null,
    inferred: false,
    observedAt: null,
    freshnessState: null,
    statusRaw: null,
    localInterfaceName: edge.sourceLabel,
    remoteInterfaceName: edge.targetLabel,
    localInterface: null,
    remoteInterface: null,
    totalBps: null,
  };
}

export function buildL3RenderGraph(payload: TopologyL3, devices: readonly RenderNode[]): L3RenderGraph {
  const deviceNodes = devices.filter((node) => node.kind !== "site" && node.kind !== "monitoring_source");
  const known = new Set(deviceNodes.map((node) => node.id));
  const graph = buildL3Graph(payload, known);
  const synthetic = graph.synthetic.map(syntheticNode);
  const byId = new Map([...deviceNodes, ...synthetic].map((node) => [node.id, node]));
  const syntheticById = new Map(graph.synthetic.map((node) => [node.id, node]));
  const nameOf = (id: string) => {
    const node = byId.get(id);
    return node ? displayName(node) : null;
  };
  return {
    nodes: [...deviceNodes, ...synthetic],
    edges: graph.edges.map(l3Edge),
    rootOrder: graph.rootOrder,
    hoverRows: (id) => {
      const node = syntheticById.get(id);
      if (node) return l3SyntheticRows(node);
      return known.has(id) ? l3DeviceRows(payload.devices[id], nameOf) : null;
    },
  };
}
