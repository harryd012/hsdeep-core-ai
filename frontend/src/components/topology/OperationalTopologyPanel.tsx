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
import { AlertTriangle, Loader2, RefreshCw, RotateCcw, Search, Eye, Play } from "lucide-react";

import { getLiveTopology, type TopologyLive } from "../../lib/topologyApi";
import {
  LAYOUTS,
  fitViewport,
  layoutNodes,
  type LayoutKind,
  type Viewport,
} from "../../lib/topology/layout";
import {
  DEVICE_STATE_VISUALS,
  LINK_STATE_VISUALS,
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
import { isUnknownResource, resolveVendorVisual } from "../../lib/topology/vendors";
import TopologyCanvas from "./TopologyCanvas";
import TopologyDetailDrawer from "./TopologyDetailDrawer";
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
};

const IDENTITY_VIEWPORT: Viewport = { scale: 1, offsetX: 60, offsetY: 60 };

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
      relationshipCount: live?.relationship_count ?? 0,
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
  const [viewport, setViewport] = useState<Viewport>(IDENTITY_VIEWPORT);
  const [layout, setLayout] = useState<LayoutKind>("hierarchical");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [queryText, setQueryText] = useState("");
  const [filters, setFilters] = useState<TopologyFilters>(EMPTY_FILTERS);
  const [siteFilter, setSiteFilter] = useState("");
  const [refreshSeconds, setRefreshSeconds] = useState(60);
  const [animateEnabled, setAnimateEnabled] = useState(true);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement | null>(null);

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

  const positions = useMemo(() => {
    const placed = layoutNodes(
      visible.nodes.map((node) => ({ id: node.id, siteId: node.siteId, type: node.kind })),
      visible.edges.map((edge) => ({ source: edge.source, target: edge.target })),
      layout
    );
    return new Map(placed.map((entry) => [entry.id, { x: entry.x, y: entry.y }]));
  }, [visible, layout]);

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
    if (queryText.trim()) {
      return {
        nodes: new Set(visible.nodes.map((node) => node.id)),
        edges: new Set(visible.edges.map((edge) => edge.id)),
      };
    }
    return null;
  }, [selectedNodeId, queryText, visible]);

  const fit = useCallback(() => {
    const fitted = fitViewport([...positions.values()], size);
    if (fitted) setViewport(fitted);
  }, [positions, size]);

  const zoomBy = (factor: number) => {
    if (!size.width || !size.height) return;
    const next = Math.min(3, Math.max(0.15, viewport.scale * factor));
    const ratio = next / viewport.scale;
    const centreX = size.width / 2;
    const centreY = size.height / 2;
    setViewport({
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

  const live = model ? isFreshnessCurrent(model.freshnessState) : false;
  const generated = model ? new Date(model.generatedAt).toLocaleTimeString() : null;
  const stateChips: DeviceState[] = [
    "ONLINE",
    "WARNING",
    "OFFLINE",
    "UNKNOWN",
    "STALE",
    "MAINTENANCE",
  ];

  return (
    <section style={{ padding: "4px 0 24px" }}>
      <header
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "baseline",
          gap: 12,
          justifyContent: "space-between",
        }}
      >
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 600, color: "var(--fg)", margin: 0 }}>
            Live Operational Topology
          </h1>
          <p style={{ fontSize: 11, color: "var(--muted)", margin: "4px 0 0" }}>
            Site → device → interface, from recorded relationships and telemetry only.
            {model?.graphStatus ? ` Graph status: ${model.graphStatus}.` : ""}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span
            data-testid="freshness-badge"
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
            <span style={{ fontSize: 11, color: "var(--muted)" }}>Updated {generated}</span>
          ) : null}
          {model ? (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              {model.devicesReporting} device(s) reporting
            </span>
          ) : null}

          <label
            style={{
              fontSize: 11,
              color: "var(--muted)",
              display: "flex",
              gap: 6,
              alignItems: "center",
            }}
          >
            Auto refresh
            <select
              value={refreshSeconds}
              onChange={(event) => setRefreshSeconds(Number(event.target.value))}
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
          >
            {query.isFetching ? <Loader2 size={12} /> : <RefreshCw size={12} />}
            Refresh
          </button>

          <button
            type="button"
            onClick={() => setAnimateEnabled((value) => !value)}
            style={{ ...chip(animateEnabled), display: "inline-flex", gap: 6, alignItems: "center" }}
            aria-pressed={animateEnabled}
            title="Traffic animation is driven only by measured telemetry"
          >
            <Play size={12} />
            {animateEnabled ? "Flow animation on" : "Flow animation off"}
          </button>
        </div>
      </header>

      {error ? (
        <div
          style={{
            marginTop: 12,
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
          data-testid="topology-summary"
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 18,
            marginTop: 14,
            padding: "10px 12px",
            border: "1px solid var(--border)",
            borderRadius: 10,
            fontSize: 12,
            color: "var(--fg)",
          }}
        >
          {[
            { label: "Devices", value: model.summary.devices, tone: undefined },
            { label: "Online", value: model.summary.online, tone: "var(--green)" },
            { label: "Warning", value: model.summary.warning, tone: "var(--amber)" },
            { label: "Offline", value: model.summary.offline, tone: "var(--red)" },
            { label: "Unknown", value: model.summary.unknown, tone: "var(--muted)" },
            { label: "Stale", value: model.summary.stale, tone: "var(--muted)" },
            { label: "Links", value: model.summary.links, tone: undefined },
            { label: "Interfaces", value: model.summary.interfacesTotal, tone: undefined },
            { label: "Unidentified", value: model.summary.unidentified, tone: undefined },
          ].map((item) => (
            <span
              key={item.label}
              style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}
            >
              <strong style={{ color: item.tone ?? "var(--fg)" }}>{item.value}</strong>
              <span style={{ color: "var(--muted)" }}>{item.label}</span>
            </span>
          ))}
          <span style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
            <strong
              style={{ color: model.summary.trafficMeasured ? "var(--fg)" : "var(--muted)" }}
            >
              {model.summary.trafficMeasured
                ? `${model.summary.rxLabel ?? "N/A"} ↓ / ${model.summary.txLabel ?? "N/A"} ↑`
                : "No interface telemetry"}
            </strong>
            <span style={{ color: "var(--muted)" }}>Traffic</span>
          </span>
        </div>
      ) : null}

      {model && model.limitations.length ? (
        <div
          style={{
            marginTop: 10,
            padding: "8px 10px",
            border: "1px dashed var(--border)",
            borderRadius: 8,
            fontSize: 11,
            color: "var(--muted)",
          }}
        >
          {model.limitations.map((limitation) => (
            <div key={limitation}>• {limitation}</div>
          ))}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          alignItems: "center",
          marginTop: 12,
        }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "4px 8px",
          }}
        >
          <Search size={13} aria-hidden="true" />
          <input
            value={queryText}
            onChange={(event) => setQueryText(event.target.value)}
            placeholder="Search hostname, IP, serial, vendor, model, site…"
            aria-label="Search topology"
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--fg)",
              fontSize: 12,
              width: 260,
            }}
          />
        </span>

        <label
          style={{
            fontSize: 11,
            color: "var(--muted)",
            display: "flex",
            gap: 6,
            alignItems: "center",
          }}
        >
          Site
          <select
            value={siteFilter}
            onChange={(event) => setSiteFilter(event.target.value)}
            style={{
              background: "transparent",
              color: "var(--fg)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "2px 4px",
              fontSize: 11,
            }}
          >
            <option value="">All sites</option>
            {(model?.siteOptions ?? []).map((site) => (
              <option key={site.id} value={site.id.replace(/^site:/, "")}>
                {site.label}
              </option>
            ))}
          </select>
        </label>

        <span style={{ display: "inline-flex", gap: 6 }}>
          {LAYOUTS.map((entry) => (
            <button
              key={entry.kind}
              type="button"
              onClick={() => setLayout(entry.kind)}
              style={chip(layout === entry.kind)}
              title={entry.hint}
            >
              {entry.label}
            </button>
          ))}
        </span>

        <span style={{ display: "inline-flex", gap: 6 }}>
          <button type="button" onClick={() => zoomBy(1.2)} style={chip(false)} aria-label="Zoom in">
            +
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1 / 1.2)}
            style={chip(false)}
            aria-label="Zoom out"
          >
            −
          </button>
          <button type="button" onClick={fit} style={chip(false)}>
            Fit
          </button>
          <button
            type="button"
            onClick={() => setViewport(IDENTITY_VIEWPORT)}
            style={{ ...chip(false), display: "inline-flex", gap: 6, alignItems: "center" }}
          >
            <RotateCcw size={12} /> Reset view
          </button>
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          alignItems: "center",
          marginTop: 10,
        }}
      >
        <Eye size={13} aria-hidden="true" />
        {stateChips
          .filter((state) => !options || options.states.includes(state))
          .map((state) => (
            <button
              key={state}
              type="button"
              onClick={() => toggleState(state)}
              style={chip(filters.states.includes(state))}
            >
              {DEVICE_STATE_VISUALS[state].label}
            </button>
          ))}
        {(options?.vendors ?? []).map((vendor) => (
          <button
            key={vendor.key}
            type="button"
            onClick={() =>
              setFilters((previous) => ({
                ...previous,
                vendors: previous.vendors.includes(vendor.key)
                  ? previous.vendors.filter((entry) => entry !== vendor.key)
                  : [...previous.vendors, vendor.key],
              }))
            }
            style={chip(filters.vendors.includes(vendor.key))}
          >
            {vendor.label}
          </button>
        ))}
        {(options?.deviceTypes ?? []).map((type) => (
          <button
            key={type}
            type="button"
            onClick={() =>
              setFilters((previous) => ({
                ...previous,
                deviceTypes: previous.deviceTypes.includes(type)
                  ? previous.deviceTypes.filter((entry) => entry !== type)
                  : [...previous.deviceTypes, type],
              }))
            }
            style={chip(filters.deviceTypes.includes(type))}
          >
            {type}
          </button>
        ))}
        {(options?.relationshipTypes ?? []).map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => toggleRelationship(type)}
            style={chip(filters.relationshipTypes.includes(type))}
          >
            {type.replace(/_/g, " ")}
          </button>
        ))}
        {["observed", "inferred", "unclassified", "active", "passive", "api"]
          .filter(
            (tag) =>
              tag === "observed" ||
              tag === "inferred" ||
              (options?.classifications ?? []).includes(tag)
          )
          .map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => toggleEvidence(tag as TopologyFilters["evidence"][number])}
              style={chip(
                filters.evidence.includes(tag as TopologyFilters["evidence"][number])
              )}
            >
              {tag}
            </button>
          ))}
        <button
          type="button"
          onClick={() => {
            setFilters(EMPTY_FILTERS);
            setQueryText("");
          }}
          style={chip(false)}
        >
          Clear filters
        </button>
      </div>

      <div
        ref={containerRef}
        style={{ position: "relative", height: "clamp(420px, 60vh, 820px)", marginTop: 12 }}
      >
        {!model ? (
          <p style={{ fontSize: 12, color: "var(--muted)" }}>Loading live topology…</p>
        ) : visible.nodes.length === 0 ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              border: "1px dashed var(--border)",
              borderRadius: 10,
              fontSize: 12,
              color: "var(--muted)",
              textAlign: "center",
              padding: 24,
            }}
          >
            {model.nodes.length === 0
              ? "No infrastructure resources are recorded for this tenant yet, so there is nothing to draw."
              : "No resources match the current search and filters."}
          </div>
        ) : (
          <TopologyCanvas
            model={visible}
            positions={positions}
            viewport={viewport}
            onViewportChange={setViewport}
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
        )}

        <TopologyDetailDrawer
          node={selectedNode}
          edge={selectedEdge}
          onClose={() => {
            setSelectedNodeId(null);
            setSelectedEdgeId(null);
          }}
        />
      </div>

      <div
        style={{
          marginTop: 10,
          fontSize: 10,
          color: "var(--muted)",
          display: "flex",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <span>Legend</span>
        {Object.values(DEVICE_STATE_VISUALS).map((visual) => (
          <span key={visual.state} style={{ color: visual.color }}>
            {visual.marker} {visual.label}
          </span>
        ))}
        {Object.values(LINK_STATE_VISUALS).map((visual) => (
          <span key={`link-${visual.state}`} style={{ color: visual.color }}>
            — {visual.label}
          </span>
        ))}
        {model && model.scopeHiddenNodes > 0 ? (
          <span>
            Site filter hides {model.scopeHiddenNodes} node(s) and {model.scopeHiddenEdges} link(s)
          </span>
        ) : null}
      </div>
    </section>
  );
}
