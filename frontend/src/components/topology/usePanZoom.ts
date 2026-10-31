"use client";

/**
 * Wheel-zoom and drag-pan for a topology canvas.
 *
 * Shared by the card canvas and the tree canvas. The viewport itself is owned by
 * the panel (so a refresh keeps zoom and pan); this hook only turns pointer and
 * wheel events into viewport changes.
 */
import { useRef, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";

import type { Viewport } from "../../lib/topology/layout";

export const MIN_SCALE = 0.15;
export const MAX_SCALE = 3;

export function usePanZoom(viewport: Viewport, onViewportChange: (viewport: Viewport) => void) {
  const drag = useRef<{
    x: number;
    y: number;
    offsetX: number;
    offsetY: number;
    active: boolean;
  } | null>(null);

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, viewport.scale * factor));
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

  return {
    onWheel,
    onPointerDown,
    onPointerMove,
    onPointerUp: endDrag,
    onPointerLeave: endDrag,
  };
}
