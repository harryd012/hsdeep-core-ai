/** Layout determinism, coverage and fit-to-screen tests (Phase 9.5). */
import assert from "node:assert/strict";
import test from "node:test";

import {
  LAYOUTS,
  fitViewport,
  layoutBounds,
  layoutNodes,
  type LayoutKind,
} from "../../src/lib/topology/layout.ts";

const NODES = [
  { id: "d-fw", siteId: "site-a" },
  { id: "d-core", siteId: "site-a" },
  { id: "d-acc1", siteId: "site-a" },
  { id: "d-acc2", siteId: "site-b" },
  { id: "d-loose", siteId: null },
];
const EDGES = [
  { source: "d-fw", target: "d-core" },
  { source: "d-core", target: "d-acc1" },
  { source: "d-core", target: "d-acc2" },
];
const KINDS: LayoutKind[] = ["hierarchical", "force", "radial", "site-grouped"];

test("every declared layout is actually implemented", () => {
  assert.deepEqual(
    LAYOUTS.map((entry) => entry.kind),
    KINDS
  );
  for (const kind of KINDS) {
    assert.ok(layoutNodes(NODES, EDGES, kind).length > 0, kind);
  }
});

test("layouts are deterministic", () => {
  for (const kind of KINDS) {
    assert.deepEqual(layoutNodes(NODES, EDGES, kind), layoutNodes(NODES, EDGES, kind), kind);
  }
  // Input order must not matter: the same graph in a different order is the same picture.
  const shuffled = [...NODES].reverse();
  assert.deepEqual(layoutNodes(shuffled, EDGES, "force"), layoutNodes(NODES, EDGES, "force"));
});

test("every layout places every node exactly once", () => {
  for (const kind of KINDS) {
    const placed = layoutNodes(NODES, EDGES, kind).map((p) => p.id).sort();
    assert.deepEqual(placed, NODES.map((n) => n.id).sort(), kind);
  }
});

test("an empty graph lays out to nothing", () => {
  for (const kind of KINDS) {
    assert.deepEqual(layoutNodes([], [], kind), []);
  }
});

test("hierarchical puts a root above its children", () => {
  const positions = new Map(
    layoutNodes(NODES, EDGES, "hierarchical").map((p) => [p.id, p])
  );
  assert.ok(positions.get("d-fw")!.y < positions.get("d-core")!.y);
  assert.ok(positions.get("d-core")!.y < positions.get("d-acc1")!.y);
});

test("radial centres the busiest node and rings the rest", () => {
  const positions = new Map(layoutNodes(NODES, EDGES, "radial").map((p) => [p.id, p]));
  // d-core has the highest degree and must sit at the centre.
  assert.equal(positions.get("d-core")!.x, 0);
  assert.equal(positions.get("d-core")!.y, 0);
  assert.ok(Math.hypot(positions.get("d-acc1")!.x, positions.get("d-acc1")!.y) > 0);
});

test("site grouping separates sites and keeps unsited devices", () => {
  const groups = new Map(layoutNodes(NODES, [], "site-grouped").map((p) => [p.id, p]));
  assert.ok(groups.get("d-acc1")!.x < groups.get("d-acc2")!.x);
  // The unsited device is still placed, not silently dropped.
  assert.ok(groups.has("d-loose"));
});

test("bounds and fit viewport are honest about empty input", () => {
  assert.equal(layoutBounds([]), null);
  assert.equal(fitViewport([], { width: 800, height: 600 }), null);
  assert.equal(fitViewport([{ x: 0, y: 0 }], { width: 0, height: 0 }), null);
});

test("fit viewport centres a non-empty graph inside the canvas", () => {
  const points = [
    { x: -100, y: -100 },
    { x: 100, y: 100 },
  ];
  const viewport = fitViewport(points, { width: 1000, height: 800 }, 100);
  assert.ok(viewport);
  const centreX = (points[0].x + points[1].x) / 2;
  const screenCentre = centreX * viewport!.scale + viewport!.offsetX;
  assert.equal(Math.round(screenCentre), 500);
  assert.ok(viewport!.scale > 0);
});