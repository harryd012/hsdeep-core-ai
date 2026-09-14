"use client";

import {
  LayoutDashboard, Server, Activity, Bell, TriangleAlert, Ticket, ArrowLeftRight, BookOpen,
  Bot, Workflow, Database, CreditCard, Settings, ChevronRight, ChevronDown, Check,
    Building2, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  clearActiveTenantOverride, getActiveTenantOverride, getTenantId, setActiveTenantOverride,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface NavChild {
  label: string;
  route: string;
}

interface NavGroup {
  id: string;
  label: string;
  icon: LucideIcon;
  route: string;
  children?: NavChild[];
  /**
   * RBAC gate. MUST map to an existing code in the backend canonical
   * catalog (backend/app/core/permissions.py PERMISSION_CATALOG) — never
   * an invented code. Groups WITHOUT a permission are visible to every
   * authorized user. Nav visibility is UX convenience only; the backend
   * remains authoritative (every API route enforces its own permission).
   */
  permission?: string;
}

/**
 * Centralized HSDEEP CORE AI navigation configuration.
 * Groups map 1:1 to real routes (route-safety rule): every leaf points to an
 * existing page. Sub-items are only listed where a real page exists, so this
 * never fabricates dead navigation links.
 *
 * RBAC mapping notes (P0 #7):
 *  - Tickets/Changes have no `tickets.*`/`changes.*` codes in the catalog yet
 *    → left ungated (visible to all) rather than faking a permission.
 *  - AI Copilot and Settings mix capability areas with no single catalog code
 *    → left ungated for now (Settings gating on `users.read` would wrongly
 *    hide General/Notifications from operators).
 */
const navConfig: NavGroup[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, route: "/dashboard", permission: "dashboard.read" },
  { id: "infrastructure", label: "Infrastructure", icon: Server, route: "/infrastructure", permission: "sites.read", children: [
    { label: "Overview", route: "/infrastructure" },
    { label: "Asset Inventory", route: "/infrastructure/assets" },
    { label: "Topology", route: "/infrastructure-topology" },
  ] },
  { id: "org", label: "Organization", icon: Building2, route: "/organization", children: [
    { label: "Hierarchy", route: "/organization" },
  ] },
  { id: "monitoring", label: "Monitoring", icon: Activity, route: "/monitoring", permission: "monitoring.read" },
  { id: "alerts", label: "Alerts", icon: Bell, route: "/alerts", permission: "alerts.read" },
  { id: "incidents", label: "Incidents", icon: TriangleAlert, route: "/incidents", permission: "incidents.read" },
  { id: "tickets", label: "Tickets", icon: Ticket, route: "/tickets" },
  {
    id: "changes", label: "Changes", icon: ArrowLeftRight, route: "/changes", children: [
      { label: "Change Dashboard", route: "/changes" },
    ],
  },
  { id: "sop", label: "SOP Engine", icon: BookOpen, route: "/sop-engine", permission: "sops.read" },
  { id: "ai", label: "AI Copilot", icon: Bot, route: "/ai-copilot" },
  { id: "automation", label: "Automation", icon: Workflow, route: "/automation", permission: "workflows.read" },
  {
    id: "reports", label: "Reports", icon: Database, route: "/reports", permission: "dashboard.read", children: [
      { label: "Reports Overview", route: "/reports" },
      { label: "Alert Analysis", route: "/reports/alert-analysis" },
      { label: "Collector Performance", route: "/reports/collector-performance" },
      { label: "Infrastructure Summary", route: "/reports/infrastructure-summary" },
      { label: "SOP Execution Log", route: "/reports/sop-execution-log" },
    ],
  },
  { id: "subscription", label: "Subscription", icon: CreditCard, route: "/subscription", permission: "usage.read" },
  {
    id: "settings", label: "Settings", icon: Settings, route: "/settings", children: [
      { label: "General", route: "/settings/general" },
      { label: "Security", route: "/settings/security" },
      { label: "Users & Roles", route: "/settings/users-roles" },
      { label: "Notifications", route: "/settings/notifications" },
      { label: "API Keys", route: "/settings/api-keys" },
      { label: "Customers (Admin)", route: "/admin/customers" },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();
  // Per-group explicit toggle. Absent = derive from the active route, so the
  // group containing the current page auto-expands without an effect.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  // ── RBAC nav visibility (P0 #7) ──────────────────────────────────────────
  // Filters groups by the REAL permissions the backend returns from
  // /api/auth/me (mapped to the canonical catalog). Superusers see all.
  // While the session is still resolving (user null) nothing is hidden, so a
  // transient fetch never blanks the nav; the backend remains authoritative
  // for every route — this is UX, not security.
  const visibleGroups = (() => {
    if (!user || user.is_superuser) return navConfig;
    const granted = new Set(user.permissions);
    return navConfig.filter((g) => !g.permission || granted.has(g.permission));
  })();

  // Group is active when the route matches the landing page OR any child.
  function groupActive(g: NavGroup): boolean {
    const routes = [g.route, ...(g.children ?? []).map((c) => c.route)];
    return routes.includes(pathname) || routes.some((r) => r !== "/" && pathname.startsWith(r + "/"));
  }

  const activeId = visibleGroups.find(groupActive)?.id ?? null;

  function toggleGroup(g: NavGroup) {
    setOpenGroups((prev) => ({ ...prev, [g.id]: !prev[g.id] }));
    router.push(g.route);
  }

  // ── Active tenant (P0) ────────────────────────────────────────────────────
  // The active tenant is the user's own tenant, OR — for a superuser — the
  // tenant they've chosen to act on (persisted override). The backend resolves
  // this authoritatively per-request via the X-Tenant-Id header; the frontend
  // only reflects it here.
  const accessible = user?.accessible_tenants ?? [];
  const overrideId = getActiveTenantOverride();
  const activeTenant = (() => {
    if (overrideId) {
      const match = accessible.find((t) => t.id === overrideId);
      if (match) return match;
      // Stale/invalid override (e.g. tenant removed) — clear it.
      clearActiveTenantOverride();
    }
    return accessible.find((t) => t.id === user?.tenant_id) ?? null;
  })();
  const canSwitch = (user?.is_superuser) && accessible.length > 1;
  const [tenantOpen, setTenantOpen] = useState(false);
  const tenantRef = useRef<HTMLDivElement>(null);

  // Close the tenant dropdown on outside click.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (tenantRef.current && !tenantRef.current.contains(e.target as Node)) {
        setTenantOpen(false);
      }
    }
    if (tenantOpen) document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [tenantOpen]);

  function switchTenant(tenantId: string) {
    setActiveTenantOverride(tenantId);
    setTenantOpen(false);
    // Reload so all dashboard data refreshes under the new active tenant.
    window.location.reload();
  }

  return (
    <aside className="hs-sidebar">
      <nav aria-label="Primary navigation">
        {/* Tenant context / switcher */}
        <div className="hs-sidebar__ctx" ref={tenantRef}>
          <div className="hs-sidebar__ctx-top">
            <span className="hs-sidebar__ctx-label">TENANT</span>
            {canSwitch && (
              <button
                type="button"
                className="hs-sidebar__ctx-toggle"
                aria-expanded={tenantOpen}
                aria-haspopup="listbox"
                aria-label="Switch tenant"
                title="Switch tenant"
                onClick={() => setTenantOpen((o) => !o)}
              >
                <ChevronDown size={13} className={tenantOpen ? "open" : ""} />
              </button>
            )}
          </div>
          <button
            type="button"
            className={`hs-sidebar__ctx-value ${canSwitch ? "switchable" : ""}`}
            disabled={!canSwitch}
            title={activeTenant?.name ?? activeTenant?.id ?? getTenantId()}
            onClick={() => { if (canSwitch) setTenantOpen((o) => !o); }}
          >
            {activeTenant?.name ?? (activeTenant?.id === "00000000-0000-0000-0000-000000000000" ? "Default" : (activeTenant?.id?.slice(0, 8) ?? getTenantId().slice(0, 8)))}
          </button>

          {canSwitch && tenantOpen && (
            <ul className="hs-sidebar__tenant-list" role="listbox" aria-label="Accessible tenants">
              {accessible.map((t) => {
                const isActive = t.id === activeTenant?.id;
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      className={`hs-sidebar__tenant-item ${isActive ? "active" : ""}`}
                      onClick={() => switchTenant(t.id)}
                    >
                      <span className="hs-sidebar__tenant-name">{t.name}</span>
                      {!t.is_active && <span className="hs-sidebar__tenant-suspended">suspended</span>}
                      {isActive && <Check size={14} className="hs-sidebar__tenant-check" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {visibleGroups.map((g) => {
          const isActive = groupActive(g);
          const expanded = openGroups[g.id] !== undefined ? openGroups[g.id] : g.id === activeId;
          const hasChildren = !!g.children?.length;
          const Icon = g.icon;
          return (
            <div key={g.id} className="hs-sidebar__group" data-active={isActive || undefined}>
              <button
                type="button"
                className={isActive ? "active" : ""}
                aria-expanded={hasChildren ? expanded : undefined}
                aria-current={isActive ? "page" : undefined}
                onClick={() => { if (hasChildren) toggleGroup(g); else router.push(g.route); }}
              >
                <Icon size={19} />
                <span>{g.label}</span>
                {hasChildren && (
                  <ChevronRight size={15} className={`hs-sidebar__chev ${expanded ? "open" : ""}`} />
                )}
              </button>

              {hasChildren && expanded && (
                <div className="hs-sidebar__children">
                  {g.children!.map((child) => {
                    const childActive = pathname === child.route;
                    return (
                      <Link
                        key={child.route + child.label}
                        href={child.route}
                        className="hs-sidebar__child"
                        aria-current={childActive ? "page" : undefined}
                      >
                        {child.label}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      <div className="system-status">
        <b>HSDEEP CORE AI<br />SYSTEM STATUS</b>
        <div className="pulse-icon"><Activity /></div>
        <small><i /> All Systems Operational</small>
      </div>
    </aside>
  );
}