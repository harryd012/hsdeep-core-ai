/**
 * Icon mapping for the topology (Phase 9.5).
 *
 * The vendor registry returns an icon KEY, never a component: that keeps
 * `lib/topology/vendors.ts` free of React and testable in plain Node, and it keeps
 * every icon reference in one place. Only icons already used elsewhere in the app
 * are referenced, so nothing here can fail to load.
 */
import {
  Archive,
  Cloud,
  Database,
  Globe2,
  Laptop,
  LayoutGrid,
  Network,
  Radio,
  Server,
  Shield,
  Tag,
  Wifi,
  type LucideIcon,
} from "lucide-react";
import { createElement } from "react";

import type { VisualIconKey } from "../../lib/topology/vendors";

export const ICON_BY_KEY: Record<VisualIconKey, LucideIcon> = {
  firewall: Shield,
  router: Network,
  switch: LayoutGrid,
  "access-point": Wifi,
  "wireless-controller": Radio,
  server: Server,
  "virtual-machine": Archive,
  cloud: Cloud,
  storage: Database,
  endpoint: Laptop,
  application: Globe2,
  unknown: Tag,
};

/** Icon for a visual key, with a guaranteed fallback. */
export function iconFor(key: VisualIconKey): LucideIcon {
  return ICON_BY_KEY[key] ?? Tag;
}

/**
 * Renders the icon for a visual key.
 *
 * Declared at module scope and rendered through `createElement` so no component
 * type is created during render — a component chosen per row must not be a new
 * component identity on every render.
 */
export function ResourceIcon({ iconKey, size }: { iconKey: VisualIconKey; size: number }) {
  return createElement(iconFor(iconKey), { size, "aria-hidden": true });
}

/** Marker shape per state token, so state survives greyscale (spec: not colour alone). */
export const MARKER_SHAPE: Record<string, string> = {
  dot: "50%,50% 4.5,0 9,9 0,9",
  ring: "50%,50% 4.5,0 9,9 0,9",
  triangle: "50%,14% 86%,78% 14%,78%",
  square: "22%,22% 78%,22% 78%,78% 22%,78%",
  hex: "50%,12% 88%,36% 88%,74% 50%,98% 12%,74% 12%,36%",
};

/** Small status glyph: a filled marker plus an outline, never colour alone. */
export function markerPoints(shape: string): string {
  return MARKER_SHAPE[shape] ?? MARKER_SHAPE.dot;
}
