"use client";

/**
 * TopologyCanvas — the graph itself (Phase 9.5).
 *
 * Layout: an SVG layer draws site boundaries and links; HTML nodes are absolutely
 * positioned on top. That keeps text and icons crisp and makes every node a real
 * focusable button, without foreignObject.
 *
 * The canvas owns no data and no viewport state: the panel passes both in, which
 * is what lets a refresh keep zoom, pan, layout and selection.
 */
import {
  useCallback,
  useMemo,
  useRef,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent,
} from "react";

import type { Viewport } from "../../lib/topology/layout";
import { DEVICE_STATE_VISUALS, LINK_STATE_VISUALS } from "../../lib/topology/status";
import type { RenderEdge, RenderNode, TopologyGraphData } from "./topologyRenderTypes";
import { ResourceIcon } from "./TopologyVisuals";

const NODE_WIDTH = 176;
const NODE_HEIGHT = 56;
const SITE_PAD = 34;

export interface TopologyCanvasProps {
  model: TopologyGraphData;
  positions: Map<string, { x: number; y: number }>;
  viewport: Viewport;
  onViewportChange: (viewport: Viewport) => void;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  /** Search / focus highlight: what stays fully opaque. */
  highlight: { nodes: Set<string>; edges: Set<string> } | null;
  reducedMotion: boolean;
  animateEnabled: boolean;
  /** Edge ids allowed to animate, from the tested bounded budget helper. */
  animatedEdgeIds: Set<string>;
}

