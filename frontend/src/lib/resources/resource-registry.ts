/**
 * Portal resource registry.
 *
 * Describes the resource types the portal exposes. Every `list`/`detail` below
 * calls the EXISTING authenticated API client - no second HTTP client, no
 * invented endpoints, no fabricated rows.
 *
 * `implemented` is explicit metadata (never inferred from a route existing).
 *
 * Tab availability is declared per type here (the tab registry), so a type only
 * offers tabs this portal can actually populate. Tabs without a real data source
 * are marked `available: false` and render an honest not-available state - there
 * are no fake charts, fake alerts or fake AI analysis anywhere in the portal.
 */

import {
  DEFAULT_RESOURCE_LIMIT,
  orDash,
  type ResourceHealthTone,
  type ResourceTab,
  type ResourceTypeDefinition,
} from "./resource-types";

import {
  getAssetDevice,
  getIncident,
  listAlerts,
  listAssetDevices,
  listAssetSites,
  listIncidents,
  listMonitoringSources,
  type Alert,
  type AssetDevice,
  type AssetSite,
  type Incident,
  type MonitoringSourceOut,
} from "@/lib/api";

/* --------------------------------------------------------------- tab sets */

const T = (
  key: ResourceTab["key"],
  label: string,
  available: boolean,
  unavailableReason?: string,
): ResourceTab => ({ key, label, available, unavailableReason });

const NOT_WIRED = (what: string) => `No portal data source is wired for ${what} yet.`;

const OVERVIEW = T("overview", "Overview", true);
const PROPERTIES = T("properties", "Properties", true);

/** Tabs every type gets: Overview + Properties are always real. */
const BASE_TABS: ResourceTab[] = [OVERVIEW, PROPERTIES];

/**
 * NOTE on Site names: `AssetDevice`, `MonitoringSourceOut`, `Alert` and
 * `Incident` expose a `site_id`, not a site NAME. Resolving id -> name would
 * need a second lookup, so the Site column renders "-" and the raw `site_id` is
 * shown (truthfully) on Properties instead of a guessed name.
 */

const DEVICE_TABS: ResourceTab[] = [
  OVERVIEW,
  T("monitoring", "Monitoring", false, NOT_WIRED("device sensors/metrics")),
  T("alerts", "Alerts", false, NOT_WIRED("device alerts")),
  T("events", "Events", false, NOT_WIRED("device events")),
  T("topology", "Topology", false, NOT_WIRED("device topology")),
  T("ai", "AI Analysis", false, NOT_WIRED("device AI analysis")),
  T("activity", "Activity", false, NOT_WIRED("device activity feed")),
  PROPERTIES,
];

const SITE_TABS: ResourceTab[] = [
  OVERVIEW,
  T("monitoring", "Monitoring", false, NOT_WIRED("site monitoring rollup")),
  T("alerts", "Alerts", false, NOT_WIRED("site alerts")),
  T("devices", "Devices", false, NOT_WIRED("site device list")),
  T("topology", "Topology", false, NOT_WIRED("site topology")),
  T("activity", "Activity", false, NOT_WIRED("site activity feed")),
  PROPERTIES,
];

const SOURCE_TABS: ResourceTab[] = [
  OVERVIEW,
  T("monitoring", "Monitoring", false, NOT_WIRED("source sensor rollup")),
  T("activity", "Activity", false, NOT_WIRED("source activity feed")),
  PROPERTIES,
];

const ALERT_TABS: ResourceTab[] = [
  OVERVIEW,
  T("ai", "AI Analysis", false, NOT_WIRED("alert correlation")),
  T("activity", "Activity", false, NOT_WIRED("alert activity feed")),
  PROPERTIES,
];

const INCIDENT_TABS: ResourceTab[] = [
  OVERVIEW,
  T("ai", "AI Analysis", false, NOT_WIRED("incident RCA / recommendation")),
  T("activity", "Activity", false, NOT_WIRED("incident activity feed")),
  PROPERTIES,
];

/** Shallow null-pruned projection for the generic Properties tab. */
export function pruneNulls(obj: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || v === undefined || v === "") continue;
    out[k] = v;
  }
  return out;
}

