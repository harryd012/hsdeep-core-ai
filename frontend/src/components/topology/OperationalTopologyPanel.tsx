"use client";

/**
 * OperationalTopologyPanel — the live Site → Device → Interface topology (9.5).
 *
 * Responsibilities are deliberately narrow: fetch ONE composed read model, turn
 * it into a render model with the tested `lib/topology` primitives, and keep view
 * state (zoom, pan, layout, selection, filters, search) alive across refreshes.
 *
 * It invents nothing. When the backend reports no relationships, no interfaces or
 * no telemetry, the panel says so instead of drawing something plausible.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronsDownUp,
  ChevronsUpDown,
  ListTree,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";

import { getLiveTopology, type TopologyLive } from "../../lib/topologyApi";
import { buildEmptyState, isGraphEmpty } from "../../lib/topology/emptyState";
import {
  fitViewport,
  layoutNodes,
  linkedNodeIds,
  type ViewKind,
  type Viewport,
} from "../../lib/topology/layout";
import {
  deviceState,
  freshnessLabel,
  isFreshnessCurrent,
  linkState,
  type DeviceState,
} from "../../lib/topology/status";
import {
  animationBudget,
  formatBps,
  formatPercent,
  shouldAnimate,
  trafficDirection,
  utilizationBand,
} from "../../lib/topology/traffic";
import {
  LARGE_TREE_THRESHOLD,
  buildTreeLayout,
  collapseBeyond,
  type TreeLayout,
} from "../../lib/topology/tree";
import { isUnknownResource, resolveVendorVisual } from "../../lib/topology/vendors";
import TopologyCanvas from "./TopologyCanvas";
import TopologyDetailDrawer from "./TopologyDetailDrawer";
import TopologyEmptyStatePanel from "./TopologyEmptyState";
import {
  TopologyCanvasControls,
  TopologyLegend,
  TopologyMinimap,
} from "./TopologyOverlays";
import TopologyToolbar from "./TopologyToolbar";
import TopologyTreeCanvas from "./TopologyTreeCanvas";
import { TopologyStatusLine, TopologyTreeLegend, UnconnectedRail } from "./TopologyTreeChrome";
import type {
  FilterOptions,
  RenderEdge,
  RenderModel,
  RenderNode,
  TopologyFilters,
} from "./topologyRenderTypes";

const REFRESH_OPTIONS = [
  { label: "Off", seconds: 0 },
  { label: "30s", seconds: 30 },
  { label: "1m", seconds: 60 },
  { label: "5m", seconds: 300 },
];

const EMPTY_FILTERS: TopologyFilters = {
  states: [],
  vendors: [],
  deviceTypes: [],
  relationshipTypes: [],
  evidence: [],
  unidentifiedOnly: false,
};

const IDENTITY_VIEWPORT: Viewport = { scale: 1, offsetX: 60, offsetY: 60 };

const NO_COLLAPSE: ReadonlySet<string> = new Set();
const PROBLEM_STATES: DeviceState[] = ["WARNING", "OFFLINE"];

/** Below this zoom tree labels are unreadable: anchor top-left instead of shrinking further. */
const TREE_MIN_FIT_SCALE = 0.45;

function fitTree(points: { x: number; y: number }[], size: { width: number; height: number }): Viewport | null {
  const fitted = fitViewport(points, size);
  if (!fitted || fitted.scale >= TREE_MIN_FIT_SCALE) return fitted;
  const minX = Math.min(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y));
  return {
    scale: TREE_MIN_FIT_SCALE,
    offsetX: 60 - minX * TREE_MIN_FIT_SCALE,
    offsetY: 48 - minY * TREE_MIN_FIT_SCALE,
  };
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Subscribe to prefers-reduced-motion.
 *
 * Used through `useSyncExternalStore` so the preference is read without an effect
 * that sets state, and so the server render (where matchMedia does not exist) can
 * report `false` explicitly.
 */
function subscribeReducedMotion(onStoreChange: () => void): () => void {
  const media = window.matchMedia(REDUCED_MOTION_QUERY);
  media.addEventListener("change", onStoreChange);
  return () => media.removeEventListener("change", onStoreChange);
}

function interfaceToRender(iface: TopologyLive["edges"][number]["live"]["local_interface"]) {
  if (!iface) return null;
  return {
    name: iface.name,
    operStatus: iface.oper_status,
    speedBps: iface.speed_bps,
    rxBps: iface.rx_bps,
    txBps: iface.tx_bps,
    utilizationPct: iface.utilization_pct,
    inErrors: iface.in_errors,
    outErrors: iface.out_errors,
    discardsIn: iface.discards_in,
    discardsOut: iface.discards_out,
    vlanId: iface.vlan_id,
    lastTelemetryAt: iface.last_telemetry_at,
  };
}

