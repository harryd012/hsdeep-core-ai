/**
 * Topology layouts (Phase 9.5).
 *
 * Four working layouts, all DETERMINISTIC: the same graph always produces the
 * same coordinates. There is no `Math.random`, no time input and no dependence on
 * object key order, so switching layouts and refreshing data can never make the
 * graph jump around for no reason.
 *
 * These functions take a graph and return positions; they touch no DOM, no React
 * state and no API.
 */

export type LayoutKind = "connected-first" | "hierarchical" | "force" | "radial" | "site-grouped";

export const LAYOUTS: readonly { kind: LayoutKind; label: string; hint: string }[] = [
  { kind: "connected-first", label: "Connected first", hint: "Linked devices as a hierarchy; unlinked devices grouped by state below." },
  { kind: "hierarchical", label: "Hierarchical", hint: "Roots at the top, one row per hop." },
  { kind: "force", label: "Force", hint: "Clusters connected devices together." },
  { kind: "radial", label: "Radial", hint: "Concentric rings around the busiest device." },
  { kind: "site-grouped", label: "Site grouped", hint: "One cluster per site." },
];

/**
 * What the Layout menu offers: the Meraki-style tree (laid out by
 * `tree.ts`, which lists unconnected devices instead of placing them) plus the
 * general layouts above.
 */
export type ViewKind = "tree" | LayoutKind;

export const VIEWS: readonly { kind: ViewKind; label: string; hint: string }[] = [
  { kind: "tree", label: "Tree (L2)", hint: "Upstream on the left, one column per hop; unconnected devices listed aside." },
  ...LAYOUTS,
];

export interface LayoutNode {
  id: string;
  /** Site grouping key. Null/undefined means the resource has no site. */
  siteId?: string | null;
  type?: string;
  /** Cluster key for unlinked devices in the connected-first layout (e.g. state). */
  group?: string | null;
}

export interface LayoutEdge {
  source: string;
  target: string;
}

export interface Point {
  x: number;
  y: number;
}

export interface LayoutResult extends Point {
  id: string;
}

export interface LayoutOptions {
  /** Horizontal spacing unit. */
  xGap?: number;
  /** Vertical spacing unit between layers / rings. */
  yGap?: number;
  /** Fixed iteration count for the force layout (never time-based). */
  iterations?: number;
}

const DEFAULTS = { xGap: 190, yGap: 130, iterations: 140 } as const;

function sorted(nodes: readonly LayoutNode[]): LayoutNode[] {
  return [...nodes].sort((a, b) => a.id.localeCompare(b.id));
}

function adjacency(nodes: readonly LayoutNode[], edges: readonly LayoutEdge[]) {
  const neighbours = new Map<string, string[]>();
  const incoming = new Map<string, number>();
  for (const node of nodes) {
    neighbours.set(node.id, []);
    incoming.set(node.id, 0);
  }
  for (const edge of edges) {
    if (!neighbours.has(edge.source) || !neighbours.has(edge.target)) continue;
    neighbours.get(edge.source)!.push(edge.target);
    neighbours.get(edge.target)!.push(edge.source);
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
  }
  for (const list of neighbours.values()) list.sort();
  return { neighbours, incoming };
}

/** Hop count from a set of roots. A component with no root is placed on layer 1. */
function depths(
  nodes: readonly LayoutNode[],
  neighbours: Map<string, string[]>,
  roots: readonly string[]
): Map<string, number> {
  const depth = new Map<string, number>();
  const queue: string[] = [];
  for (const root of roots) {
    depth.set(root, 0);
    queue.push(root);
  }
  while (queue.length) {
    const id = queue.shift()!;
    const current = depth.get(id) ?? 0;
    for (const next of neighbours.get(id) ?? []) {
      if (depth.has(next)) continue;
      depth.set(next, current + 1);
      queue.push(next);
    }
  }
  for (const node of nodes) if (!depth.has(node.id)) depth.set(node.id, 1);
  return depth;
}

/** Widest layer row before the layout wraps it into sub-rows. */
const MAX_NODES_PER_ROW = 10;

