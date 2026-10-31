import {
  Activity, ArrowLeftRight, Bell, BookOpen, Bot, Building2, CreditCard, Database, LayoutDashboard,
  LayoutGrid, Server, Settings, Shield, Ticket, TriangleAlert, Workflow, type LucideIcon,
} from "lucide-react";

/**
 * Centralized HSDEEP CORE AI navigation — the single source for the sidebar
 * and the Apps launcher (Ctrl+K), so the two can never disagree.
 *
 * Route-safety rule: every leaf points to an existing page; sub-items are only
 * listed where a real page exists, so this never fabricates dead links.
 *
 * RBAC: `permission` MUST map to an existing code in the backend canonical
 * catalog (backend/app/core/permissions.py PERMISSION_CATALOG). Groups without
 * one are visible to every authorized user. Nav visibility is UX only; every
 * API route enforces its own permission.
 *  - Tickets/Changes have no `tickets.*`/`changes.*` codes yet → ungated.
 *  - AI Copilot and Settings mix capability areas → ungated for now.
 */

export interface NavChild {
  label: string;
  route: string;
  /** Extra words the launcher search matches. */
  keywords?: string;
}

export type NavSection = "Observe" | "Infrastructure" | "Respond" | "Security" | "Automation & AI" | "Manage";

export const NAV_SECTIONS: readonly NavSection[] = ["Observe", "Infrastructure", "Respond", "Security", "Automation & AI", "Manage"];

export interface NavGroup {
  id: string;
  label: string;
  icon: LucideIcon;
  route: string;
  section: NavSection;
  /** One line shown in the Apps launcher. */
  description: string;
  children?: NavChild[];
  permission?: string;
}

export const NAV_CONFIG: NavGroup[] = [
  // -- Observe ---------------------------------------------------------------
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, route: "/dashboard", section: "Observe", permission: "dashboard.read",
    description: "Executive Command Center: estate health, incidents and response KPIs" },
  // No permission gate on purpose: /portal enforces authorization server-side.
  { id: "command-center", label: "Command Center", icon: LayoutGrid, route: "/portal", section: "Observe",
    description: "Operations portal: live alerts, incidents and services" },
  { id: "monitoring", label: "Monitoring", icon: Activity, route: "/monitoring", section: "Observe", permission: "monitoring.read",
    description: "Collection sources and sensors" },
  { id: "alerts", label: "Alerts", icon: Bell, route: "/alerts", section: "Observe", permission: "alerts.read",
    description: "Alerts from every connected source" },
  { id: "reports", label: "Reports", icon: Database, route: "/reports", section: "Observe", permission: "dashboard.read",
    description: "Operational reports",
    children: [
      { label: "Reports Overview", route: "/reports" },
      { label: "Alert Analysis", route: "/reports/alert-analysis", keywords: "mttr trends" },
      { label: "Collector Performance", route: "/reports/collector-performance", keywords: "sync lag" },
      { label: "Infrastructure Summary", route: "/reports/infrastructure-summary", keywords: "inventory" },
      { label: "SOP Execution Log", route: "/reports/sop-execution-log", keywords: "runbook history" },
    ] },

  // -- Infrastructure ------------------------------------------------------------
  { id: "infrastructure", label: "Infrastructure", icon: Server, route: "/infrastructure", section: "Infrastructure", permission: "sites.read",
    description: "Devices, resources, topology and discovery",
    children: [
      { label: "Overview", route: "/infrastructure" },
      { label: "Hosts", route: "/infrastructure/hosts", keywords: "servers devices health facets" },
      { label: "Universal Resources", route: "/infrastructure/universal", keywords: "cloud firewall router switch" },
      { label: "Asset Inventory", route: "/infrastructure/assets", keywords: "cmdb devices" },
      { label: "Firewalls", route: "/infrastructure/firewalls", keywords: "firewall fortigate palo alto ha vpn licence" },
      { label: "Backup", route: "/infrastructure/backup", keywords: "azure backup recovery services vault rpo restore" },
      { label: "Explorer", route: "/explorer", keywords: "hosts sites sensors" },
      { label: "Topology", route: "/infrastructure-topology/live", keywords: "map smartscape graph" },
      { label: "Discovery", route: "/infrastructure/discovery", keywords: "scan probe snmp" },
    ] },
  { id: "org", label: "Organization", icon: Building2, route: "/organization", section: "Infrastructure",
    description: "Tenant, people and estate hierarchy",
    children: [{ label: "Hierarchy", route: "/organization" }] },

  // -- Respond --------------------------------------------------------------------
  { id: "incidents", label: "Incidents", icon: TriangleAlert, route: "/incidents", section: "Respond", permission: "incidents.read",
    description: "Correlated incidents and their response workflow",
    children: [
      { label: "All Incidents", route: "/incidents" },
      { label: "NOC Event Log", route: "/noc-event-log", keywords: "noc events correlation timeline" },
    ] },
  { id: "tickets", label: "Tickets", icon: Ticket, route: "/tickets", section: "Respond",
    description: "Service tickets with SLA tracking" },
  { id: "changes", label: "Changes", icon: ArrowLeftRight, route: "/changes", section: "Respond",
    description: "Change requests and approvals",
    children: [{ label: "Change Dashboard", route: "/changes" }] },

  // -- Security ------------------------------------------------------------------
  { id: "soc", label: "SOC Analysis", icon: Shield, route: "/soc-analysis", section: "Security",
    description: "Security events and the analyst team",
    children: [
      { label: "SOC Dashboard", route: "/soc-analysis" },
      { label: "Analyst Roster", route: "/soc-analyst-roster" },
      { label: "Firewall Diagnostic", route: "/firewall-diagnostic", keywords: "palo alto runbook rca diagnostic" },
    ] },

  // -- Automation & AI --------------------------------------------------------------
  { id: "ai", label: "AI Copilot", icon: Bot, route: "/ai-copilot", section: "Automation & AI",
    description: "AI CTO, agents and the operations copilot" },
  { id: "automation", label: "Automation", icon: Workflow, route: "/automation", section: "Automation & AI", permission: "workflows.read",
    description: "Scheduled collection and automated remediation" },
  { id: "sop", label: "SOP Engine", icon: BookOpen, route: "/sop-engine", section: "Automation & AI", permission: "sops.read",
    description: "Standard operating procedures" },

  // -- Manage -------------------------------------------------------------------------
  { id: "subscription", label: "Subscription", icon: CreditCard, route: "/subscription", section: "Manage", permission: "usage.read",
    description: "Plan, usage limits and billing",
    children: [
      { label: "Plan & Billing", route: "/subscription" },
      { label: "Usage", route: "/usage", keywords: "consumption metering quota" },
    ] },
  { id: "settings", label: "Settings", icon: Settings, route: "/settings", section: "Manage",
    description: "Tenant, security, users, notifications and API keys",
    children: [
      { label: "General", route: "/settings/general" },
      { label: "Security", route: "/settings/security", keywords: "sso mfa password" },
      { label: "Users & Roles", route: "/settings/users-roles", keywords: "rbac permissions" },
      { label: "Notifications", route: "/settings/notifications", keywords: "email slack pagerduty" },
      { label: "API Keys", route: "/settings/api-keys", keywords: "tokens" },
      { label: "Customers (Admin)", route: "/admin/customers", keywords: "tenants msp" },
    ] },
];