const sevTone = (s: string): ResourceHealthTone =>
  s === "critical" ? "bad" : s === "warning" ? "warn" : "idle";

export const RESOURCE_TYPES: ResourceTypeDefinition<unknown>[] = [
  {
    key: "devices",
    label: "Devices",
    description: "Devices from the asset inventory (/api/assets/devices).",
    implemented: true,
    supportsSearch: true,
    supportsPagination: true,
    tabs: DEVICE_TABS,
    columns: [
      { key: "name", label: "Name", value: (r) => orDash((r as AssetDevice).name) },
      { key: "status", label: "Status", value: (r) => orDash((r as AssetDevice).status) },
      { key: "type", label: "Type", value: (r) => orDash((r as AssetDevice).device_type) },
      { key: "site", label: "Site", value: () => "-" },
      { key: "vendor", label: "Vendor", value: (r) => orDash((r as AssetDevice).vendor) },
      { key: "ip", label: "IP Address", value: (r) => orDash((r as AssetDevice).ip_address), mono: true },
    ],
    actions: [],
    list: (tenantId, params) =>
      listAssetDevices(tenantId, {
        search: params.search,
        limit: params.limit ?? DEFAULT_RESOURCE_LIMIT,
      }) as Promise<unknown[]>,
    detail: (id, tenantId) => getAssetDevice(id, tenantId) as Promise<unknown>,
    toSummary: (row) => {
      const d = row as AssetDevice;
      return {
        id: d.id,
        name: d.name,
        subtitle: d.device_type,
        status: d.status,
        vendor: d.vendor ?? undefined,
        category: d.device_type,
        lastUpdated: d.last_seen_at ?? undefined,
      };
    },
    toProperties: (row) => pruneNulls(row as unknown as Record<string, unknown>),
  },
  {
    key: "sites",
    label: "Sites",
    description: "Sites from the asset inventory (/api/assets/sites).",
    implemented: true,
    supportsSearch: true,
    supportsPagination: true,
    tabs: SITE_TABS,
    columns: [
      { key: "name", label: "Name", value: (r) => orDash((r as AssetSite).name) },
      { key: "code", label: "Code", value: (r) => orDash((r as AssetSite).code), mono: true },
      { key: "region", label: "Region", value: (r) => orDash((r as AssetSite).region) },
      { key: "criticality", label: "Criticality", value: (r) => orDash((r as AssetSite).criticality) },
    ],
    actions: [],
    list: (tenantId, params) =>
      listAssetSites(tenantId, {
        search: params.search,
        limit: params.limit ?? DEFAULT_RESOURCE_LIMIT,
      }) as Promise<unknown[]>,
    toSummary: (row) => {
      const s = row as AssetSite;
      return { id: s.id, name: s.name, subtitle: s.code, site: s.name, category: "Site" };
    },
    toProperties: (row) => pruneNulls(row as unknown as Record<string, unknown>),
  },
  {
    key: "sources",
    label: "Monitoring Sources",
    description: "Connected monitoring platforms (/api/monitoring/sources).",
    implemented: true,
    supportsSearch: false,
    supportsPagination: false,
    tabs: SOURCE_TABS,
    columns: [
      { key: "name", label: "Name", value: (r) => orDash((r as MonitoringSourceOut).name) },
      { key: "type", label: "Type", value: (r) => orDash((r as MonitoringSourceOut).source_type) },
      { key: "vendor", label: "Vendor", value: (r) => orDash((r as MonitoringSourceOut).vendor) },
      { key: "status", label: "Status", value: (r) => orDash((r as MonitoringSourceOut).status) },
      { key: "host", label: "Hostname", value: (r) => orDash((r as MonitoringSourceOut).hostname), mono: true },
    ],
    actions: [],
    list: (tenantId) => listMonitoringSources(tenantId) as Promise<unknown[]>,
    toSummary: (row) => {
      const s = row as MonitoringSourceOut;
      return {
        id: s.id,
        name: s.name,
        subtitle: s.source_type,
        status: s.status,
        vendor: s.vendor ?? undefined,
        category: s.source_type,
        lastUpdated: s.last_sync_at ?? undefined,
      };
    },
    toProperties: (row) => pruneNulls(row as unknown as Record<string, unknown>),
  },
  {
    key: "alerts",
    label: "Alerts",
    description: "Alerts (/api/alerts).",
    implemented: true,
    supportsSearch: false,
    supportsPagination: true,
    tabs: ALERT_TABS,
    columns: [
      { key: "title", label: "Title", value: (r) => orDash((r as Alert).title) },
      { key: "severity", label: "Severity", value: (r) => orDash((r as Alert).severity) },
      { key: "status", label: "Status", value: (r) => orDash((r as Alert).status) },
      { key: "source", label: "Source", value: (r) => orDash((r as Alert).source) },
      { key: "triggered", label: "Triggered", value: (r) => orDash((r as Alert).triggered_at) },
    ],
    actions: [],
    list: (tenantId, params) =>
      listAlerts({ tenantId, limit: params.limit ?? DEFAULT_RESOURCE_LIMIT }) as Promise<unknown[]>,
    toSummary: (row) => {
      const a = row as Alert;
      return {
        id: a.id,
        name: a.title,
        subtitle: a.source,
        status: a.status,
        health: { label: a.severity, tone: sevTone(a.severity) },
        category: a.metric_type ?? "alert",
        lastUpdated: a.last_occurred_at ?? a.triggered_at,
      };
    },
    toProperties: (row) => pruneNulls(row as unknown as Record<string, unknown>),
    toDescription: (row) => (row as Alert).message ?? undefined,
  },
  {
    key: "incidents",
    label: "Incidents",
    description: "Incidents (/api/incidents).",
    implemented: true,
    supportsSearch: false,
    supportsPagination: true,
    tabs: INCIDENT_TABS,
    columns: [
      { key: "title", label: "Title", value: (r) => orDash((r as Incident).title) },
      { key: "severity", label: "Severity", value: (r) => orDash((r as Incident).severity) },
      { key: "status", label: "Status", value: (r) => orDash((r as Incident).status) },
      { key: "alerts", label: "Alerts", value: (r) => orDash((r as Incident).alert_count) },
      { key: "opened", label: "Opened", value: (r) => orDash((r as Incident).opened_at) },
    ],
    actions: [],
    list: (tenantId, params) =>
      listIncidents({
        tenantId,
        limit: params.limit ?? DEFAULT_RESOURCE_LIMIT,
      }) as Promise<unknown[]>,
    detail: (id, tenantId) => getIncident(id, tenantId) as Promise<unknown>,
    toSummary: (row) => {
      const i = row as Incident;
      return {
        id: i.id,
        name: i.title,
        subtitle: i.correlation_type ?? undefined,
        status: i.status,
        health: { label: i.severity, tone: sevTone(i.severity) },
        category: "Incident",
        lastUpdated: i.last_alert_at ?? i.opened_at,
      };
    },
    toProperties: (row) => pruneNulls(row as unknown as Record<string, unknown>),
    toDescription: (row) =>
      (row as Incident).root_cause_summary ?? (row as Incident).description ?? undefined,
  },
  {
    key: "interfaces",
    label: "Interfaces",
    description: "Network interfaces.",
    implemented: false,
    unavailableReason:
      "No interface list API is exposed to the portal yet (the monitoring API covers sensors, not interfaces).",
    supportsSearch: false,
    supportsPagination: false,
    tabs: BASE_TABS,
    columns: [],
    actions: [],
  },
  {
    key: "collectors",
    label: "Collectors",
    description: "Registered customer-side collectors.",
    implemented: false,
    unavailableReason:
      "Collector enrollment/registration APIs exist, but no collector list API is exposed to the portal yet.",
    supportsSearch: false,
    supportsPagination: false,
    tabs: BASE_TABS,
    columns: [],
    actions: [],
  },
];

export function getResourceType(key: string): ResourceTypeDefinition<unknown> | undefined {
  return RESOURCE_TYPES.find((r) => r.key === key);
}

/** Implemented types only - selectors and the catalog must not offer the rest. */
export function implementedResourceTypes(): ResourceTypeDefinition<unknown>[] {
  return RESOURCE_TYPES.filter((r) => r.implemented);
}