function hierarchical(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: Required<LayoutOptions>
): LayoutResult[] {
  const ordered = sorted(nodes);
  if (!ordered.length) return [];
  const { neighbours, incoming } = adjacency(ordered, edges);
  const roots = ordered
    .filter((node) => (incoming.get(node.id) ?? 0) === 0)
    .map((node) => node.id);
  const depth = depths(ordered, neighbours, roots.length ? roots : [ordered[0].id]);

  const rows = new Map<number, string[]>();
  for (const node of ordered) {
    const level = depth.get(node.id) ?? 0;
    if (!rows.has(level)) rows.set(level, []);
    rows.get(level)!.push(node.id);
  }

  const result: LayoutResult[] = [];
  let rowCursor = 0;
  for (const level of [...rows.keys()].sort((a, b) => a - b)) {
    const ids = rows.get(level)!;
    /**
     * A very wide layer (e.g. hundreds of devices with no recorded relationship
     * yet) would otherwise be laid out as one enormous row that overflows the
     * canvas and overlaps itself. Wrapping keeps every node legible while
     * preserving the layer order that makes the hierarchy readable.
     */
    for (let start = 0; start < ids.length; start += MAX_NODES_PER_ROW) {
      const slice = ids.slice(start, start + MAX_NODES_PER_ROW);
      const span = (slice.length - 1) * options.xGap;
      slice.forEach((id, index) => {
        result.push({
          id,
          x: index * options.xGap - span / 2,
          y: rowCursor * options.yGap,
        });
      });
      rowCursor += 1;
    }
  }
  return result;
}


function radial(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: Required<LayoutOptions>
): LayoutResult[] {
  const ordered = sorted(nodes);
  if (!ordered.length) return [];
  const { neighbours } = adjacency(ordered, edges);
  const centre =
    [...ordered].sort((a, b) => {
      const degree =
        (neighbours.get(b.id)?.length ?? 0) - (neighbours.get(a.id)?.length ?? 0);
      return degree !== 0 ? degree : a.id.localeCompare(b.id);
    })[0]?.id ?? ordered[0].id;

  const ring = depths(ordered, neighbours, [centre]);
  const byRing = new Map<number, string[]>();
  for (const node of ordered) {
    const level = ring.get(node.id) ?? 0;
    if (!byRing.has(level)) byRing.set(level, []);
    byRing.get(level)!.push(node.id);
  }

  const result: LayoutResult[] = [];
  for (const level of [...byRing.keys()].sort((a, b) => a - b)) {
    const ids = byRing.get(level)!;
    if (level === 0) {
      for (const id of ids) result.push({ id, x: 0, y: 0 });
      continue;
    }
    const radius = level * options.yGap;
    ids.forEach((id, index) => {
      const angle = (2 * Math.PI * index) / ids.length - Math.PI / 2;
      result.push({ id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
    });
  }
  return result;
}

/**
 * Deterministic force-directed layout (Fruchterman-Reingold style).
 *
 * The initial placement is a circle ordered by id and the iteration count is
 * fixed, so the result depends only on the graph. Temperature cools by a constant
 * factor instead of a clock.
 */
function force(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: Required<LayoutOptions>
): LayoutResult[] {
  const ordered = sorted(nodes);
  const count = ordered.length;
  if (!count) return [];
  const size = Math.max(options.xGap, Math.sqrt(count) * options.xGap);
  const positions = new Map<string, Point>();

  ordered.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / count;
    positions.set(node.id, { x: Math.cos(angle) * size, y: Math.sin(angle) * size });
  });

  const links = edges.filter(
    (edge) => positions.has(edge.source) && positions.has(edge.target)
  );
  const k = Math.sqrt((size * size) / count);
  let temperature = size / 8;

  for (let iteration = 0; iteration < options.iterations; iteration += 1) {
    const displacement = new Map<string, Point>();
    for (const node of ordered) displacement.set(node.id, { x: 0, y: 0 });

    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const a = positions.get(ordered[i].id)!;
        const b = positions.get(ordered[j].id)!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distance = Math.hypot(dx, dy);
        if (distance === 0) {
          // Deterministic tie-break instead of a random nudge.
          dx = i - j || 1;
          dy = 1;
          distance = Math.hypot(dx, dy);
        }
        const repulsion = (k * k) / distance;
        const ux = (dx / distance) * repulsion;
        const uy = (dy / distance) * repulsion;
        const da = displacement.get(ordered[i].id)!;
        const db = displacement.get(ordered[j].id)!;
        da.x += ux;
        da.y += uy;
        db.x -= ux;
        db.y -= uy;
      }
    }

    for (const link of links) {
      const a = positions.get(link.source)!;
      const b = positions.get(link.target)!;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const distance = Math.hypot(dx, dy) || 1;
      const attraction = (distance * distance) / k;
      const ux = (dx / distance) * attraction;
      const uy = (dy / distance) * attraction;
      const da = displacement.get(link.source)!;
      const db = displacement.get(link.target)!;
      da.x -= ux;
      da.y -= uy;
      db.x += ux;
      db.y += uy;
    }

    for (const node of ordered) {
      const move = displacement.get(node.id)!;
      const point = positions.get(node.id)!;
      const length = Math.hypot(move.x, move.y) || 1;
      const limited = Math.min(length, temperature) / length;
      point.x += move.x * limited;
      point.y += move.y * limited;
    }
    temperature *= 0.97;
  }

  return ordered.map((node) => {
    const point = positions.get(node.id)!;
    return {
      id: node.id,
      x: Math.round(point.x * 100) / 100,
      y: Math.round(point.y * 100) / 100,
    };
  });
}

