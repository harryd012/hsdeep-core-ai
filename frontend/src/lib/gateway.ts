/**
 * Tool gateway entries: the collectors the backend actually registers
 * (GET /api/monitoring/source-types), joined with the tenant's own monitoring
 * sources. "Connected" needs a successful poll inside the freshness window,
 * so a configured-but-silent source never looks healthy.
 */

export interface GatewaySourceType {
  source_type: string;
  display_name: string;
  implemented: boolean;
  category: string;
  firewall_capabilities: string[];
}

export interface GatewaySource {
  source_type: string;
  is_active: boolean;
  last_successful_run_at?: string | null;
}

export type GatewayState = "connected" | "stale" | "available";

export interface GatewayEntry extends GatewaySourceType {
  state: GatewayState;
  /** Active sources of this type the tenant has configured. */
  sources: number;
  lastSuccessAt: string | null;
}

/** Same window the backend uses for device evidence (24 h). */
export const GATEWAY_FRESHNESS_MS = 24 * 60 * 60 * 1000;

export const CATEGORY_ORDER = ["firewall", "network", "server", "cloud", "monitoring", "other"] as const;

export const CATEGORY_LABEL: Record<string, string> = {
  firewall: "Firewalls",
  network: "Network",
  server: "Servers",
  cloud: "Cloud & virtualization",
  monitoring: "Monitoring tools",
  other: "Other",
};

export function gatewayEntries(
  types: GatewaySourceType[],
  sources: GatewaySource[],
  now: number = Date.now(),
): GatewayEntry[] {
  const entries = types
    .filter((t) => t.implemented)
    .map((t): GatewayEntry => {
      const mine = sources.filter((s) => s.is_active && s.source_type === t.source_type);
      const last = mine
        .map((s) => s.last_successful_run_at ?? null)
        .filter((v): v is string => Boolean(v))
        .sort()
        .at(-1) ?? null;
      const fresh = last !== null && now - Date.parse(last) <= GATEWAY_FRESHNESS_MS;
      const state: GatewayState = mine.length === 0 ? "available" : fresh ? "connected" : "stale";
      return { ...t, state, sources: mine.length, lastSuccessAt: last };
    });
  const rank = (c: string) => {
    const i = (CATEGORY_ORDER as readonly string[]).indexOf(c);
    return i === -1 ? CATEGORY_ORDER.length : i;
  };
  const stateRank: Record<GatewayState, number> = { connected: 0, stale: 1, available: 2 };
  return entries.sort(
    (a, b) =>
      rank(a.category) - rank(b.category) ||
      stateRank[a.state] - stateRank[b.state] ||
      a.display_name.localeCompare(b.display_name),
  );
}

export function groupByCategory(entries: GatewayEntry[]): { category: string; label: string; entries: GatewayEntry[] }[] {
  const groups = new Map<string, GatewayEntry[]>();
  for (const e of entries) groups.set(e.category, [...(groups.get(e.category) ?? []), e]);
  return [...groups.entries()].map(([category, list]) => ({
    category,
    label: CATEGORY_LABEL[category] ?? CATEGORY_LABEL.other,
    entries: list,
  }));
}