function buildEdges(payload: TopologyLive): RenderEdge[] {
  return payload.edges.map((edge) => {
    const local = edge.live.local_interface;
    const remote = edge.live.remote_interface;
    const rx = edge.live.link.rx_bps;
    const tx = edge.live.link.tx_bps;
    const utilizationPct = edge.live.link.utilization_pct;
    return {
      id: edge.id,
      source: edge.source_id,
      target: edge.target_id,
      relationshipType: edge.relationship_type,
      unknownRelationshipType: Boolean(edge.unknown_relationship_type),
      state: linkState({
        statusRaw: edge.live.status_raw ?? edge.status ?? null,
        freshnessState: edge.live.link.freshness.state,
        localOperStatus: local?.oper_status ?? null,
        remoteOperStatus: remote?.oper_status ?? null,
      }),
      label: edge.label ?? null,
      direction: trafficDirection({
        localRxBps: local?.rx_bps ?? null,
        localTxBps: local?.tx_bps ?? null,
        remoteRxBps: remote?.rx_bps ?? null,
        remoteTxBps: remote?.tx_bps ?? null,
      }),
      forwardLabel: local?.tx_bps === null || local?.tx_bps === undefined ? null : formatBps(local.tx_bps),
      reverseLabel:
        remote?.tx_bps === null || remote?.tx_bps === undefined ? null : formatBps(remote.tx_bps),
      capacityLabel: formatBps(edge.live.link.capacity_bps),
      utilization: {
        label: formatPercent(utilizationPct),
        band: utilizationBand(utilizationPct),
        source: edge.live.link.utilization_source,
      },
      evidence: edge.evidence,
      discoveredBy: edge.discovered_by ?? null,
      classification: edge.live.classification,
      confidence: edge.confidence ?? null,
      inferred: Boolean(edge.is_inferred),
      observedAt: edge.live.link.observed_at,
      freshnessState: edge.live.link.freshness.state,
      statusRaw: edge.live.status_raw ?? edge.status ?? null,
      localInterfaceName: edge.live.local_interface_name,
      remoteInterfaceName: edge.live.remote_interface_name,
      localInterface: interfaceToRender(local),
      remoteInterface: interfaceToRender(remote),
      totalBps: rx === null && tx === null ? null : (rx ?? 0) + (tx ?? 0),
    };
  });
}

function buildNodes(payload: TopologyLive): RenderNode[] {
  const siteLabelById = new Map<string, string>();
  for (const node of payload.nodes) {
    if (node.type === "site") siteLabelById.set(node.id, node.name);
  }

  return payload.nodes.map((node) => {
    const live = node.live;
    const identity = live?.identity ?? null;
    const deviceType = node.device_type ?? identity?.device_type ?? null;
    const deviceTypeRaw = node.device_type_raw ?? identity?.device_type_raw ?? null;
    const visual = resolveVendorVisual({
      vendor: identity?.vendor ?? null,
      model: identity?.model ?? null,
      deviceType,
      deviceTypeRaw,
    });
    const unidentified = isUnknownResource({
      deviceType,
      deviceTypeRaw,
      recognitionStatus: live?.recognition.status ?? null,
    });
    const state: DeviceState = live
      ? deviceState({
          operationalState: live.operational_state,
          health: node.health,
          freshnessState: live.freshness.state,
          lifecycleState: live.lifecycle_state,
        })
      : "UNKNOWN";
    const metadataIp = node.metadata?.ip;
    return {
      id: node.id,
      name: node.name,
      kind: node.type,
      siteId: node.site_id ?? live?.site_id ?? null,
      siteLabel: node.site_id ? siteLabelById.get(`site:${node.site_id}`) ?? null : null,
      state,
      visual,
      ip: identity?.ip ?? (typeof metadataIp === "string" ? metadataIp : null),
      unidentified,
      recognitionStatus: live?.recognition.status ?? null,
      health: node.health ?? null,
      operationalState: live?.operational_state ?? null,
      statusRaw: live?.status_raw ?? node.status ?? null,
      lifecycleState: live?.lifecycle_state ?? null,
      freshnessState: live?.freshness.state ?? null,
      freshnessLabel: freshnessLabel(live?.freshness.state),
      ageSeconds: live?.freshness.age_seconds ?? null,
      interfaces: live
        ? {
            total: live.interfaces.total,
            up: live.interfaces.up,
            down: live.interfaces.down,
            hasTelemetry: live.interfaces.has_telemetry,
          }
        : null,
      rxBps: live?.interfaces.rx_bps ?? null,
      txBps: live?.interfaces.tx_bps ?? null,
      relationshipCount: live?.relationship_count ?? null,
      vendorLabel: visual.vendorLabel,
      deviceType,
      deviceTypeRaw,
      model: identity?.model ?? null,
      serial: identity?.serial ?? null,
      hostname: identity?.hostname ?? null,
      sourceType: node.source_type ?? null,
    };
  });
}

