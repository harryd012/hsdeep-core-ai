/**
 * L3 (routed) topology, Meraki-style.
 *
 * Turns the `/topology/l3` read model into tree input for `tree.ts`: devices
 * joined by shared subnets, subnet "pipes" for subnets with 3+ devices,
 * next-hop diamonds for routes that leave the known network, rooted at the
 * default-route exit. Device state/icon/name are NOT here: device ids match the
 * live graph's, and the panel takes those from the live model.
 *
 * Pure: no DOM, no React, no API.
 */
import type { L3Device, L3Edge, L3Node, L3Route, TopologyL3 } from "../topologyApi";

export interface L3TreeEdge {
  id: string;
  source: string;
  target: string;
  kind: L3Edge["kind"];
  /** Address shown at each end of the link (the L3 equivalent of a port). */
  sourceLabel: string | null;
  targetLabel: string | null;
  /** One-line description for the link tooltip / detail. */
  title: string;
}

export interface L3Graph {
  /** Device node ids drawn on the map (all present in `knownDeviceIds`). */
  deviceIds: string[];
  /** Subnet pipes and next-hop diamonds that still touch a drawn device. */
  synthetic: L3Node[];
  edges: L3TreeEdge[];
  /** Root preference for `buildTreeLayout`. */
  rootOrder: string[];
}

const isDevice = (id: string) => id.startsWith("device:");

function title(edge: L3Edge): string {
  if (edge.kind === "route") {
    const via = edge.source_address ? `via ${edge.source_address}` : "route";
    const to = edge.destinations.length ? ` → ${edge.destinations.join(", ")}` : "";
    return `${via}${to}`;
  }
  const ends = [edge.source_address, edge.target_address].filter(Boolean).join(" ↔ ");
  return ends ? `${edge.network} · ${ends}` : edge.network ?? "subnet";
}

/**
 * Build the drawable L3 graph. Devices outside `knownDeviceIds` (filtered out,
 * or not in the live view) are dropped with every edge that touches them, and
 * a pipe or diamond left with nothing to connect is dropped too, so the map
 * never shows a link to something it is not drawing.
 */
export function buildL3Graph(payload: TopologyL3, knownDeviceIds: ReadonlySet<string>): L3Graph {
  const synthetic = new Map(payload.nodes.map((node) => [node.id, node]));
  const drawable = (id: string) => (isDevice(id) ? knownDeviceIds.has(id) : synthetic.has(id));

  const kept = payload.edges.filter((edge) => drawable(edge.source) && drawable(edge.target));
  // A pipe with fewer than two drawn members, or a diamond with no drawn
  // device, carries no information once its devices are filtered away.
  const degree = new Map<string, number>();
  for (const edge of kept) {
    for (const end of [edge.source, edge.target]) {
      if (!isDevice(end)) degree.set(end, (degree.get(end) ?? 0) + 1);
    }
  }
  const keepSynthetic = (id: string) => {
    const node = synthetic.get(id);
    if (!node) return false;
    return (degree.get(id) ?? 0) >= (node.kind === "subnet" ? 2 : 1);
  };
  const edges = kept
    .filter((edge) => (isDevice(edge.source) || keepSynthetic(edge.source)) && (isDevice(edge.target) || keepSynthetic(edge.target)))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: edge.kind,
      sourceLabel: edge.source_address,
      targetLabel: edge.target_address,
      title: title(edge),
    }));

  const used = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  return {
    deviceIds: [...used].filter(isDevice).sort(),
    synthetic: payload.nodes.filter((node) => used.has(node.id)),
    edges,
    rootOrder: payload.roots.filter((id) => used.has(id)),
  };
}

const MAX_LINES = 8;

function lines(items: string[]): string {
  if (items.length <= MAX_LINES) return items.join("\n");
  return [...items.slice(0, MAX_LINES), `+${items.length - MAX_LINES} more`].join("\n");
}

function vrfSuffix(vrf: string): string {
  return vrf && vrf !== "-" ? ` [${vrf}]` : "";
}

function routeLine(route: L3Route, nameOf: (id: string) => string | null): string {
  const name = route.next_hop_node ? nameOf(route.next_hop_node) : null;
  // A next-hop diamond is named by its own address; don't repeat it.
  const target = name && name !== route.next_hop ? name : null;
  const via = route.next_hop ? `via ${route.next_hop}${target ? ` (${target})` : ""}` : route.interface ? `dev ${route.interface}` : "";
  return `${route.destination} ${via}`.trim() + vrfSuffix(route.vrf);
}

/** Hover-card rows for a device on the L3 map: subnets/IPs and routes. */
export function l3DeviceRows(device: L3Device | undefined, nameOf: (id: string) => string | null): [string, string][] {
  if (!device) return [["Addresses", "Not reported by any collector"]];
  const rows: [string, string][] = [];
  if (device.addresses.length) {
    rows.push([
      "Interface IPs",
      lines(
        device.addresses.map(
          (a) => `${a.address}/${a.prefix_length}${a.interface ? ` · ${a.interface}` : ""}${vrfSuffix(a.vrf)}`
        )
      ),
    ]);
    const subnets = [...new Set(device.addresses.map((a) => a.network + vrfSuffix(a.vrf)))];
    rows.push(["Subnets", lines(subnets)]);
  } else {
    rows.push(["Addresses", "Not reported by any collector"]);
  }
  if (device.default_route) rows.push(["Default route", routeLine(device.default_route, nameOf)]);
  const statics = device.static_routes.filter((route) => route.destination !== "0.0.0.0/0" && route.destination !== "::/0");
  if (statics.length) rows.push(["Static routes", lines(statics.map((route) => routeLine(route, nameOf)))]);
  const dynamic = Object.entries(device.routes_by_protocol)
    .filter(([protocol]) => protocol !== "static" && protocol !== "connected")
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([protocol, count]) => `${protocol} ${count}`);
  if (dynamic.length) rows.push(["Dynamic routes", dynamic.join(" · ")]);
  return rows;
}

/** Hover-card rows for a subnet pipe or next-hop diamond. */
export function l3SyntheticRows(node: L3Node): [string, string][] {
  if (node.kind === "subnet") {
    const rows: [string, string][] = [["Subnet", node.network ?? node.label]];
    if (node.vrf !== "-") rows.push(["VRF", node.vrf]);
    rows.push(["Devices", String(node.member_count ?? 0)]);
    return rows;
  }
  const rows: [string, string][] = [
    ["Next hop", node.address ?? node.label],
    ["Device", "Not a monitored device"],
    ["Routes via", String(node.route_count ?? 0)],
  ];
  if (node.vrf !== "-") rows.push(["VRF", node.vrf]);
  return rows;
}

/** One sentence on how much of the network reported L3 data. */
export function l3CoverageText(coverage: TopologyL3["coverage"]): string {
  const { devices_with_addresses: withAddresses, devices_total: total, providers } = coverage;
  if (!withAddresses) {
    return "No collector has reported interface addresses yet. SNMP (IP-MIB), FortiGate, Palo Alto and Linux SSH sources report them on their next poll.";
  }
  const from = providers.length ? ` (${providers.join(", ")})` : "";
  return `Interface addresses reported for ${withAddresses} of ${total} devices${from}; ${coverage.subnets} shared subnet${coverage.subnets === 1 ? "" : "s"}.`;
}
