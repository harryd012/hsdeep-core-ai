/** Tree (Meraki-style L2) layout tests. */
import assert from "node:assert/strict";
import test from "node:test";

import {
  LARGE_TREE_THRESHOLD,
  buildTreeLayout,
  collapseBeyond,
  groupBy,
  treeLinkPath,
} from "../../src/lib/topology/tree.ts";

const NODES = [
  { id: "acc1", iconKey: "switch" },
  { id: "acc2", iconKey: "switch" },
  { id: "ap1", iconKey: "access-point" },
  { id: "ap2", iconKey: "access-point" },
  { id: "cam", iconKey: "endpoint" },
  { id: "core", iconKey: "switch" },
  { id: "fw", iconKey: "firewall" },
  { id: "loose1", iconKey: "unknown" },
  { id: "loose2", iconKey: "server" },
];
const EDGES = [
  { id: "e1", source: "core", target: "fw" },
  { id: "e2", source: "core", target: "acc1" },
  { id: "e3", source: "core", target: "acc2" },
  { id: "e4", source: "acc1", target: "ap1" },
  { id: "e5", source: "acc1", target: "ap2" },
  { id: "e6", source: "acc2", target: "cam" },
  { id: "e7", source: "acc1", target: "acc2" }, // redundant link
];

test("roots at the firewall even when it is the edge target", () => {
  const tree = buildTreeLayout(NODES, EDGES);
  assert.deepEqual(tree.roots, ["fw"]);
  assert.equal(tree.depth.get("core"), 1);
  assert.equal(tree.depth.get("ap1"), 3);
});

test("unconnected devices are listed, never positioned", () => {
  const tree = buildTreeLayout(NODES, EDGES);
  assert.deepEqual(tree.unlinked, ["loose1", "loose2"]);
  assert.equal(tree.positions.has("loose1"), false);
  assert.equal(tree.positions.size, 7);
});

test("one column per hop, parent centred on its children", () => {
  const tree = buildTreeLayout(NODES, EDGES, { xGap: 100, yGap: 10 });
  const acc1 = tree.positions.get("acc1")!;
  assert.equal(acc1.x, 200);
  assert.equal(tree.positions.get("fw")!.x, 0);
  const ys = ["ap1", "ap2"].map((id) => tree.positions.get(id)!.y);
  assert.equal(acc1.y, (ys[0] + ys[1]) / 2);
});

test("leaves never share a row", () => {
  const tree = buildTreeLayout(NODES, EDGES);
  const leafYs = ["ap1", "ap2", "cam"].map((id) => tree.positions.get(id)!.y);
  assert.equal(new Set(leafYs).size, 3);
});

test("redundant links become cross links, not tree links", () => {
  const tree = buildTreeLayout(NODES, EDGES);
  assert.equal(tree.links.length, 6);
  assert.deepEqual(tree.crossLinks.map((link) => link.id), ["e7"]);
});

test("deterministic regardless of input order", () => {
  const a = buildTreeLayout(NODES, EDGES);
  const b = buildTreeLayout([...NODES].reverse(), [...EDGES].reverse());
  assert.deepEqual([...a.positions.entries()].sort(), [...b.positions.entries()].sort());
});

test("collapsing hides the subtree and keeps the descendant count", () => {
  const tree = buildTreeLayout(NODES, EDGES, { collapsed: new Set(["acc1"]) });
  assert.equal(tree.positions.has("ap1"), false);
  assert.equal(tree.positions.has("acc1"), true);
  assert.equal(tree.descendants.get("acc1"), 2);
  assert.ok(tree.links.every((link) => link.child !== "ap1"));
});

test("collapseBeyond folds everything deeper than the requested hop", () => {
  const full = buildTreeLayout(NODES, EDGES);
  assert.deepEqual([...collapseBeyond(full, 2)].sort(), ["acc1", "acc2"]);
  assert.deepEqual([...collapseBeyond(full, 0)], ["fw"]);
  assert.ok(LARGE_TREE_THRESHOLD > 0);
});

test("separate networks are stacked, biggest first", () => {
  const tree = buildTreeLayout(
    [...NODES, { id: "r2", iconKey: "router" }, { id: "s2", iconKey: "switch" }],
    [...EDGES, { id: "e9", source: "s2", target: "r2" }]
  );
  assert.deepEqual(tree.roots, ["fw", "r2"]);
  const maxFirst = Math.max(
    ...["fw", "core", "acc1", "acc2", "ap1", "ap2", "cam"].map((id) => tree.positions.get(id)!.y)
  );
  assert.ok(tree.positions.get("s2")!.y > maxFirst);
});

test("link path is a left-to-right curve", () => {
  assert.equal(treeLinkPath({ x: 0, y: 0 }, { x: 100, y: 40 }), "M0,0 C50,0 50,40 100,40");
});

test("groupBy puts the biggest group first", () => {
  const groups = groupBy(["a", "bb", "cc", "dd", "e"], (item) => String(item.length));
  assert.deepEqual(groups.map((group) => group.key), ["2", "1"]);
});