function buildRenderModel(payload: TopologyLive): RenderModel {
  const nodes = buildNodes(payload);
  const edges = buildEdges(payload);
  const counts: Record<DeviceState, number> = {
    ONLINE: 0,
    WARNING: 0,
    OFFLINE: 0,
    UNKNOWN: 0,
    STALE: 0,
    MAINTENANCE: 0,
  };
  for (const node of nodes) if (node.kind === "device") counts[node.state] += 1;

  return {
    nodes,
    edges,
    summary: {
      devices: payload.summary.devices_total,
      sites: payload.summary.sites_total,
      links: payload.summary.links_total,
      unidentified: payload.summary.unidentified_devices_total,
      online: counts.ONLINE,
      warning: counts.WARNING,
      offline: counts.OFFLINE,
      unknown: counts.UNKNOWN,
      stale: counts.STALE,
      maintenance: counts.MAINTENANCE,
      interfacesTotal: payload.summary.interfaces_total,
      interfacesUp: payload.summary.interfaces_up,
      interfacesDown: payload.summary.interfaces_down,
      trafficMeasured: payload.summary.traffic_measured,
      rxLabel: formatBps(payload.summary.rx_bps_total),
      txLabel: formatBps(payload.summary.tx_bps_total),
    },
    freshnessState: payload.telemetry_freshness.state,
    freshnessLabel: freshnessLabel(payload.telemetry_freshness.state),
    freshnessAgeSeconds: payload.telemetry_freshness.age_seconds,
    devicesReporting: payload.telemetry_freshness.devices_reporting,
    generatedAt: payload.generated_at,
    graphStatus: payload.status ?? null,
    limitations: payload.limitations ?? [],
    scopeHiddenNodes: payload.scope.nodes_hidden,
    scopeHiddenEdges: payload.scope.edges_hidden,
    siteOptions: payload.nodes
      .filter((node) => node.type === "site")
      .map((node) => ({ id: node.id, label: node.name }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  };
}

/** Filter values that exist in THIS tenant's data — never a fixed list. */
function buildFilterOptions(model: RenderModel): FilterOptions {
  const states = new Set<DeviceState>();
  const vendors = new Map<string, string>();
  const deviceTypes = new Set<string>();
  const relationshipTypes = new Set<string>();
  const evidenceSources = new Set<string>();
  const classifications = new Set<string>();

  for (const node of model.nodes) {
    if (node.kind !== "device") continue;
    states.add(node.state);
    if (node.visual.vendorKey) vendors.set(node.visual.vendorKey, node.vendorLabel);
    if (node.deviceType) deviceTypes.add(node.deviceType);
  }
  for (const edge of model.edges) {
    relationshipTypes.add(edge.relationshipType);
    if (edge.discoveredBy) evidenceSources.add(edge.discoveredBy);
    classifications.add(edge.classification ?? "unclassified");
  }

  return {
    states: [...states].sort(),
    vendors: [...vendors.entries()]
      .map(([key, label]) => ({ key, label }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    deviceTypes: [...deviceTypes].sort(),
    relationshipTypes: [...relationshipTypes].sort(),
    evidenceSources: [...evidenceSources].sort(),
    classifications: [...classifications].sort(),
  };
}

interface VisibleGraph {
  nodes: RenderNode[];
  edges: RenderEdge[];
}

/**
 * Filtering happens on the render model. Site nodes are kept only while they
 * still have a visible member, so an empty site box is never drawn.
 */
function filterModel(model: RenderModel, filters: TopologyFilters, query: string): VisibleGraph {
  const needle = query.trim().toLowerCase();
  const devices = model.nodes.filter((node) => {
    if (node.kind === "site") return false;
    if (filters.states.length && !filters.states.includes(node.state)) return false;
    if (filters.vendors.length && !filters.vendors.includes(node.visual.vendorKey)) return false;
    if (
      filters.deviceTypes.length &&
      !(node.deviceType && filters.deviceTypes.includes(node.deviceType))
    ) {
      return false;
    }
    // "Unclassified" is the backend's own recognition verdict, not a guess.
    if (filters.unidentifiedOnly && !node.unidentified) return false;
    if (needle) {
      const haystack = [
        node.name,
        node.ip ?? "",
        node.hostname ?? "",
        node.serial ?? "",
        node.vendorLabel,
        node.model ?? "",
        node.siteLabel ?? "",
        node.deviceTypeRaw ?? "",
      ]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });

  const keep = new Set(devices.map((node) => node.id));
  for (const node of devices) {
    if (node.siteId) keep.add(`site:${node.siteId}`);
  }

  const edges = model.edges.filter((edge) => {
    if (!keep.has(edge.source) || !keep.has(edge.target)) return false;
    if (
      filters.relationshipTypes.length &&
      !filters.relationshipTypes.includes(edge.relationshipType)
    ) {
      return false;
    }
    if (filters.evidence.length) {
      const tags: string[] = [edge.classification ?? "unclassified"];
      if (edge.inferred) tags.push("inferred");
      if (
        !tags.some((tag) => filters.evidence.includes(tag as TopologyFilters["evidence"][number]))
      ) {
        return false;
      }
    }
    return true;
  });

  return { nodes: model.nodes.filter((node) => keep.has(node.id)), edges };
}

export default function OperationalTopologyPanel() {
  // View state lives here, so a refresh updates DATA without resetting the view.
  const [viewportOverride, setViewportOverride] = useState<Viewport | null>(null);
  const [layout, setLayout] = useState<ViewKind>("tree");
  // Null until the operator expands/collapses; then their choice wins.
  const [collapsedOverride, setCollapsedOverride] = useState<Set<string> | null>(null);
  const [labelAll, setLabelAll] = useState(false);
  const [showUnconnected, setShowUnconnected] = useState(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [queryText, setQueryText] = useState("");
  const [filters, setFilters] = useState<TopologyFilters>(EMPTY_FILTERS);
  const [siteFilter, setSiteFilter] = useState("");
  const [refreshSeconds, setRefreshSeconds] = useState(60);
  const [animateEnabled, setAnimateEnabled] = useState(true);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);

  // Server state lives in React Query (the app's convention, same as the
  // discovery page and useNetworkIntelligence): cached, deduplicated, and
  // refreshed in place instead of mirrored into local state.
  const query = useQuery({
    queryKey: ["topology-live", siteFilter || null],
    queryFn: () => getLiveTopology({ siteId: siteFilter || null }),
    refetchInterval: refreshSeconds > 0 ? refreshSeconds * 1000 : false,
    refetchOnWindowFocus: false,
  });
  const payload = query.data ?? null;
  const error =
    query.error instanceof Error
      ? query.error.message
      : query.error
        ? "Topology unavailable"
        : null;

  // prefers-reduced-motion through useSyncExternalStore: hydration-safe, and it
  // updates without a state-setting effect.
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_MOTION_QUERY).matches,
    () => false
  );

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, []);

  const model = useMemo(() => (payload ? buildRenderModel(payload) : null), [payload]);
  const options = useMemo(() => (model ? buildFilterOptions(model) : null), [model]);
  const visible = useMemo(
    () => (model ? filterModel(model, filters, queryText) : { nodes: [], edges: [] }),
    [model, filters, queryText]
  );

  /**
   * The tree is built from the FILTERED graph but not the search: searching
   * highlights matches in place instead of re-rooting the tree around them.
   */
  const treeGraph = useMemo(
    () =>
      model && layout === "tree" ? filterModel(model, filters, "") : { nodes: [], edges: [] },
    [model, filters, layout]
  );
  const treeInput = useMemo(
    () => ({
      nodes: treeGraph.nodes
        .filter((node) => node.kind !== "site")
        .map((node) => ({ id: node.id, iconKey: node.visual.iconKey })),
      edges: treeGraph.edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target })),
    }),
    [treeGraph]
  );
  const fullTree = useMemo<TreeLayout | null>(
    () => (layout === "tree" ? buildTreeLayout(treeInput.nodes, treeInput.edges) : null),
    [layout, treeInput]
  );
  const collapsed = useMemo<ReadonlySet<string>>(() => {
    if (!fullTree || queryText.trim()) return NO_COLLAPSE;
    if (collapsedOverride) return collapsedOverride;
    // A very large network opens two hops deep, like Meraki's large-network guard.
    return fullTree.positions.size > LARGE_TREE_THRESHOLD ? collapseBeyond(fullTree, 2) : NO_COLLAPSE;
  }, [fullTree, collapsedOverride, queryText]);
  const tree = useMemo<TreeLayout | null>(() => {
    if (!fullTree) return null;
    if (!collapsed.size) return fullTree;
    return buildTreeLayout(treeInput.nodes, treeInput.edges, { collapsed });
  }, [fullTree, collapsed, treeInput]);
  const treeNodeMap = useMemo(
    () => new Map(treeGraph.nodes.map((node) => [node.id, node])),
    [treeGraph.nodes]
  );
  const treeEdgeMap = useMemo(
    () => new Map(treeGraph.edges.map((edge) => [edge.id, edge])),
    [treeGraph.edges]
  );
  const unconnectedDevices = useMemo(
    () =>
      tree
        ? tree.unlinked
            .map((id) => treeNodeMap.get(id))
            .filter((node): node is RenderNode => Boolean(node))
        : [],
    [tree, treeNodeMap]
  );

  const toggleCollapse = (id: string) =>
    setCollapsedOverride(() => {
      const next = new Set(collapsed);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const positions = useMemo(() => {
    if (tree) return tree.positions;
    const placed = layoutNodes(
      visible.nodes.map((node) => ({ id: node.id, siteId: node.siteId, type: node.kind, group: node.state })),
      visible.edges.map((edge) => ({ source: edge.source, target: edge.target })),
      layout === "tree" ? "connected-first" : layout,
      {},
      // Worst first, so the devices that need attention sit right under the map.
      ["OFFLINE", "WARNING", "STALE", "UNKNOWN", "MAINTENANCE", "ONLINE"]
    );
    return new Map(placed.map((entry) => [entry.id, { x: entry.x, y: entry.y }]));
  }, [tree, visible, layout]);

  const fitPoints = useCallback(
    (points: { x: number; y: number }[]) =>
      layout === "tree" ? fitTree(points, size) : fitViewport(points, size),
    [layout, size]
  );

  /**
   * The viewport is DERIVED until an operator moves it: with no explicit
   * override the graph fits itself, so a first load (or a layout switch) never
   * opens on an off-screen graph. Zoom/pan/fit/reset set the override, and a
   * refresh keeps it — which is what preserves the view across refreshes.
   */
  const viewport = useMemo(
    () => viewportOverride ?? fitPoints([...positions.values()]) ?? IDENTITY_VIEWPORT,
    [viewportOverride, positions, fitPoints]
  );

  const animatedEdgeIds = useMemo(() => {
    const budget = animationBudget(visible.edges.length);
    const allowed = new Set<string>();
    let used = 0;
    for (const edge of visible.edges) {
      if (used >= budget) break;
      const animating = shouldAnimate({
        bps: edge.totalBps,
        freshnessCurrent: isFreshnessCurrent(edge.freshnessState),
        reducedMotion,
        animateEnabled,
        budget: budget - used,
      });
      if (!animating) continue;
      allowed.add(edge.id);
      used += 1;
    }
    return allowed;
  }, [visible.edges, reducedMotion, animateEnabled]);

  const highlight = useMemo(() => {
    if (selectedNodeId) {
      const nodes = new Set<string>([selectedNodeId]);
      const edges = new Set<string>();
      for (const edge of visible.edges) {
        if (edge.source === selectedNodeId || edge.target === selectedNodeId) {
          edges.add(edge.id);
          nodes.add(edge.source);
          nodes.add(edge.target);
        }
      }
      return { nodes, edges };
    }
    if (queryText.trim() && layout === "tree") {
      // Tree search highlights matches (and their uplinks) without re-rooting.
      const nodes = new Set(visible.nodes.map((node) => node.id));
      const edges = new Set<string>();
      for (const link of tree?.links ?? []) {
        if (nodes.has(link.child)) edges.add(link.id);
      }
      return { nodes, edges };
    }
    if (queryText.trim()) {
      return {
        nodes: new Set(visible.nodes.map((node) => node.id)),
        edges: new Set(visible.edges.map((edge) => edge.id)),
      };
    }
    return null;
  }, [selectedNodeId, queryText, visible, layout, tree]);

  const fit = useCallback(() => {
    const fitted = fitPoints([...positions.values()]);
    if (fitted) setViewportOverride(fitted);
  }, [positions, fitPoints]);

  const zoomBy = (factor: number) => {
    if (!size.width || !size.height) return;
    const next = Math.min(3, Math.max(0.15, viewport.scale * factor));
    const ratio = next / viewport.scale;
    const centreX = size.width / 2;
    const centreY = size.height / 2;
    setViewportOverride({
      scale: next,
      offsetX: centreX - (centreX - viewport.offsetX) * ratio,
      offsetY: centreY - (centreY - viewport.offsetY) * ratio,
    });
  };

  const selectedNode = selectedNodeId
    ? model?.nodes.find((node) => node.id === selectedNodeId) ?? null
    : null;
  const selectedEdge = selectedEdgeId
    ? model?.edges.find((edge) => edge.id === selectedEdgeId) ?? null
    : null;

  const toggleState = (state: DeviceState) =>
    setFilters((previous) => ({
      ...previous,
      states: previous.states.includes(state)
        ? previous.states.filter((entry) => entry !== state)
        : [...previous.states, state],
    }));

  const toggleRelationship = (type: string) =>
    setFilters((previous) => ({
      ...previous,
      relationshipTypes: previous.relationshipTypes.includes(type)
        ? previous.relationshipTypes.filter((entry) => entry !== type)
        : [...previous.relationshipTypes, type],
    }));

  const toggleEvidence = (tag: TopologyFilters["evidence"][number]) =>
    setFilters((previous) => ({
      ...previous,
      evidence: previous.evidence.includes(tag)
        ? previous.evidence.filter((entry) => entry !== tag)
        : [...previous.evidence, tag],
    }));

  const chip = (active: boolean) => ({
    fontSize: 11,
    padding: "3px 8px",
    borderRadius: 999,
    border: `1px solid ${active ? "var(--accent, #38bdf8)" : "var(--border)"}`,
    background: active ? "rgba(56,189,248,0.14)" : "transparent",
    color: active ? "var(--fg)" : "var(--muted)",
    cursor: "pointer",
  });

  const nodeCount = visible.nodes.length;
  const unlinkedCount = useMemo(() => {
    const devices = visible.nodes.filter((node) => node.kind !== "site");
    const linked = linkedNodeIds(devices, visible.edges);
    return devices.filter((node) => !linked.has(node.id)).length;
  }, [visible]);
  const edgeCount = visible.edges.length;
  /**
   * Zero relationships means the honest empty state applies. Real device nodes
   * are still drawn (they exist); no link is ever implied.
   */
  const showEmptyState = model !== null && isGraphEmpty(edgeCount);

  const emptyState = useMemo(
    () =>
      buildEmptyState({
        devices: model?.summary.devices ?? 0,
        relationships: edgeCount,
        interfaces: model?.summary.interfacesTotal ?? 0,
        online: model?.summary.online ?? 0,
        offline: model?.summary.offline ?? 0,
        unidentified: model?.summary.unidentified ?? 0,
        limitations: model?.limitations ?? [],
      }),
    [model, edgeCount]
  );

  /** Centre the viewport on a graph coordinate (the minimap's only job). */
  const navigateTo = useCallback(
    (x: number, y: number) => {
      if (!size.width || !size.height) return;
      setViewportOverride({
        scale: viewport.scale,
        offsetX: size.width / 2 - x * viewport.scale,
        offsetY: size.height / 2 - y * viewport.scale,
      });
    },
    [viewport.scale, size.width, size.height]
  );

  /** View-level freshness: LIVE DATA vs DATA STALE, from the backend's own state. */
  const live = model ? isFreshnessCurrent(model.freshnessState) : false;
  const generated = model ? new Date(model.generatedAt).toLocaleTimeString() : null;

  return (
    <section
      data-testid="topology-workspace"
      style={{ display: "flex", flexDirection: "column", gap: 8, padding: "4px 0 12px" }}
    >
      <header
        data-testid="topology-header"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 10,
          justifyContent: "space-between",
          paddingBottom: 6,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ minWidth: 220 }}>
          <h1
            style={{
              fontSize: 17,
              fontWeight: 600,
              color: "var(--fg)",
              margin: 0,
              letterSpacing: "0.2px",
            }}
          >
            Live Operational Topology
          </h1>
          <p style={{ fontSize: 11, color: "var(--muted)", margin: "3px 0 0" }}>
            Site → device → interface, from recorded relationships and telemetry only.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span
            data-testid="freshness-badge"
            title="Freshness of the newest telemetry the backend holds for this view"
            style={{
              fontSize: 11,
              letterSpacing: "0.5px",
              color: live ? "var(--green)" : "var(--amber)",
            }}
          >
            ● {live ? "LIVE DATA" : "DATA STALE"}
            {model ? ` · ${model.freshnessLabel.toLowerCase()}` : ""}
          </span>
          {generated ? (
            <span data-testid="topology-updated" style={{ fontSize: 11, color: "var(--muted)" }}>
              Updated {generated}
            </span>
          ) : null}
          {model ? (
            <span data-testid="topology-reporting" style={{ fontSize: 11, color: "var(--muted)" }}>
              {model.devicesReporting} reporting
            </span>
          ) : null}
          {model?.graphStatus ? (
            <span
              data-testid="topology-graph-status"
              title="The graph engine's own completeness status"
              style={{ fontSize: 11, color: "var(--muted)" }}
            >
              graph: {model.graphStatus}
            </span>
          ) : null}

          <label
            style={{
              fontSize: 11,
              color: "var(--muted)",
              display: "flex",
              gap: 5,
              alignItems: "center",
            }}
          >
            Auto
            <select
              value={refreshSeconds}
              onChange={(event) => setRefreshSeconds(Number(event.target.value))}
              aria-label="Auto refresh interval"
              data-testid="topology-auto-refresh"
              style={{
                background: "transparent",
                color: "var(--fg)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "2px 4px",
                fontSize: 11,
              }}
            >
              {REFRESH_OPTIONS.map((option) => (
                <option key={option.seconds} value={option.seconds}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={() => void query.refetch()}
            style={{ ...chip(false), display: "inline-flex", gap: 6, alignItems: "center" }}
            aria-label="Refresh topology"
            data-testid="topology-refresh"
          >
            {query.isFetching ? <Loader2 size={12} /> : <RefreshCw size={12} />}
            Refresh
          </button>

          <button
            type="button"
            onClick={() => setAnimateEnabled((value) => !value)}
            style={{
              ...chip(animateEnabled),
              display: "inline-flex",
              gap: 6,
              alignItems: "center",
            }}
            aria-pressed={animateEnabled}
            title="Traffic animation is driven only by measured interface counters"
            data-testid="topology-animation-toggle"
          >
            <Play size={12} />
            {animateEnabled ? "Flow animation on" : "Flow animation off"}
          </button>
        </div>
      </header>

      {error ? (
        <div
          data-testid="topology-error"
          style={{
            padding: "8px 10px",
            border: "1px solid var(--red)",
            borderRadius: 8,
            fontSize: 12,
            color: "var(--fg)",
            display: "flex",
            gap: 8,
            alignItems: "center",
          }}
        >
          <AlertTriangle size={14} />
          <span>Topology unavailable: {error}</span>
        </div>
      ) : null}

      {model ? (
        <div
          style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}
          data-testid="topology-status-row"
        >
          <TopologyStatusLine summary={model.summary} active={filters.states} onToggle={toggleState} />
          <button
            type="button"
            onClick={() =>
              setFilters((previous) => ({
                ...previous,
                states: PROBLEM_STATES.every((state) => previous.states.includes(state)) &&
                  previous.states.length === PROBLEM_STATES.length
                  ? []
                  : PROBLEM_STATES,
              }))
            }
            aria-pressed={
              filters.states.length === PROBLEM_STATES.length &&
              PROBLEM_STATES.every((state) => filters.states.includes(state))
            }
            title="Show only alerting and offline devices"
            style={chip(
              filters.states.length === PROBLEM_STATES.length &&
                PROBLEM_STATES.every((state) => filters.states.includes(state))
            )}
            data-testid="topology-only-problems"
          >
            Only problems
          </button>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            {model.summary.links} link{model.summary.links === 1 ? "" : "s"} ·{" "}
            {model.summary.interfacesTotal} interface{model.summary.interfacesTotal === 1 ? "" : "s"}
            {model.summary.unidentified ? ` · ${model.summary.unidentified} unclassified` : ""}
            {model.summary.trafficMeasured
              ? ` · ↓ ${model.summary.rxLabel ?? "—"} ↑ ${model.summary.txLabel ?? "—"}`
              : " · traffic not measured"}
          </span>
        </div>
      ) : null}

      {model ? (
        <TopologyToolbar
          query={queryText}
          onQueryChange={setQueryText}
          siteFilter={siteFilter}
          onSiteFilterChange={setSiteFilter}
          siteOptions={model.siteOptions}
          options={options}
          filters={filters}
          onToggleState={toggleState}
          onToggleUnidentified={() =>
            setFilters((previous) => ({
              ...previous,
              unidentifiedOnly: !previous.unidentifiedOnly,
            }))
          }
          onToggleVendor={(key) =>
            setFilters((previous) => ({
              ...previous,
              vendors: previous.vendors.includes(key)
                ? previous.vendors.filter((entry) => entry !== key)
                : [...previous.vendors, key],
            }))
          }
          onToggleDeviceType={(type) =>
            setFilters((previous) => ({
              ...previous,
              deviceTypes: previous.deviceTypes.includes(type)
                ? previous.deviceTypes.filter((entry) => entry !== type)
                : [...previous.deviceTypes, type],
            }))
          }
          onToggleRelationship={toggleRelationship}
          onToggleEvidence={toggleEvidence}
          onClear={() => {
            setFilters(EMPTY_FILTERS);
            setQueryText("");
          }}
          layout={layout}
          showStateChips={false}
          viewControls={
            layout === "tree" ? (
              <>
                <button
                  type="button"
                  onClick={() => setCollapsedOverride(new Set())}
                  style={{ ...chip(false), display: "inline-flex", gap: 4, alignItems: "center" }}
                  title="Expand every branch"
                  data-testid="topology-expand-all"
                >
                  <ChevronsUpDown size={11} /> Expand
                </button>
                <button
                  type="button"
                  onClick={() => fullTree && setCollapsedOverride(collapseBeyond(fullTree, 1))}
                  style={{ ...chip(false), display: "inline-flex", gap: 4, alignItems: "center" }}
                  title="Collapse to the first hop"
                  data-testid="topology-collapse-all"
                >
                  <ChevronsDownUp size={11} /> Collapse
                </button>
                <label
                  style={{ fontSize: 11, color: "var(--muted)", display: "inline-flex", gap: 4, alignItems: "center" }}
                >
                  <input
                    type="checkbox"
                    checked={labelAll}
                    onChange={(event) => setLabelAll(event.target.checked)}
                    data-testid="topology-label-all"
                  />
                  Label all devices
                </label>
                {!showUnconnected && unconnectedDevices.length ? (
                  <button
                    type="button"
                    onClick={() => setShowUnconnected(true)}
                    style={{ ...chip(false), display: "inline-flex", gap: 4, alignItems: "center" }}
                    data-testid="topology-show-unconnected"
                  >
                    <ListTree size={11} /> Not connected ({unconnectedDevices.length})
                  </button>
                ) : null}
              </>
            ) : null
          }
          onLayoutChange={(next) => {
            setLayout(next);
            // A new layout has new coordinates: re-fit instead of keeping an
            // override that would leave the graph off-screen.
            setViewportOverride(null);
          }}
          resultCount={nodeCount}
          totalCount={model.nodes.filter((node) => node.kind !== "site").length}
        />
      ) : null}

      {model && layout === "connected-first" && unlinkedCount > 0 ? (
        <p
          role="status"
          style={{ margin: "0 0 8px", fontSize: 12, color: "var(--ecc-text-muted, var(--muted))" }}
        >
          {unlinkedCount} of {visible.nodes.filter((node) => node.kind !== "site").length} devices have no recorded
          relationship yet. Linked devices are drawn as a hierarchy at the top; unlinked devices are grouped
          below by state, worst first. Run discovery with LLDP/CDP or add relationships to connect them.
        </p>
      ) : null}

      <div
        ref={shellRef}
        data-testid="topology-canvas-shell"
        style={{
          position: "relative",
          display: "flex",
          height: "clamp(480px, calc(100vh - 300px), 1400px)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          overflow: "hidden",
          // The tree follows the theme; the card layouts keep their dark stage.
          background: layout === "tree" ? "var(--ecc-surface, #0b1220)" : "rgba(2,6,23,0.55)",
        }}
      >
        <div ref={containerRef} style={{ position: "relative", flex: 1, minWidth: 0 }}>
          {!model ? (
            <p style={{ padding: 16, fontSize: 12, color: "var(--muted)" }}>
              Loading live topology…
            </p>
          ) : nodeCount === 0 ? (
            <TopologyEmptyStatePanel state={emptyState} variant="full" />
          ) : tree ? (
            tree.positions.size === 0 ? (
              model.summary.links > 0 ? (
                // Links exist; the filters hide every linked device. Say so,
                // rather than claiming nothing was discovered.
                <div
                  data-testid="topology-tree-filtered-empty"
                  style={{ padding: 24, fontSize: 12, color: "var(--muted)" }}
                >
                  No connected device matches the current filters.{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setFilters(EMPTY_FILTERS);
                      setQueryText("");
                    }}
                    style={{ ...chip(false), marginLeft: 6 }}
                  >
                    Clear filters
                  </button>
                </div>
              ) : (
                <TopologyEmptyStatePanel state={emptyState} variant="full" />
              )
            ) : (
              <TopologyTreeCanvas
                tree={tree}
                nodes={treeNodeMap}
                edges={treeEdgeMap}
                viewport={viewport}
                onViewportChange={setViewportOverride}
                selectedNodeId={selectedNodeId}
                selectedEdgeId={selectedEdgeId}
                onSelectNode={(id) => {
                  setSelectedEdgeId(null);
                  setSelectedNodeId(id);
                }}
                onSelectEdge={(id) => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(id);
                }}
                onClearSelection={() => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(null);
                }}
                collapsed={collapsed}
                onToggleCollapse={toggleCollapse}
                labelAll={labelAll}
                highlight={highlight}
              />
            )
          ) : (
            <>
              <TopologyCanvas
                model={visible}
                positions={positions}
                viewport={viewport}
                onViewportChange={setViewportOverride}
                selectedNodeId={selectedNodeId}
                selectedEdgeId={selectedEdgeId}
                onSelectNode={(id) => {
                  setSelectedEdgeId(null);
                  setSelectedNodeId(id);
                }}
                onSelectEdge={(id) => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(id);
                }}
                onClearSelection={() => {
                  setSelectedNodeId(null);
                  setSelectedEdgeId(null);
                }}
                highlight={highlight}
                reducedMotion={reducedMotion}
                animateEnabled={animateEnabled}
                animatedEdgeIds={animatedEdgeIds}
              />
              {showEmptyState ? (
                <TopologyEmptyStatePanel state={emptyState} variant="compact" />
              ) : null}
            </>
          )}

          {model && nodeCount > 0 && positions.size > 0 ? (
            <>
              <TopologyMinimap
                nodes={tree ? treeGraph.nodes : visible.nodes}
                positions={positions}
                edgeCount={tree ? tree.links.length + tree.crossLinks.length : edgeCount}
                viewport={viewport}
                canvasSize={size}
                onNavigate={navigateTo}
              />
              <TopologyCanvasControls
                scale={viewport.scale}
                onZoomIn={() => zoomBy(1.2)}
                onZoomOut={() => zoomBy(1 / 1.2)}
                onFit={fit}
                onReset={() => setViewportOverride(IDENTITY_VIEWPORT)}
                fullscreenTarget={shellRef}
              />
              {tree ? <TopologyTreeLegend /> : <TopologyLegend />}
            </>
          ) : null}

          <TopologyDetailDrawer
            node={selectedNode}
            edge={selectedEdge}
            onClose={() => {
              setSelectedNodeId(null);
              setSelectedEdgeId(null);
            }}
          />
        </div>

        {tree && showUnconnected && unconnectedDevices.length ? (
          <UnconnectedRail
            devices={unconnectedDevices}
            selectedNodeId={selectedNodeId}
            onSelect={(id) => {
              setSelectedEdgeId(null);
              setSelectedNodeId(id);
            }}
            onClose={() => setShowUnconnected(false)}
          />
        ) : null}
      </div>

      {model && model.scopeHiddenNodes > 0 ? (
        <p style={{ fontSize: 10, color: "var(--muted)", margin: 0 }}>
          Site filter hides {model.scopeHiddenNodes} node(s) and {model.scopeHiddenEdges} link(s)
          outside the selected site.
        </p>
      ) : null}
    </section>
  );
}
