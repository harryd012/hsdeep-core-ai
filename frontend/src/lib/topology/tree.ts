/**
 * Tree topology layout (Meraki-style L2 view).
 *
 * Linked devices are drawn as a left-to-right tree: one root per connected
 * component (the most upstream-looking device), one column per hop, leaves
 * stacked on consecutive rows and every parent centred on its children.
 * Devices with no recorded relationship are NOT placed on the canvas; the
 * caller lists them separately, because a grid of hundreds of unconnected
 * cards hides the real links.
 *
 * Pure and deterministic like `layout.ts`: same graph + same collapsed set ->
 * same coordinates. No DOM, no React, no API.
 */

export interface TreeNodeInput {
  id: string;
  /** Icon key from `resolveVendorVisual`; decides who is upstream. */
  iconKey?: string | null;
}

export interface TreeEdgeInput {
  id: string;
  source: string;
  target: string;
}

export interface TreeLink {
  /** Id of the RenderEdge this link draws. */
  id: string;
  parent: string;
  child: string;
}

export interface TreeOptions {
  /** Node ids whose subtree is folded away. */
  collapsed?: ReadonlySet<string>;
  /** Horizontal distance between hops. */
  xGap?: number;
  /** Vertical distance between leaf rows. */
  yGap?: number;
  /** Extra rows between two components. */
  componentGap?: number;
  /**
   * Preferred roots, best first (the L3 view passes its default-route exits).
   * A component containing one of these is rooted there; others fall back to
   * the role ranking.
   */
  rootOrder?: readonly string[];
}

export interface TreeLayout {
  /** Positions of the nodes that are drawn (collapsed descendants excluded). */
  positions: Map<string, { x: number; y: number }>;
  /** Parent -> child links between drawn nodes. */
  links: TreeLink[];
  /** Recorded edges that are not part of the spanning tree (loops, redundancy). */
  crossLinks: TreeLink[];
  roots: string[];
  depth: Map<string, number>;
  /** Direct children in the full (uncollapsed) tree. */
  children: Map<string, string[]>;
  /** All descendants in the full tree, for "+N" badges on collapsed nodes. */
  descendants: Map<string, number>;
  /** Devices with no recorded relationship. Never positioned. */
  unlinked: string[];
}

const DEFAULTS = { xGap: 220, yGap: 30, componentGap: 1.5 } as const;

/**
 * Lower rank = more upstream. A firewall or router is the natural root of a
 * site (Meraki roots its tree at the security appliance / default route).
 */
const ROLE_RANK: Record<string, number> = {
  firewall: 0,
  router: 1,
  "wireless-controller": 2,
  switch: 3,
  cloud: 4,
};

function rank(node: TreeNodeInput | undefined): number {
  return ROLE_RANK[node?.iconKey ?? ""] ?? 9;
}

