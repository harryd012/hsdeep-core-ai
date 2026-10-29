"use client";

/**
 * TopologyTreeCanvas — the Meraki-style L2 tree.
 *
 * Devices are small glyphs (shape = device role, colour = state) instead of
 * cards, so hundreds of devices stay readable. Each link fans out from its
 * parent to a port dot on the child's row, runs straight to the child's port
 * dot and on to the child (the LLDP-tree convention), with the recorded
 * interface names as port labels. Labels are shown for branch devices, the
 * selection and the hovered device; "Label all devices" shows the rest.
 *
 * Everything is one SVG under a pan/zoom transform. The hover card is plain
 * HTML in screen space so it never scales with the graph.
 */
import { useMemo, useState } from "react";

import type { Viewport } from "../../lib/topology/layout";
import { DEVICE_STATE_VISUALS, LINK_STATE_VISUALS } from "../../lib/topology/status";
import type { TreeLayout, TreeLink } from "../../lib/topology/tree";
import { treeLinkPath } from "../../lib/topology/tree";
import type { VisualIconKey } from "../../lib/topology/vendors";
import type { RenderEdge, RenderNode } from "./topologyRenderTypes";
import { usePanZoom } from "./usePanZoom";

/** Where the two port dots sit between parent and child, as a share of the hop. */
const PARENT_PORT_AT = 0.42;
const CHILD_PORT_AT = 0.78;
const IDLE_LINK = "rgba(148,163,184,0.55)";

export interface TopologyTreeCanvasProps {
  tree: TreeLayout;
  nodes: Map<string, RenderNode>;
  edges: Map<string, RenderEdge>;
  viewport: Viewport;
  onViewportChange: (viewport: Viewport) => void;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  collapsed: ReadonlySet<string>;
  onToggleCollapse: (id: string) => void;
  labelAll: boolean;
  highlight: { nodes: Set<string>; edges: Set<string> } | null;
}

/** Device glyph by role. Centred on (0,0); `color` is the state colour. */
export function TreeGlyph({
  iconKey,
  color,
  hollow = false,
}: {
  iconKey: VisualIconKey;
  color: string;
  hollow?: boolean;
}) {
  const fill = hollow ? "transparent" : color;
  const common = { fill, stroke: color, strokeWidth: 1.6 } as const;
  switch (iconKey) {
    case "switch":
      // Stacked bars, Meraki's switch mark.
      return (
        <g>
          {[-4.5, -1.5, 1.5, 4.5].map((y) => (
            <rect key={y} x={-9} y={y - 1.1} width={18} height={2.2} rx={0.6} fill={color} />
          ))}
        </g>
      );
    case "firewall":
      return <rect x={-7} y={-7} width={14} height={14} rx={1.5} {...common} />;
    case "router":
      return (
        <g>
          <circle r={7.5} {...common} />
          <path d="M-4,0 H4 M0,-4 V4" stroke="var(--ecc-surface, #0b1220)" strokeWidth={1.6} />
        </g>
      );
    case "wireless-controller":
    case "access-point":
      return (
        <g>
          <circle r={6} {...common} />
          <path d="M-9,-6 A11,11 0 0 1 9,-6" fill="none" stroke={color} strokeWidth={1.4} />
        </g>
      );
    case "server":
    case "storage":
    case "virtual-machine":
      return <rect x={-5} y={-8} width={10} height={16} rx={2} {...common} />;
    case "cloud":
    case "application":
      return <polygon points="0,-8 8,0 0,8 -8,0" {...common} />;
    case "endpoint":
      return <circle r={4.5} {...common} />;
    default:
      return <circle r={6} fill="transparent" stroke={color} strokeWidth={1.8} />;
  }
}

function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "never";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/** The interface name on `deviceId`'s side of an edge. */
function portOn(edge: RenderEdge | undefined, deviceId: string): string | null {
  if (!edge) return null;
  if (edge.source === deviceId) return edge.localInterfaceName;
  if (edge.target === deviceId) return edge.remoteInterfaceName;
  return null;
}

export function displayName(node: RenderNode): string {
  return node.unidentified ? node.ip ?? node.hostname ?? "Unknown resource" : node.name;
}

