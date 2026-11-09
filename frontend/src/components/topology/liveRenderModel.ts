/**
 * Live topology payload -> render model.
 *
 * Shared by the Topology page and the dashboard's LLDP topology card, so both
 * classify device state, link state and identity exactly the same way.
 */
import type { TopologyLive } from "../../lib/topologyApi";
import { deviceState, freshnessLabel, linkState, type DeviceState } from "../../lib/topology/status";
import { formatBps, formatPercent, trafficDirection, utilizationBand } from "../../lib/topology/traffic";
import { isUnknownResource, resolveVendorVisual } from "../../lib/topology/vendors";
import type { RenderEdge, RenderModel, RenderNode } from "./topologyRenderTypes";

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

export function buildRenderModel(payload: TopologyLive): RenderModel {
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