/**
 * One cluster per site, clusters laid out left to right in site order.
 *
 * Group order and in-group order are both sorted, so the same topology always
 * produces the same picture. Devices with no site are grouped together rather
 * than dropped, because "no site recorded" is itself a fact worth seeing.
 */
function siteGrouped(
  nodes: readonly LayoutNode[],
  options: Required<LayoutOptions>
): LayoutResult[] {
  const groups = new Map<string, LayoutNode[]>();
  for (const node of sorted(nodes)) {
    const key = node.siteId?.trim() || "__unassigned__";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(node);
  }

  const result: LayoutResult[] = [];
  let columnOffset = 0;
  for (const key of [...groups.keys()].sort((a, b) => a.localeCompare(b))) {
    const members = groups.get(key)!;
    const columns = Math.max(1, Math.ceil(Math.sqrt(members.length)));
    members.forEach((node, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      result.push({
        id: node.id,
        x: columnOffset + column * options.xGap,
        y: row * options.yGap,
      });
    });
    const usedColumns = Math.ceil(members.length / columns);
    columnOffset += (usedColumns + 1) * options.xGap;
  }
  return result;
}

/**
 * Connected-first: the devices that actually have relationships are laid out
 * hierarchically at the top, and every device with NO recorded relationship is
 * packed into compact clusters below, one per `group` (e.g. device state),
 * in the caller's `groupOrder` (worst state first). On an estate where discovery
 * has found few links this keeps the real topology readable instead of burying
 * it in a wall of unconnected boxes - while still drawing every device.
 */
function connectedFirst(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: Required<LayoutOptions>,
  groupOrder: readonly string[]
): LayoutResult[] {
  const linked = linkedNodeIds(nodes, edges);
  const connected = nodes.filter((n) => linked.has(n.id));
  const result = hierarchical(connected, edges, options);
  const connectedBottom = result.length ? Math.max(...result.map((r) => r.y)) + options.yGap * 1.6 : 0;

  const groups = new Map<string, LayoutNode[]>();
  for (const node of sorted(nodes)) {
    if (linked.has(node.id)) continue;
    const key = node.group?.trim() || "__ungrouped__";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(node);
  }
  const rank = (key: string) => {
    const i = groupOrder.indexOf(key);
    return i === -1 ? groupOrder.length : i;
  };
  const keys = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  // Compact grid: tighter spacing than the linked map, a fixed row width so
  // clusters stack as readable bands centred under the connected graph.
  const cellX = options.xGap * 0.82;
  const cellY = options.yGap * 0.62;
  const perRow = 14;
  let cursorY = connectedBottom;
  for (const key of keys) {
    const members = groups.get(key)!;
    const width = (Math.min(perRow, members.length) - 1) * cellX;
    members.forEach((node, index) => {
      result.push({
        id: node.id,
        x: (index % perRow) * cellX - width / 2,
        y: cursorY + Math.floor(index / perRow) * cellY,
      });
    });
    cursorY += Math.ceil(members.length / perRow) * cellY + options.yGap * 0.9;
  }
  return result;
}

/** Ids with at least one relationship to another visible node. */
export function linkedNodeIds(nodes: readonly LayoutNode[], edges: readonly LayoutEdge[]): Set<string> {
  const ids = new Set(nodes.map((n) => n.id));
  const linked = new Set<string>();
  for (const e of edges) {
    if (ids.has(e.source) && ids.has(e.target) && e.source !== e.target) {
      linked.add(e.source);
      linked.add(e.target);
    }
  }
  return linked;
}