/** Groups the signed-in user may see. While the session resolves nothing is hidden. */
export function visibleNav(user: { is_superuser: boolean; permissions: string[] } | null | undefined): NavGroup[] {
  if (!user || user.is_superuser) return NAV_CONFIG;
  const granted = new Set(user.permissions);
  return NAV_CONFIG.filter((g) => !g.permission || granted.has(g.permission));
}

export interface LauncherEntry {
  key: string;
  label: string;
  route: string;
  section: NavSection;
  parent: string | null;
  description: string;
  icon: LucideIcon;
  haystack: string;
}

/** Flattened, searchable entries: each group, plus each child page that is not the group's own landing page. */
export function launcherEntries(groups: NavGroup[]): LauncherEntry[] {
  const out: LauncherEntry[] = [];
  for (const g of groups) {
    out.push({
      key: g.id,
      label: g.label,
      route: g.route,
      section: g.section,
      parent: null,
      description: g.description,
      icon: g.icon,
      haystack: `${g.label} ${g.description} ${g.section}`.toLowerCase(),
    });
    for (const c of g.children ?? []) {
      if (c.route === g.route) continue;
      out.push({
        key: `${g.id}:${c.route}`,
        label: c.label,
        route: c.route,
        section: g.section,
        parent: g.label,
        description: `${g.label} › ${c.label}`,
        icon: g.icon,
        haystack: `${c.label} ${g.label} ${c.keywords ?? ""} ${g.section}`.toLowerCase(),
      });
    }
  }
  return out;
}

/** Every whitespace-separated term must appear somewhere in the entry. */
export function searchLauncher(entries: LauncherEntry[], query: string): LauncherEntry[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return entries;
  return entries.filter((e) => terms.every((t) => e.haystack.includes(t)));
}