export function buildTreeLayout(
  nodes: readonly TreeNodeInput[],
  edges: readonly TreeEdgeInput[],
  options: TreeOptions = {}
): TreeLayout {
  const xGap = options.xGap ?? DEFAULTS.xGap;
  const yGap = options.yGap ?? DEFAULTS.yGap;
  const componentGap = options.componentGap ?? DEFAULTS.componentGap;
  const collapsed = options.collapsed ?? new Set<string>();
  const preferred = new Map((options.rootOrder ?? []).map((id, index) => [id, index]));

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const neighbours = new Map<string, { id: string; edge: string }[]>();
  for (const node of nodes) neighbours.set(node.id, []);
  for (const edge of [...edges].sort((a, b) => a.id.localeCompare(b.id))) {
    if (edge.source === edge.target) continue;
    if (!neighbours.has(edge.source) || !neighbours.has(edge.target)) continue;
    neighbours.get(edge.source)!.push({ id: edge.target, edge: edge.id });
    neighbours.get(edge.target)!.push({ id: edge.source, edge: edge.id });
  }

  const ids = [...byId.keys()].sort();
  const unlinked = ids.filter((id) => neighbours.get(id)!.length === 0);
  const linked = ids.filter((id) => neighbours.get(id)!.length > 0);

  // Connected components, each with its most upstream-looking root.
  const seen = new Set<string>();
  const components: string[][] = [];
  for (const start of linked) {
    if (seen.has(start)) continue;
    const members: string[] = [];
    const queue = [start];
    seen.add(start);
    while (queue.length) {
      const id = queue.shift()!;
      members.push(id);
      for (const next of neighbours.get(id)!) {
        if (seen.has(next.id)) continue;
        seen.add(next.id);
        queue.push(next.id);
      }
    }
    components.push(members);
  }
  const pickRoot = (members: string[]) =>
    [...members].sort((a, b) => {
      const byPreference = (preferred.get(a) ?? Infinity) - (preferred.get(b) ?? Infinity);
      if (byPreference) return byPreference;
      const byRank = rank(byId.get(a)) - rank(byId.get(b));
      if (byRank) return byRank;
      const byDegree = neighbours.get(b)!.length - neighbours.get(a)!.length;
      return byDegree || a.localeCompare(b);
    })[0];

  // BFS spanning tree per component. Non-tree edges become cross links.
  const parentEdge = new Map<string, string>();
  const children = new Map<string, string[]>();
  const depth = new Map<string, number>();
  const roots: string[] = [];
  const treeEdges = new Set<string>();
  const ordered = components
    .map((members) => ({ members, root: pickRoot(members) }))
    // Biggest network first, then a stable order.
    .sort((a, b) => b.members.length - a.members.length || a.root.localeCompare(b.root));

  for (const { root } of ordered) {
    roots.push(root);
    depth.set(root, 0);
    children.set(root, []);
    const queue = [root];
    while (queue.length) {
      const id = queue.shift()!;
      for (const next of neighbours.get(id)!) {
        if (depth.has(next.id)) continue;
        depth.set(next.id, depth.get(id)! + 1);
        parentEdge.set(next.id, next.edge);
        treeEdges.add(next.edge);
        children.get(id)!.push(next.id);
        children.set(next.id, []);
        queue.push(next.id);
      }
    }
  }

  const descendants = new Map<string, number>();
  const countDescendants = (id: string): number => {
    let total = 0;
    for (const child of children.get(id) ?? []) total += 1 + countDescendants(child);
    descendants.set(id, total);
    return total;
  };
  for (const root of roots) countDescendants(root);

  // Children with their own subtree first, so access switches sit above leaves.
  for (const list of children.values()) {
    list.sort((a, b) => {
      const bySize = (descendants.get(b) ?? 0) - (descendants.get(a) ?? 0);
      if (bySize) return bySize;
      const byRank = rank(byId.get(a)) - rank(byId.get(b));
      return byRank || a.localeCompare(b);
    });
  }

  // Tidy placement: leaves take consecutive rows, parents centre on children.
  const positions = new Map<string, { x: number; y: number }>();
  let row = 0;
  const place = (id: string): number => {
    const kids = collapsed.has(id) ? [] : children.get(id) ?? [];
    let y: number;
    if (!kids.length) {
      y = row * yGap;
      row += 1;
    } else {
      const ys = kids.map(place);
      y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    positions.set(id, { x: (depth.get(id) ?? 0) * xGap, y });
    return y;
  };
  roots.forEach((root, index) => {
    if (index > 0) row += componentGap;
    place(root);
  });

  const links: TreeLink[] = [];
  for (const [child, edgeId] of parentEdge) {
    if (!positions.has(child)) continue;
    const parent = [...children.entries()].find(([, kids]) => kids.includes(child))?.[0];
    if (parent && positions.has(parent)) links.push({ id: edgeId, parent, child });
  }
  links.sort((a, b) => a.id.localeCompare(b.id));

  const crossLinks: TreeLink[] = [];
  for (const edge of edges) {
    if (treeEdges.has(edge.id) || edge.source === edge.target) continue;
    if (positions.has(edge.source) && positions.has(edge.target)) {
      crossLinks.push({ id: edge.id, parent: edge.source, child: edge.target });
    }
  }
  crossLinks.sort((a, b) => a.id.localeCompare(b.id));

  return { positions, links, crossLinks, roots, depth, children, descendants, unlinked };
}

/** Nodes to collapse so the tree shows `maxDepth` hops (0 = roots only). Only
 * the frontier folds, so expanding one of them opens exactly one more level. */
export function collapseBeyond(layout: Pick<TreeLayout, "depth" | "children">, maxDepth: number): Set<string> {
  const result = new Set<string>();
  for (const [id, level] of layout.depth) {
    if (level === maxDepth && (layout.children.get(id)?.length ?? 0) > 0) result.add(id);
  }
  return result;
}

/** Above this many linked devices the tree opens collapsed, like Meraki's guard. */
export const LARGE_TREE_THRESHOLD = 400;

/** Smooth left-to-right link, the Meraki tree's curve. */
export function treeLinkPath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const mid = (from.x + to.x) / 2;
  return `M${from.x},${from.y} C${mid},${from.y} ${mid},${to.y} ${to.x},${to.y}`;
}

export interface UnlinkedGroup<T> {
  key: string;
  items: T[];
}

/** Group unconnected devices (by device type, say), biggest group first. */
export function groupBy<T>(items: readonly T[], keyOf: (item: T) => string): UnlinkedGroup<T>[] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(item);
  }
  return [...groups.entries()]
    .map(([key, list]) => ({ key, items: list }))
    .sort((a, b) => b.items.length - a.items.length || a.key.localeCompare(b.key));
}