/** Positions for one layout. Deterministic in (nodes, edges, kind, options). */
export function layoutNodes(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  kind: LayoutKind,
  options: LayoutOptions = {},
  groupOrder: readonly string[] = []
): LayoutResult[] {
  const resolved: Required<LayoutOptions> = { ...DEFAULTS, ...options };
  switch (kind) {
    case "connected-first":
      return connectedFirst(nodes, edges, resolved, groupOrder);
    case "force":
      return force(nodes, edges, resolved);
    case "radial":
      return radial(nodes, edges, resolved);
    case "site-grouped":
      return siteGrouped(nodes, resolved);
    case "hierarchical":
    default:
      return hierarchical(nodes, edges, resolved);
  }
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
}

export function layoutBounds(points: readonly Point[]): Bounds | null {
  if (!points.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }
  return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY };
}

export interface Viewport {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/**
 * Fit-to-screen transform for a set of points.
 *
 * Returns null for an empty graph — nothing to fit, and the caller must not
 * invent a transform. The scale is clamped so a single device is not blown up to
 * fill the canvas and a very large graph stays readable.
 */
export function fitViewport(
  points: readonly Point[],
  size: { width: number; height: number },
  padding = 80
): Viewport | null {
  const bounds = layoutBounds(points);
  if (!bounds || size.width <= 0 || size.height <= 0) return null;
  const usableWidth = Math.max(1, size.width - padding * 2);
  const usableHeight = Math.max(1, size.height - padding * 2);
  const scaleX = bounds.width > 0 ? usableWidth / bounds.width : 1.5;
  const scaleY = bounds.height > 0 ? usableHeight / bounds.height : 1.5;
  const scale = Math.min(1.5, Math.max(0.05, Math.min(scaleX, scaleY)));
  const centreX = (bounds.minX + bounds.maxX) / 2;
  const centreY = (bounds.minY + bounds.maxY) / 2;
  return {
    scale,
    offsetX: size.width / 2 - centreX * scale,
    offsetY: size.height / 2 - centreY * scale,
  };
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Transform that fits the whole graph into a small minimap box.
 *
 * Unlike `fitViewport`, the scale is NOT floored: a graph spanning millions of
 * units must still fit a 180px minimap. Returns null for an empty graph, so the
 * caller hides the minimap instead of drawing an empty frame.
 */
export function minimapTransform(
  points: readonly Point[],
  size: { width: number; height: number },
  padding = 6
): Viewport | null {
  const bounds = layoutBounds(points);
  if (!bounds || size.width <= 0 || size.height <= 0) return null;
  const spanX = Math.max(bounds.width, 1);
  const spanY = Math.max(bounds.height, 1);
  const usableWidth = Math.max(1, size.width - padding * 2);
  const usableHeight = Math.max(1, size.height - padding * 2);
  const scale = Math.min(1, Math.max(1e-6, Math.min(usableWidth / spanX, usableHeight / spanY)));
  const centreX = (bounds.minX + bounds.maxX) / 2;
  const centreY = (bounds.minY + bounds.maxY) / 2;
  return {
    scale,
    offsetX: size.width / 2 - centreX * scale,
    offsetY: size.height / 2 - centreY * scale,
  };
}

/** The GRAPH-space rectangle currently visible in the canvas viewport. */
export function graphVisibleRect(
  viewport: Viewport,
  size: { width: number; height: number }
): Rect {
  const scale = viewport.scale > 0 ? viewport.scale : 1;
  return {
    x: -viewport.offsetX / scale,
    y: -viewport.offsetY / scale,
    width: size.width / scale,
    height: size.height / scale,
  };
}

/**
 * Map a graph-space rectangle into minimap space, clipped to the box.
 *
 * Clipping matters: when the canvas viewport is larger than the graph itself the
 * indicator would otherwise spill outside the minimap frame.
 */
export function toMinimapRect(
  rect: Rect,
  transform: Viewport,
  size: { width: number; height: number },
  padding = 6
): Rect {
  const x = rect.x * transform.scale + transform.offsetX;
  const y = rect.y * transform.scale + transform.offsetY;
  const width = rect.width * transform.scale;
  const height = rect.height * transform.scale;
  const minX = Math.max(padding, x);
  const minY = Math.max(padding, y);
  const maxX = Math.min(size.width - padding, x + width);
  const maxY = Math.min(size.height - padding, y + height);
  return {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}
