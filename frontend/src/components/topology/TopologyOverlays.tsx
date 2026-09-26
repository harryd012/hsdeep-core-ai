"use client";

/**
 * Canvas overlays (Phase 9.7A): minimap, floating controls and the legend.
 *
 * They sit ON the canvas so the graph keeps the space, and they consume the SAME
 * render model as the canvas — there is no second graph renderer here, just a
 * scaled projection of the positions the canvas already computed.
 */
import { useEffect, useState } from "react";
import { Maximize2, Minimize2, Minus, Plus, RotateCcw, Scan } from "lucide-react";

import {
  graphVisibleRect,
  minimapTransform,
  toMinimapRect,
  type Viewport,
} from "../../lib/topology/layout";
import {
  DEVICE_STATE_VISUALS,
  LINK_STATE_VISUALS,
  type DeviceState,
} from "../../lib/topology/status";

const MINIMAP_WIDTH = 184;
const MINIMAP_HEIGHT = 118;

export interface TopologyMinimapProps {
  nodes: { id: string; state: DeviceState }[];
  positions: Map<string, { x: number; y: number }>;
  edgeCount: number;
  viewport: Viewport;
  canvasSize: { width: number; height: number };
  /** Move the main viewport so this graph coordinate becomes the centre. */
  onNavigate: (x: number, y: number) => void;
}

/** Compact overview with a viewport indicator. Hidden when there is no graph. */
export function TopologyMinimap({
  nodes,
  positions,
  edgeCount,
  viewport,
  canvasSize,
  onNavigate,
}: TopologyMinimapProps) {
  const points = nodes
    .map((node) => positions.get(node.id))
    .filter((point): point is { x: number; y: number } => Boolean(point));
  const transform = minimapTransform(points, {
    width: MINIMAP_WIDTH,
    height: MINIMAP_HEIGHT,
  });

  // No graph → no minimap (never an empty frame pretending to be a map).
  if (!transform || !points.length) return null;

  const indicator = toMinimapRect(
    graphVisibleRect(viewport, canvasSize),
    transform,
    { width: MINIMAP_WIDTH, height: MINIMAP_HEIGHT }
  );

  const handleClick = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    onNavigate((x - transform.offsetX) / transform.scale, (y - transform.offsetY) / transform.scale);
  };

  return (
    <svg
      data-testid="topology-minimap"
      width={MINIMAP_WIDTH}
      height={MINIMAP_HEIGHT}
      onClick={handleClick}
      role="img"
      aria-label={`Topology overview: ${nodes.length} nodes, ${edgeCount} links`}
      style={{
        position: "absolute",
        right: 12,
        // Sits above the control bar so it never collides with the app's
        // floating status badges in the bottom-right corner.
        bottom: 56,
        background: "rgba(2,6,23,0.85)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        cursor: "crosshair",
      }}
    >
      {nodes.map((node) => {
        const point = positions.get(node.id);
        if (!point) return null;
        return (
          <circle
            key={node.id}
            cx={point.x * transform.scale + transform.offsetX}
            cy={point.y * transform.scale + transform.offsetY}
            r={2}
            fill={DEVICE_STATE_VISUALS[node.state].color}
          />
        );
      })}
      <rect
        x={indicator.x}
        y={indicator.y}
        width={indicator.width}
        height={indicator.height}
        fill="rgba(56,189,248,0.10)"
        stroke="rgba(56,189,248,0.75)"
        strokeWidth={1}
      />
    </svg>
  );
}

export interface TopologyCanvasControlsProps {
  scale: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onReset: () => void;
  /** Element that should enter fullscreen (the canvas shell). */
  fullscreenTarget: React.RefObject<HTMLElement | null>;
}

function controlButtonStyle(): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 26,
    height: 26,
    background: "rgba(2,6,23,0.85)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    color: "var(--fg)",
    cursor: "pointer",
  };
}

/** Floating zoom / fit / reset / fullscreen controls (bottom-left of the canvas). */
export function TopologyCanvasControls({
  scale,
  onZoomIn,
  onZoomOut,
  onFit,
  onReset,
  fullscreenTarget,
}: TopologyCanvasControlsProps) {
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = async () => {
    const element = fullscreenTarget.current;
    if (!element || typeof element.requestFullscreen !== "function") return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await element.requestFullscreen();
    } catch {
      // The browser refused (or the API is unavailable): do nothing rather than
      // pretending the workspace changed.
    }
  };

  return (
    <div
      data-testid="topology-canvas-controls"
      style={{
        position: "absolute",
        left: 12,
        bottom: 12,
        display: "inline-flex",
        gap: 4,
        alignItems: "center",
        padding: 4,
        background: "rgba(2,6,23,0.72)",
        border: "1px solid var(--border)",
        borderRadius: 8,
      }}
    >
      <button
        type="button"
        onClick={onZoomIn}
        style={controlButtonStyle()}
        aria-label="Zoom in"
        title="Zoom in"
      >
        <Plus size={13} />
      </button>
      <button
        type="button"
        onClick={onZoomOut}
        style={controlButtonStyle()}
        aria-label="Zoom out"
        title="Zoom out"
      >
        <Minus size={13} />
      </button>
      <button
        type="button"
        onClick={onFit}
        style={controlButtonStyle()}
        aria-label="Fit to screen"
        title="Fit to screen"
      >
        <Scan size={13} />
      </button>
      <button
        type="button"
        onClick={onReset}
        style={controlButtonStyle()}
        aria-label="Reset view"
        title="Reset view"
      >
        <RotateCcw size={13} />
      </button>
      <button
        type="button"
        onClick={() => void toggleFullscreen()}
        style={controlButtonStyle()}
        aria-label="Toggle fullscreen"
        title="Toggle fullscreen"
        data-testid="topology-fullscreen"
      >
        {fullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
      </button>
      <span style={{ fontSize: 10, color: "var(--muted)", padding: "0 4px" }}>
        {Math.round(scale * 100)}%
      </span>
    </div>
  );
}

/**
 * Persistent legend: explains only semantics the canvas actually uses, so a
 * reader can interpret the graph without guessing what a colour means.
 */
export function TopologyLegend() {
  return (
    <div
      data-testid="topology-legend"
      style={{
        position: "absolute",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: 12,
        display: "flex",
        gap: 12,
        flexWrap: "wrap",
        justifyContent: "center",
        maxWidth: "min(760px, calc(100% - 320px))",
        padding: "4px 10px",
        background: "rgba(2,6,23,0.72)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        fontSize: 10,
        color: "var(--muted)",
      }}
    >
      <span style={{ color: "var(--fg)" }}>LEGEND</span>
      {Object.values(DEVICE_STATE_VISUALS).map((visual) => (
        <span key={visual.state} style={{ color: visual.color }} title={visual.description}>
          {visual.marker} {visual.label}
        </span>
      ))}
      {Object.values(LINK_STATE_VISUALS).map((visual) => (
        <span key={`link-${visual.state}`} style={{ color: visual.color }} title={visual.description}>
          — {visual.label}
        </span>
      ))}
      <span title="Animated dashes mean measured traffic in that direction; without counters the link stays static">
        ⇢ traffic (measured only)
      </span>
      <span title="Arrows show the direction evidenced by interface transmit/receive counters">
        ↑↓ direction
      </span>
    </div>
  );
}
