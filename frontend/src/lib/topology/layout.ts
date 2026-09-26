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

export type LayoutKind = "hierarchical" | "force" | "radial" | "site-grouped";

export const LAYOUTS: readonly { kind: LayoutKind; label: string; hint: string }[] = [
  { kind: "hierarchical", label: "Hierarchical", hint: "Roots at the top, one row per hop." },
  { kind: "force", label: "Force", hint: "Clusters connected devices together." },
  { kind: "radial", label: "Radial", hint: "Concentric rings around the busiest device." },
  { kind: "site-grouped", label: "Site grouped", hint: "One cluster per site." },
];

export interface LayoutNode {
  id: string;
  /** Site grouping key. Null/undefined means the resource has no site. */
  siteId?: string | null;
  type?: string;
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
  for (const level of [...rows.keys()].sort((a, b) => a - b)) {
    const ids = rows.get(level)!;
    const span = (ids.length - 1) * options.xGap;
    ids.forEach((id, index) => {
      result.push({ id, x: index * options.xGap - span / 2, y: level * options.yGap });
    });
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

/** Positions for one layout. Deterministic in (nodes, edges, kind, options). */
export function layoutNodes(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  kind: LayoutKind,
  options: LayoutOptions = {}
): LayoutResult[] {
  const resolved: Required<LayoutOptions> = { ...DEFAULTS, ...options };
  switch (kind) {
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
