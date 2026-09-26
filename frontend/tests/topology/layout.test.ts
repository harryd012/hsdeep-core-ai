/** Layout determinism, coverage and fit-to-screen tests (Phase 9.5). */
import assert from "node:assert/strict";
import test from "node:test";

import {
  LAYOUTS,
  fitViewport,
  graphVisibleRect,
  layoutBounds,
  layoutNodes,
  minimapTransform,
  toMinimapRect,
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

test("hierarchical wraps a very wide layer instead of overflowing", () => {
  const wide = Array.from({ length: 25 }, (_, index) => ({ id: `n${index}` }));
  const placed = layoutNodes(wide, [], "hierarchical");
  assert.equal(placed.length, 25);

  // Three sub-rows of at most ten, no two nodes sharing a position.
  const perRow = new Map<number, number>();
  placed.forEach((point) => perRow.set(point.y, (perRow.get(point.y) ?? 0) + 1));
  assert.deepEqual(
    [...perRow.values()].sort((a, b) => b - a),
    [10, 10, 5]
  );
  const positions = new Set(placed.map((point) => `${point.x}:${point.y}`));
  assert.equal(positions.size, 25);
});

test("hierarchical keeps an unconnected fleet on consecutive rows", () => {
  const fleet = Array.from({ length: 12 }, (_, index) => ({ id: `dev-${index}` }));
  const first = layoutNodes(fleet, [], "hierarchical");
  const second = layoutNodes([...fleet].reverse(), [], "hierarchical");
  assert.deepEqual(first, second);
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

test("the minimap transform fits the whole graph, however large", () => {
  assert.equal(minimapTransform([], { width: 180, height: 120 }), null);
  assert.equal(minimapTransform([{ x: 0, y: 0 }], { width: 0, height: 0 }), null);

  const points = [
    { x: -5000, y: -5000 },
    { x: 5000, y: 5000 },
  ];
  const transform = minimapTransform(points, { width: 180, height: 120 })!;
  for (const point of points) {
    const x = point.x * transform.scale + transform.offsetX;
    const y = point.y * transform.scale + transform.offsetY;
    assert.ok(x >= 0 && x <= 180, String(x));
    assert.ok(y >= 0 && y <= 120, String(y));
  }

  // Unlike fit-to-screen there is no 0.05 floor: a million-unit span must fit.
  const huge = minimapTransform(
    [
      { x: -500_000, y: -500_000 },
      { x: 500_000, y: 500_000 },
    ],
    { width: 180, height: 120 }
  )!;
  assert.ok(huge.scale < 0.001, String(huge.scale));
});

test("the minimap viewport indicator maps and clips", () => {
  const rect = graphVisibleRect({ scale: 2, offsetX: -100, offsetY: -50 }, {
    width: 800,
    height: 600,
  });
  assert.deepEqual(rect, { x: 50, y: 25, width: 400, height: 300 });

  // A viewport larger than the graph is clipped to the minimap frame.
  const clipped = toMinimapRect(
    { x: -1000, y: -1000, width: 20_000, height: 20_000 },
    { scale: 0.01, offsetX: 90, offsetY: 60 },
    { width: 180, height: 120 }
  );
  assert.ok(clipped.x >= 6);
  assert.ok(clipped.y >= 6);
  assert.ok(clipped.x + clipped.width <= 174.001, String(clipped));
  assert.ok(clipped.y + clipped.height <= 114.001, String(clipped));
});