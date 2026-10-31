/** L3 topology mapping tests. */
import assert from "node:assert/strict";
import test from "node:test";

import { buildL3Graph, l3CoverageText, l3DeviceRows, l3SyntheticRows } from "../../src/lib/topology/l3.ts";
import { buildTreeLayout } from "../../src/lib/topology/tree.ts";
import type { L3Edge, L3Node, TopologyL3 } from "../../src/lib/topologyApi.ts";

const pipe: L3Node = {
  id: "subnet:-:-:10.0.0.0/24",
  kind: "subnet",
  label: "10.0.0.0/24",
  vrf: "-",
  site_id: null,
  network: "10.0.0.0/24",
  member_count: 3,
  address: null,
  route_count: null,
};
const internet: L3Node = {
  id: "nexthop:-:-:203.0.113.1",
  kind: "next_hop",
  label: "203.0.113.1",
  vrf: "-",
  site_id: null,
  network: null,
  member_count: null,
  address: "203.0.113.1",
  route_count: 1,
};

function edge(id: string, source: string, target: string, extra: Partial<L3Edge> = {}): L3Edge {
  return {
    id,
    source,
    target,
    kind: "subnet",
    network: "10.0.0.0/24",
    vrf: "-",
    source_address: null,
    source_interface: null,
    target_address: null,
    target_interface: null,
    destinations: [],
    ...extra,
  };
}

const PAYLOAD: TopologyL3 = {
  nodes: [pipe, internet],
  edges: [
    edge("p-fw", pipe.id, "device:fw", { target_address: "10.0.0.1" }),
    edge("p-sw1", pipe.id, "device:sw1", { target_address: "10.0.0.2" }),
    edge("p-sw2", pipe.id, "device:sw2", { target_address: "10.0.0.3" }),
    edge("r-fw", internet.id, "device:fw", { kind: "route", network: null, source_address: "203.0.113.1", destinations: ["0.0.0.0/0"] }),
  ],
  devices: {
    "device:fw": {
      name: "fw",
      device_type: "firewall",
      addresses: [
        { address: "10.0.0.1", prefix_length: 24, network: "10.0.0.0/24", interface: "port1", vrf: "-", is_primary: true },
        { address: "203.0.113.2", prefix_length: 29, network: "203.0.113.0/29", interface: "wan1", vrf: "-", is_primary: true },
      ],
      static_routes: [
        { destination: "0.0.0.0/0", next_hop: "203.0.113.1", interface: "wan1", protocol: "static", metric: null, vrf: "-", next_hop_node: internet.id },
        { destination: "172.16.0.0/12", next_hop: "10.0.0.2", interface: "port1", protocol: "static", metric: null, vrf: "-", next_hop_node: "device:sw1" },
      ],
      default_route: { destination: "0.0.0.0/0", next_hop: "203.0.113.1", interface: "wan1", protocol: "static", metric: null, vrf: "-", next_hop_node: internet.id },
      routes_by_protocol: { static: 2, bgp: 40 },
    },
  },
  roots: [internet.id],
  unlinked: [],
  coverage: { devices_total: 5, devices_with_addresses: 3, devices_with_routes: 1, subnets: 1, providers: ["fortigate", "snmp"] },
  site_id: null,
  generated_at: "2026-10-29T00:00:00Z",
};

const ALL = new Set(["device:fw", "device:sw1", "device:sw2"]);

test("maps pipes, diamonds and edges with addresses as port labels", () => {
  const graph = buildL3Graph(PAYLOAD, ALL);
  assert.deepEqual(graph.deviceIds, ["device:fw", "device:sw1", "device:sw2"]);
  assert.deepEqual(graph.synthetic.map((n) => n.id), [pipe.id, internet.id]);
  const route = graph.edges.find((e) => e.id === "r-fw")!;
  assert.equal(route.sourceLabel, "203.0.113.1");
  assert.equal(route.title, "via 203.0.113.1 → 0.0.0.0/0");
  assert.equal(graph.edges.find((e) => e.id === "p-sw1")!.targetLabel, "10.0.0.2");
});

test("roots the tree at the default-route exit", () => {
  const graph = buildL3Graph(PAYLOAD, ALL);
  const nodes = [
    ...graph.deviceIds.map((id) => ({ id, iconKey: id === "device:fw" ? "firewall" : "switch" })),
    ...graph.synthetic.map((n) => ({ id: n.id })),
  ];
  const tree = buildTreeLayout(nodes, graph.edges, { rootOrder: graph.rootOrder });
  assert.deepEqual(tree.roots, [internet.id]);
  assert.equal(tree.depth.get("device:fw"), 1);
  assert.equal(tree.depth.get(pipe.id), 2);
  assert.equal(tree.depth.get("device:sw1"), 3);
});

test("without rootOrder the role ranking still decides", () => {
  const graph = buildL3Graph(PAYLOAD, ALL);
  const nodes = [
    ...graph.deviceIds.map((id) => ({ id, iconKey: id === "device:fw" ? "firewall" : "switch" })),
    ...graph.synthetic.map((n) => ({ id: n.id })),
  ];
  assert.deepEqual(buildTreeLayout(nodes, graph.edges).roots, ["device:fw"]);
});

test("filtered-out devices take their links, and orphaned pipes, with them", () => {
  const graph = buildL3Graph(PAYLOAD, new Set(["device:fw", "device:sw1"]));
  // The pipe still joins two drawn devices.
  assert.ok(graph.synthetic.some((n) => n.id === pipe.id));
  assert.equal(graph.edges.some((e) => e.target === "device:sw2"), false);

  const lonely = buildL3Graph(PAYLOAD, new Set(["device:sw1"]));
  assert.deepEqual(lonely.synthetic, []);
  assert.deepEqual(lonely.edges, []);
  assert.deepEqual(lonely.rootOrder, []);
});

test("device hover lists IPs, subnets, default and static routes", () => {
  const rows = Object.fromEntries(l3DeviceRows(PAYLOAD.devices["device:fw"], (id) => (id === "device:sw1" ? "sw1" : null)));
  assert.equal(rows["Interface IPs"], "10.0.0.1/24 · port1\n203.0.113.2/29 · wan1");
  assert.equal(rows["Subnets"], "10.0.0.0/24\n203.0.113.0/29");
  assert.equal(rows["Default route"], "0.0.0.0/0 via 203.0.113.1");
  assert.equal(rows["Static routes"], "172.16.0.0/12 via 10.0.0.2 (sw1)");
  assert.equal(rows["Dynamic routes"], "bgp 40");
});

test("a device with no L3 data says so instead of showing nothing", () => {
  assert.deepEqual(l3DeviceRows(undefined, () => null), [["Addresses", "Not reported by any collector"]]);
});

test("synthetic node rows", () => {
  assert.deepEqual(l3SyntheticRows(pipe), [["Subnet", "10.0.0.0/24"], ["Devices", "3"]]);
  assert.equal(Object.fromEntries(l3SyntheticRows(internet))["Next hop"], "203.0.113.1");
});

test("coverage text is honest about zero coverage", () => {
  assert.match(l3CoverageText({ ...PAYLOAD.coverage, devices_with_addresses: 0 }), /No collector has reported/);
  assert.equal(
    l3CoverageText(PAYLOAD.coverage),
    "Interface addresses reported for 3 of 5 devices (fortigate, snmp); 1 shared subnet."
  );
});