function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "never";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export default function TopologyCanvas({
  model,
  positions,
  viewport,
  onViewportChange,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  highlight,
  reducedMotion,
  animateEnabled,
  animatedEdgeIds,
}: TopologyCanvasProps) {
  const drag = useRef<{
    x: number;
    y: number;
    offsetX: number;
    offsetY: number;
    active: boolean;
  } | null>(null);
  const layerRef = useRef<HTMLDivElement | null>(null);

  const point = useCallback(
    (id: string) => positions.get(id) ?? { x: 0, y: 0 },
    [positions]
  );

  /** Site boundaries are drawn only where a site actually has member devices. */
  const siteBoxes = useMemo(() => {
    const boxes: { id: string; label: string; x: number; y: number; w: number; h: number }[] = [];
    for (const site of model.nodes) {
      if (site.kind !== "site") continue;
      const members = model.nodes.filter(
        (node) => node.kind === "device" && node.siteId === site.id
      );
      if (!members.length) continue;
      const xs = members.map((member) => point(member.id).x);
      const ys = members.map((member) => point(member.id).y);
      const minX = Math.min(...xs) - NODE_WIDTH / 2 - SITE_PAD;
      const maxX = Math.max(...xs) + NODE_WIDTH / 2 + SITE_PAD;
      const minY = Math.min(...ys) - NODE_HEIGHT / 2 - SITE_PAD;
      const maxY = Math.max(...ys) + NODE_HEIGHT / 2 + SITE_PAD;
      boxes.push({ id: site.id, label: site.name, x: minX, y: minY, w: maxX - minX, h: maxY - minY });
    }
    return boxes;
  }, [model.nodes, point]);

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.min(3, Math.max(0.15, viewport.scale * factor));
    const ratio = next / viewport.scale;
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    onViewportChange({
      scale: next,
      offsetX: pointerX - (pointerX - viewport.offsetX) * ratio,
      offsetY: pointerY - (pointerY - viewport.offsetY) * ratio,
    });
  };

  /**
   * Panning starts only once the pointer has actually moved.
   *
   * Capturing the pointer on pointerdown (the obvious implementation) retargets
   * the compatibility click to the container, so a plain click on a node or a
   * link never reaches its handler and the detail drawer never opens. The
   * threshold keeps clicks as clicks and drags as drags.
   */
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      offsetX: viewport.offsetX,
      offsetY: viewport.offsetY,
      active: false,
    };
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (!state) return;
    const dx = event.clientX - state.x;
    const dy = event.clientY - state.y;
    if (!state.active) {
      if (Math.hypot(dx, dy) < 4) return;
      state.active = true;
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    onViewportChange({
      scale: viewport.scale,
      offsetX: state.offsetX + dx,
      offsetY: state.offsetY + dy,
    });
  };

  const endDrag = () => {
    drag.current = null;
  };

  const opacityFor = (inHighlight: boolean) =>
    highlight !== null && !inHighlight ? 0.25 : 1;

  return (
    <div
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerLeave={endDrag}
      onClick={(event) => {
        // Background click (the container itself or the transformed layer, i.e.
        // empty canvas) clears the selection. A click on a node/link targets
        // those elements and is handled there.
        if (event.target === event.currentTarget || event.target === layerRef.current) {
          onClearSelection();
        }
      }}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        cursor: "grab",
        background:
          "radial-gradient(circle at 1px 1px, rgba(148,163,184,0.16) 1px, transparent 0) 0 0 / 26px 26px",
        border: "1px solid var(--border)",
        borderRadius: 10,
      }}
      role="application"
      aria-label="Live infrastructure topology"
    >
      <div
        ref={layerRef}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          transformOrigin: "0 0",
          transform: `translate(${viewport.offsetX}px, ${viewport.offsetY}px) scale(${viewport.scale})`,
        }}
      >
        <svg
          width={1}
          height={1}
          style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}
          aria-hidden="true"
        >
          {siteBoxes.map((box) => (
            <g key={box.id}>
              <rect
                x={box.x}
                y={box.y}
                width={box.w}
                height={box.h}
                rx={14}
                fill="rgba(56,189,248,0.05)"
                stroke="rgba(56,189,248,0.28)"
                strokeDasharray="4 6"
              />
              <text
                x={box.x + 12}
                y={box.y + 18}
                fontSize={11}
                fill="var(--muted)"
                letterSpacing="0.6px"
              >
                {box.label.toUpperCase()}
              </text>
            </g>
          ))}

          {model.edges.map((edge: RenderEdge) => {
            const from = point(edge.source);
            const to = point(edge.target);
            const visual = LINK_STATE_VISUALS[edge.state];
            const animating = animatedEdgeIds.has(edge.id);
            const midX = (from.x + to.x) / 2;
            const midY = (from.y + to.y) / 2;
            const angle = Math.atan2(to.y - from.y, to.x - from.x);
            const arrow = (x: number, y: number, rotation: number) => (
              <polygon
                points="0,0 -7,3.5 -7,-3.5"
                fill={visual.color}
                transform={`translate(${x} ${y}) rotate(${rotation})`}
              />
            );
            return (
              <g key={edge.id} opacity={opacityFor(highlight === null || highlight.edges.has(edge.id))}>
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={visual.color}
                  strokeWidth={selectedEdgeId === edge.id ? 3.2 : 2}
                  strokeDasharray={visual.dash || undefined}
                  strokeLinecap="round"
                />
                {animating ? (
                  <line
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={visual.color}
                    strokeWidth={3.4}
                    strokeLinecap="round"
                    strokeDasharray="10 22"
                    style={{ animation: "hsdeep-flow-dash 1.1s linear infinite" }}
                  />
                ) : null}
                {edge.direction === "forward" || edge.direction === "bidirectional"
                  ? arrow(
                      to.x - 10 * Math.cos(angle),
                      to.y - 10 * Math.sin(angle),
                      (angle * 180) / Math.PI
                    )
                  : null}
                {edge.direction === "reverse" || edge.direction === "bidirectional"
                  ? arrow(
                      from.x + 10 * Math.cos(angle),
                      from.y + 10 * Math.sin(angle),
                      ((angle + Math.PI) * 180) / Math.PI
                    )
                  : null}
                {selectedEdgeId === edge.id ? (
                  <circle cx={midX} cy={midY} r={5} fill={visual.color} />
                ) : null}
                {/* Hit target so a thin link is still clickable. */}
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke="transparent"
                  strokeWidth={14}
                  style={{ cursor: "pointer", pointerEvents: "stroke" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectEdge(edge.id);
                  }}
                />
              </g>
            );
          })}
        </svg>

        {model.nodes.map((node: RenderNode) => {
          if (node.kind === "site") return null;
          const position = point(node.id);
          const stateVisual = DEVICE_STATE_VISUALS[node.state];
          const selected = selectedNodeId === node.id;
          const points =
            stateVisual.marker === "triangle"
              ? "5,0 10,9 0,9"
              : stateVisual.marker === "square"
                ? "1,1 9,1 9,9 1,9"
                : stateVisual.marker === "hex"
                  ? "5,0 10,3 10,7 5,10 0,7 0,3"
                  : "5,0 10,5 5,10 0,5";
          return (
            <button
              key={node.id}
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onSelectNode(node.id);
              }}
              title={`${node.unidentified ? "Unknown Resource" : node.name} — ${stateVisual.label}${
                node.ip ? ` — ${node.ip}` : ""
              }`}
              style={{
                position: "absolute",
                left: position.x - NODE_WIDTH / 2,
                top: position.y - NODE_HEIGHT / 2,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 8px",
                textAlign: "left",
                background: "rgba(15,23,42,0.92)",
                border: `1px solid ${selected ? stateVisual.color : "var(--border)"}`,
                boxShadow: selected ? `0 0 0 2px ${stateVisual.color}` : "none",
                borderRadius: 10,
                opacity: opacityFor(highlight === null || highlight.nodes.has(node.id)),
                cursor: "pointer",
              }}
            >
              <span style={{ position: "relative", display: "inline-flex", flexShrink: 0 }}>
                <ResourceIcon iconKey={node.visual.iconKey} size={18} />
                <svg
                  width={10}
                  height={10}
                  style={{ position: "absolute", right: -4, bottom: -3 }}
                  aria-hidden="true"
                >
                  <polygon
                    points={points}
                    fill={stateVisual.marker === "ring" ? "transparent" : stateVisual.color}
                    stroke={stateVisual.color}
                    strokeWidth={1.5}
                  />
                </svg>
              </span>
              <span
                style={{
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                  lineHeight: 1.25,
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--fg)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {node.unidentified ? "Unknown Resource" : node.name}
                </span>
                <span style={{ fontSize: 10, color: "var(--muted)", whiteSpace: "nowrap" }}>
                  {node.ip ?? node.vendorLabel}
                </span>
                <span style={{ fontSize: 9, color: stateVisual.color, letterSpacing: "0.4px" }}>
                  {stateVisual.label.toUpperCase()}
                  {node.freshnessState && node.freshnessState !== "fresh"
                    ? ` · ${formatAge(node.ageSeconds)}`
                    : ""}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <style>{`@keyframes hsdeep-flow-dash { to { stroke-dashoffset: -32; } }`}</style>
    </div>
  );
}
