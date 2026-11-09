/**
 * Phone-width navigation drawer.
 *
 * Below MOBILE_NAV_MAX_WIDTH the sidebar stops reserving layout width and
 * becomes an off-canvas overlay drawer, closed by default. The CSS side lives
 * in styles/navigation.css under the same breakpoint; keep the two in sync.
 */

/** Widest viewport (px) that gets the overlay drawer instead of the rail. */
export const MOBILE_NAV_MAX_WIDTH = 767;
export const MOBILE_NAV_QUERY = `(max-width: ${MOBILE_NAV_MAX_WIDTH}px)`;

export type DrawerEvent =
  | { type: "toggle" }
  | { type: "close" }
  /** The route changed (a nav link was followed). */
  | { type: "navigate" }
  | { type: "key"; key: string };

/** Next open/closed state of the drawer. */
export function drawerReducer(open: boolean, event: DrawerEvent): boolean {
  switch (event.type) {
    case "toggle":
      return !open;
    case "close":
    case "navigate":
      return false;
    case "key":
      return event.key === "Escape" ? false : open;
  }
}

/**
 * What the brand-row collapse button does at this width: on desktop it
 * collapses the rail, on a phone it closes the drawer.
 */
export function collapseButtonRole(mobile: boolean, collapsed: boolean): { action: "rail" | "close"; label: string } {
  if (mobile) return { action: "close", label: "Close navigation" };
  return { action: "rail", label: collapsed ? "Expand navigation" : "Collapse navigation" };
}
