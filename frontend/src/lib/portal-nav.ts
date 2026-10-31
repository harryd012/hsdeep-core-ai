/**
 * Enterprise Portal — navigation model (Portal V1, Step 1).
 *
 * SINGLE source of truth for portal navigation. It deliberately maps the portal
 * IA onto routes that ALREADY exist: per the milestone rules an existing route
 * must be integrated rather than duplicated, and deep links must not break.
 *
 * Where a service has no implementation yet the entry carries `comingSoon: true`
 * and NO route. It renders as an explicit unavailable state - never fabricated
 * data, never a dead link.
 *
 * Nothing here is invented: every `route` was verified present in
 * src/app/(dashboard).
 */

export type NavEntry = {
  id: string;
  label: string;
  /** Existing route. Absent when the capability is not implemented yet. */
  route?: string;
  /** True when the capability genuinely does not exist yet. */
  comingSoon?: boolean;
  /** Short note shown for coming-soon entries. */
  note?: string;
  children?: NavEntry[];
};

export type NavSection = {
  id: "home" | "favorites" | "services" | "resources" | "operations" | "admin";
  label: string;
  entries: NavEntry[];
};

const COMING_SOON = (id: string, label: string, note: string): NavEntry => ({
  id,
  label,
  comingSoon: true,
  note,
});

export const PORTAL_NAV: NavSection[] = [
  {
    id: "home",
    label: "Home",
    entries: [{ id: "home", label: "Command Center", route: "/dashboard" }],
  },
  {
    id: "favorites",
    label: "Favorites",
    entries: [], // populated by the shell from local preferences
  },
  {
    id: "services",
    label: "Services",
    entries: [
      { id: "svc-monitoring", label: "Monitoring", route: "/monitoring" },
      { id: "svc-discovery", label: "Discovery", route: "/infrastructure/discovery" },
      {
        id: "svc-netint",
        label: "Network Intelligence",
        route: "/infrastructure-topology",
        children: [
          { id: "svc-netint-topology", label: "Topology", route: "/infrastructure-topology" },
          { id: "svc-netint-rel", label: "Relationships", route: "/infrastructure/assets" },
          COMING_SOON("svc-netint-sdwan", "SD-WAN", "No SD-WAN view exists yet."),
          COMING_SOON("svc-netint-sase", "SASE", "No SASE view exists yet."),
          COMING_SOON("svc-netint-paths", "Network Paths", "No path-analysis view exists yet."),
        ],
      },
      { id: "svc-operations", label: "Operations", route: "/portal/operations" },
      { id: "svc-ai", label: "AI Intelligence", route: "/ai-copilot" },
      { id: "svc-automation", label: "Automation", route: "/automation" },
      { id: "svc-security", label: "Security", route: "/soc-analysis" },
      COMING_SOON("svc-integrations", "Integrations", "No integration catalog page exists yet."),
    ],
  },
  {
    id: "resources",
    label: "Resources",
    entries: [
      { id: "res-devices", label: "Devices", route: "/portal/resources/devices" },
      { id: "res-sites", label: "Sites", route: "/portal/resources/sites" },
      { id: "res-sources", label: "Monitoring Sources", route: "/portal/resources/sources" },
      { id: "res-alerts", label: "Alerts", route: "/portal/resources/alerts" },
      { id: "res-incidents", label: "Incidents", route: "/portal/resources/incidents" },
      COMING_SOON("res-collectors", "Collectors", "Collector APIs exist; no collector page exists yet."),
      COMING_SOON("res-servers", "Servers", "No server inventory page exists yet."),
      COMING_SOON("res-networks", "Networks", "No network inventory page exists yet."),
      COMING_SOON("res-cloud", "Cloud Resources", "No cloud inventory page exists yet."),
    ],
  },
  {
    id: "operations",
    label: "Operations",
    entries: [
      { id: "ops-alerts", label: "Alerts", route: "/portal/resources/alerts" },
      { id: "ops-incidents", label: "Incidents", route: "/portal/resources/incidents" },
      { id: "ops-changes", label: "Changes", route: "/changes" },
      { id: "ops-tickets", label: "Tickets", route: "/tickets" },
      { id: "ops-events", label: "Event Log", route: "/noc-event-log" },
      { id: "ops-sop", label: "SOP / Runbooks", route: "/sop-engine" },
      COMING_SOON("ops-problems", "Problems", "No problem-management page exists yet."),
      COMING_SOON("ops-maintenance", "Maintenance", "No maintenance-window page exists yet."),
      COMING_SOON("ops-health", "Service Health", "No service-health view exists yet."),
    ],
  },
  {
    id: "admin",
    label: "Administration",
    entries: [
      { id: "adm-users", label: "Users & Roles", route: "/settings/users-roles" },
      { id: "adm-customers", label: "Tenants / Customers", route: "/admin/customers" },
      { id: "adm-subscription", label: "Subscription", route: "/subscription" },
      { id: "adm-usage", label: "Usage & Entitlements", route: "/usage" },
      { id: "adm-sources", label: "Monitoring Sources", route: "/monitoring" },
      COMING_SOON("adm-collectors", "Collectors", "No collector admin page exists yet."),
      { id: "adm-settings", label: "Settings", route: "/settings/general" },
      { id: "adm-apikeys", label: "API Keys", route: "/settings/api-keys" },
      { id: "adm-security", label: "Security Settings", route: "/settings/security" },
      COMING_SOON("adm-audit", "Audit", "No audit-log page exists yet."),
    ],
  },
];

/** Flat list of routed entries (for search and favourites). */
export function flatNavEntries(sections: NavSection[] = PORTAL_NAV): NavEntry[] {
  const out: NavEntry[] = [];
  const walk = (entries: NavEntry[]) => {
    for (const e of entries) {
      if (e.route) out.push(e);
      if (e.children) walk(e.children);
    }
  };
  for (const s of sections) walk(s.entries);
  return out;
}