export default function TopologyTreeCanvas({
  tree,
  nodes,
  edges,
  viewport,
  onViewportChange,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  collapsed,
  onToggleCollapse,
  labelAll,
  highlight,
}: TopologyTreeCanvasProps) {
  const panZoom = usePanZoom(viewport, onViewportChange);
  const [hoverId, setHoverId] = useState<string | null>(null);

  const parentLink = useMemo(() => {
    const map = new Map<string, TreeLink>();
    for (const link of tree.links) map.set(link.child, link);
    return map;
  }, [tree.links]);

  const at = (id: string) => tree.positions.get(id) ?? { x: 0, y: 0 };
  const dim = (inHighlight: boolean) => (highlight !== null && !inHighlight ? 0.2 : 1);
  // Port labels only once they can be read; the dots stay at any zoom.
  const showPorts = labelAll || viewport.scale >= 0.8;

  const hovered = hoverId ? nodes.get(hoverId) ?? null : null;
  const hoverCard = (() => {
    if (!hovered) return null;
    const point = at(hovered.id);
    const uplink = parentLink.get(hovered.id);
    const uplinkEdge = uplink ? edges.get(uplink.id) : undefined;
    const parent = uplink ? nodes.get(uplink.parent) : undefined;
    const state = DEVICE_STATE_VISUALS[hovered.state];
    const rows: [string, string][] = [];
    if (hovered.ip) rows.push(["IP", hovered.ip]);
    // An unrecognised vendor with no model says nothing; leave the row out.
    const make = [hovered.visual.vendorKey ? hovered.vendorLabel : null, hovered.model]
      .filter(Boolean)
      .join(" · ");
    if (make) rows.push(["Model", make]);
    if (hovered.deviceType) rows.push(["Type", hovered.deviceType]);
    if (parent) {
      const local = portOn(uplinkEdge, hovered.id);
      const remote = portOn(uplinkEdge, parent.id);
      rows.push([
        "Uplink",
        `${local ?? "port not recorded"} → ${displayName(parent)}${remote ? ` ${remote}` : ""}`,
      ]);
    }
    const downstream = tree.descendants.get(hovered.id) ?? 0;
    if (downstream) rows.push(["Downstream", `${downstream} device${downstream === 1 ? "" : "s"}`]);
    if (hovered.interfaces) {
      rows.push([
        "Interfaces",
        `${hovered.interfaces.up} up · ${hovered.interfaces.down} down of ${hovered.interfaces.total}`,
      ]);
    }
    rows.push(["Last data", formatAge(hovered.ageSeconds)]);
    return (
      <div
        role="tooltip"
        data-testid="topology-hover-card"
        style={{
          position: "absolute",
          left: point.x * viewport.scale + viewport.offsetX + 16,
          top: point.y * viewport.scale + viewport.offsetY - 12,
          minWidth: 220,
          maxWidth: 320,
          padding: "8px 10px",
          background: "var(--ecc-surface-raised, rgba(15,23,42,0.97))",
          border: "1px solid var(--border)",
          borderLeft: `3px solid ${state.color}`,
          borderRadius: 8,
          boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
          fontSize: 11,
          color: "var(--fg)",
          pointerEvents: "none",
          zIndex: 5,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 2 }}>{displayName(hovered)}</div>
        <div style={{ color: state.color, fontSize: 10, letterSpacing: "0.4px", marginBottom: 6 }}>
          {state.label.toUpperCase()}
        </div>
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px", margin: 0 }}>
          {rows.map(([label, value]) => (
            <div key={label} style={{ display: "contents" }}>
              <dt style={{ color: "var(--muted)" }}>{label}</dt>
              <dd style={{ margin: 0, overflowWrap: "anywhere" }}>{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  })();

  return (
    <div
      {...panZoom}
      onClick={(event) => {
        const target = event.target as Element;
        if (target === event.currentTarget || target.tagName === "svg") onClearSelection();
      }}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        cursor: "grab",
        background:
          "radial-gradient(circle at 1px 1px, rgba(148,163,184,0.12) 1px, transparent 0) 0 0 / 26px 26px",
      }}
      role="application"
      aria-label="Network topology tree"
      data-testid="topology-tree"
    >
      <svg width="100%" height="100%" style={{ position: "absolute", inset: 0 }}>
        <g
          transform={`translate(${viewport.offsetX} ${viewport.offsetY}) scale(${viewport.scale})`}
        >
          {/* Redundant / looped links: dashed, under the tree. */}
          {tree.crossLinks.map((link) => {
            const edge = edges.get(link.id);
            const color = edge && edge.state !== "UP" ? LINK_STATE_VISUALS[edge.state].color : IDLE_LINK;
            return (
              <path
                key={link.id}
                d={treeLinkPath(at(link.parent), at(link.child))}
                fill="none"
                stroke={color}
                strokeWidth={1}
                strokeDasharray="3 4"
                opacity={dim(highlight === null || highlight.edges.has(link.id)) * 0.8}
                style={{ cursor: "pointer" }}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectEdge(link.id);
                }}
              >
                <title>Redundant link (not on the tree path)</title>
              </path>
            );
          })}

          {tree.links.map((link) => {
            const edge = edges.get(link.id);
            const from = at(link.parent);
            const to = at(link.child);
            const hop = to.x - from.x;
            const a = { x: from.x + hop * PARENT_PORT_AT, y: to.y };
            const b = { x: from.x + hop * CHILD_PORT_AT, y: to.y };
            const parentPort = portOn(edge, link.parent);
            const childPort = portOn(edge, link.child);
            const selected = selectedEdgeId === link.id;
            const color = edge && edge.state !== "UP" ? LINK_STATE_VISUALS[edge.state].color : IDLE_LINK;
            const d = `M${from.x},${from.y} L${a.x},${a.y} L${to.x},${to.y}`;
            return (
              <g key={link.id} opacity={dim(highlight === null || highlight.edges.has(link.id))}>
                <path
                  d={d}
                  fill="none"
                  stroke={selected ? "var(--accent, #38bdf8)" : color}
                  strokeWidth={selected ? 2.2 : 1.1}
                  strokeDasharray={edge ? LINK_STATE_VISUALS[edge.state].dash || undefined : undefined}
                />
                {parentPort ? <circle cx={a.x} cy={a.y} r={2.6} fill={color} /> : null}
                {childPort ? <circle cx={b.x} cy={b.y} r={2.6} fill={color} /> : null}
                {showPorts && parentPort ? (
                  <text x={a.x + 4} y={a.y - 4} fontSize={9} fill="var(--muted)">
                    {parentPort}
                  </text>
                ) : null}
                {showPorts && childPort ? (
                  <text x={b.x + 4} y={b.y - 4} fontSize={9} fill="var(--muted)">
                    {childPort}
                  </text>
                ) : null}
                {/* Wide transparent hit target so a thin link is clickable. */}
                <path
                  d={d}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={10}
                  style={{ cursor: "pointer", pointerEvents: "stroke" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectEdge(link.id);
                  }}
                >
                  <title>
                    {`${parentPort ?? "?"} ↔ ${childPort ?? "?"}${edge ? ` · ${LINK_STATE_VISUALS[edge.state].label}` : ""}`}
                  </title>
                </path>
              </g>
            );
          })}

          {[...tree.positions.entries()].map(([id, point]) => {
            const node = nodes.get(id);
            if (!node) return null;
            const state = DEVICE_STATE_VISUALS[node.state];
            const kids = tree.children.get(id)?.length ?? 0;
            const isCollapsed = collapsed.has(id);
            const isBranch = kids > 0;
            const selected = selectedNodeId === id;
            const labelled = labelAll || isBranch || selected || hoverId === id;
            const hidden = tree.descendants.get(id) ?? 0;
            return (
              <g
                key={id}
                transform={`translate(${point.x} ${point.y})`}
                opacity={dim(highlight === null || highlight.nodes.has(id))}
                data-testid="topology-tree-node"
              >
                {selected ? (
                  <circle r={13} fill="none" stroke="var(--accent, #38bdf8)" strokeWidth={2} />
                ) : null}
                <g
                  role="button"
                  tabIndex={0}
                  aria-label={`${displayName(node)}, ${state.label}`}
                  style={{ cursor: "pointer" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectNode(id);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelectNode(id);
                  }}
                  onPointerEnter={() => setHoverId(id)}
                  onPointerLeave={() => setHoverId((current) => (current === id ? null : current))}
                  onFocus={() => setHoverId(id)}
                  onBlur={() => setHoverId(null)}
                >
                  {/* Generous invisible hit area around a small glyph. */}
                  <circle r={12} fill="transparent" />
                  <TreeGlyph
                    iconKey={node.visual.iconKey}
                    color={state.color}
                    hollow={node.state === "UNKNOWN" || node.state === "STALE"}
                  />
                </g>
                {labelled ? (
                  <text
                    x={isBranch && !isCollapsed ? -2 : 14}
                    y={isBranch && !isCollapsed ? -14 : 3.5}
                    textAnchor={isBranch && !isCollapsed ? "middle" : "start"}
                    fontSize={isBranch ? 11 : 10}
                    fontWeight={isBranch ? 600 : 400}
                    fill={node.state === "OFFLINE" ? state.color : "var(--fg)"}
                    style={{ pointerEvents: "none" }}
                  >
                    {displayName(node)}
                  </text>
                ) : null}
                {isBranch ? (
                  <g
                    transform="translate(0 15)"
                    role="button"
                    tabIndex={0}
                    aria-label={isCollapsed ? `Expand ${hidden} devices` : "Collapse branch"}
                    aria-expanded={!isCollapsed}
                    style={{ cursor: "pointer" }}
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggleCollapse(id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") onToggleCollapse(id);
                    }}
                    data-testid="topology-tree-toggle"
                  >
                    <rect
                      x={isCollapsed ? -14 : -6}
                      y={-6}
                      width={isCollapsed ? 28 : 12}
                      height={12}
                      rx={6}
                      fill="var(--ecc-surface, #0f172a)"
                      stroke="var(--border)"
                    />
                    <text
                      y={3.5}
                      textAnchor="middle"
                      fontSize={9}
                      fill="var(--muted)"
                      style={{ pointerEvents: "none" }}
                    >
                      {isCollapsed ? `+${hidden}` : "−"}
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
      {hoverCard}
    </div>
  );
}
