/**
 * Portal resource contract (Resource Explorer / Resource Detail milestone).
 *
 * FRONTEND PRESENTATION CONTRACT ONLY.
 *
 * There is deliberately no backend model here and no duplication of backend
 * resource models: these types describe how an EXISTING backend resource is
 * presented in the portal. Every loader referenced by the registry calls the
 * existing authenticated API client (`@/lib/api`); there is no second HTTP
 * client and no new tenant mechanism.
 *
 * Honesty rules encoded in the types:
 *  - `implemented: false` means the portal must NOT claim the capability.
 *  - Optional fields are omitted rather than filled with invented values.
 *  - Tabs with no real data source resolve to an explicit unavailable state.
 */

export type ResourceHealthTone = "ok" | "warn" | "bad" | "idle";

export type ResourceHealth = {
  /** Human label; health is never communicated by colour alone. */
  label: string;
  tone: ResourceHealthTone;
};

/** A single column in the explorer table. `value` returns "-" for absent data. */
export type ResourceColumn<T> = {
  key: string;
  label: string;
  /** Renders the cell. Must return "-" (never a fabricated value) when absent. */
  value: (row: T) => string;
  /** Monospace (ids, IPs, MACs). */
  mono?: boolean;
  /** Sortable client-side over the bounded result set. */
  sortable?: boolean;
};

/** Normalised row shape the explorer renders, whatever the backend entity is. */
export type ResourceSummary = {
  id: string;
  name: string;
  subtitle?: string;
  status?: string;
  health?: ResourceHealth;
  site?: string;
  vendor?: string;
  category?: string;
  lastUpdated?: string;
};

/**
 * Detail payload. `properties` is fed to the generic Properties tab; keys with
 * undefined/empty values are dropped before rendering.
 */
export type ResourceDetail = {
  summary: ResourceSummary;
  properties: Record<string, unknown>;
  /** Free-text description shown on Overview when the backend supplies one. */
  description?: string;
};

export type ResourceTabKey =
  | "overview"
  | "monitoring"
  | "alerts"
  | "events"
  | "topology"
  | "ai"
  | "actions"
  | "activity"
  | "properties"
  | "devices";

export type ResourceTab = {
  key: ResourceTabKey;
  label: string;
  /**
   * True only when this portal has a REAL data source for the tab. When false
   * the tab renders an explicit not-available state instead of fake content.
   */
  available: boolean;
  /** Shown when `available` is false. */
  unavailableReason?: string;
};

export type ResourceAction = {
  key: string;
  label: string;
  /** Existing route to open (no destructive actions in this milestone). */
  href?: string;
  /** In-page behaviour. Only refresh is supported today. */
  kind?: "refresh" | "link";
};

export type ResourceListParams = {
  search?: string;
  limit?: number;
  offset?: number;
};

/**
 * Registry entry. `implemented` is explicit metadata and is NEVER inferred from
 * the mere existence of a route - that is exactly how a portal ends up claiming
 * functionality it does not have.
 */
export type ResourceTypeDefinition<T> = {
  key: string;
  label: string;
  description: string;
  /** Explicit availability, not a route-existence guess. */
  implemented: boolean;
  /** Reason shown when `implemented` is false. */
  unavailableReason?: string;
  /** Optional supported filters (server-side where the API supports them). */
  supportsSearch: boolean;
  supportsPagination: boolean;
  columns: ResourceColumn<T>[];
  tabs: ResourceTab[];
  actions: ResourceAction[];
  /** Existing API call. Returns a BOUNDED result set. */
  list?: (tenantId: string | undefined, params: ResourceListParams) => Promise<T[]>;
  /** Optional single-resource fetch; when absent the detail page resolves the
   *  row from the bounded list instead of inventing an endpoint. */
  detail?: (id: string, tenantId: string | undefined) => Promise<T>;
  /** Projects a backend entity onto the generic summary. */
  toSummary?: (row: T) => ResourceSummary;
  /** Projects a backend entity onto the generic properties dictionary. */
  toProperties?: (row: T) => Record<string, unknown>;
  /** Description on Overview, only when the backend actually provides one. */
  toDescription?: (row: T) => string | undefined;
};

/** Bounded default so the explorer never issues an unbounded request. */
export const DEFAULT_RESOURCE_LIMIT = 50;

/** Safe cell renderer: absent data is "-", never a fabricated value. */
export function orDash(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}
