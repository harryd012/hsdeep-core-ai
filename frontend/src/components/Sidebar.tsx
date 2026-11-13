"use client";

import { Activity, Check, ChevronDown, ChevronRight, Menu, PanelLeftClose, PanelLeftOpen, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  clearActiveTenantOverride, getActiveTenantOverride, getAlertSummary, getTenantId, setActiveTenantOverride,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAiCtoStatus } from "@/hooks/useAiCtoStatus";
import { dependencyStatusLine } from "@/lib/health";
import AppearanceMenu from "@/components/AppearanceMenu";
import AppLauncher from "@/components/AppLauncher";
import SupportAccessForm from "@/components/SupportAccessForm";
import "@/app/styles/executive-command-center.css";
import "@/app/styles/navigation.css";
import { NAV_SECTIONS, visibleNav, type NavGroup } from "@/lib/navigation";
import { NAV_STORAGE_KEY } from "@/lib/theme";
import { MOBILE_NAV_QUERY, collapseButtonRole, drawerReducer } from "@/lib/mobile-nav";

function subscribeMobile(onChange: () => void) {
  const mq = window.matchMedia(MOBILE_NAV_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
const isMobileNow = () => window.matchMedia(MOBILE_NAV_QUERY).matches;
const isMobileOnServer = () => false;

// The per-dependency system status line is derived in lib/health.ts
// (dependencyStatusLine) so the sidebar and the status tests share one source.

/** Navigation model and RBAC rules live in lib/navigation.ts (shared with the Apps launcher). */

export default function Sidebar() {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const { user } = useAuth();
  // Real dependency state for the SYSTEM STATUS block. This block previously
  // read a hardcoded "All Systems Operational", which meant the platform could
  // report a healthy system while the database or Redis was down. It now
  // derives from the same real probe the rest of the portal uses.
  const { data: aiCto } = useAiCtoStatus();
  // Per-group explicit toggle. Absent = derive from the active route, so the
  // group containing the current page auto-expands without an effect.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  // ── RBAC nav visibility (P0 #7) ──────────────────────────────────────────
  // Filters groups by the REAL permissions the backend returns from
  // /api/auth/me (mapped to the canonical catalog). Superusers see all.
  // While the session is still resolving (user null) nothing is hidden, so a
  // transient fetch never blanks the nav; the backend remains authoritative
  // for every route — this is UX, not security.
  const visibleGroups = visibleNav(user);

  // ── Apps launcher (Ctrl+K / ⌘K) and collapsed rail ─────────────────────
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => typeof document !== "undefined" && document.documentElement.getAttribute("data-nav") === "collapsed",
  );
  // ── Phone-width overlay drawer (lib/mobile-nav.ts) ─────────────────────
  // Below the breakpoint the sidebar is off-canvas and closed by default;
  // the top bar's menu button opens it. Following a link, Escape, the
  // backdrop or the brand-row button (the rail collapse control on desktop)
  // closes it.
  const mobile = useSyncExternalStore(subscribeMobile, isMobileNow, isMobileOnServer);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerPath, setDrawerPath] = useState(pathname);
  if (drawerPath !== pathname) {
    setDrawerPath(pathname);
    setDrawerOpen((o) => drawerReducer(o, { type: "navigate" }));
  }
  const drawerShown = mobile && drawerOpen;
  const collapseRole = collapseButtonRole(mobile, collapsed);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setLauncherOpen((o) => !o);
      }
      if (e.key === "Escape") setDrawerOpen((o) => drawerReducer(o, { type: "key", key: e.key }));
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  function toggleCollapsed() {
    if (collapseRole.action === "close") {
      setDrawerOpen((o) => drawerReducer(o, { type: "close" }));
      return;
    }
    const next = !collapsed;
    setCollapsed(next);
    document.documentElement.setAttribute("data-nav", next ? "collapsed" : "expanded");
    try {
      localStorage.setItem(NAV_STORAGE_KEY, next ? "collapsed" : "expanded");
    } catch {
      // Storage unavailable: the rail still toggles for this visit.
    }
  }

  // Live count on the Alerts entry: open (unacknowledged) alerts. Same cached
  // read the Alerts page uses; failures simply show no badge.
  const { data: alertSummary } = useQuery({
    queryKey: ["nav", "alert-summary"],
    queryFn: () => getAlertSummary(),
    enabled: !!user,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  const badges: Record<string, number | undefined> = { alerts: alertSummary?.by_status.open };

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
  // Customer tenant awaiting a break-glass reason before the switch.
  const [pendingTenant, setPendingTenant] = useState<{ id: string; name: string } | null>(null);
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

  function switchTenant(tenantId: string, name: string) {
    // Another tenant needs a support grant (reason + duration) first; the
    // user's own tenant just drops the override.
    const home = user?.home_tenant_id ?? user?.tenant_id;
    if (tenantId !== home) {
      setPendingTenant({ id: tenantId, name });
      return;
    }
    clearActiveTenantOverride();
    setTenantOpen(false);
    window.location.reload();
  }

  function grantOpened(tenantId: string) {
    setActiveTenantOverride(tenantId);
    setPendingTenant(null);
    setTenantOpen(false);
    // Reload so all dashboard data refreshes under the new active tenant.
    window.location.reload();
  }

  return (
    <>
    <div className="hs-mobilebar">
      <button
        type="button"
        className="hs-mobilebar__menu"
        onClick={() => setDrawerOpen((o) => drawerReducer(o, { type: "toggle" }))}
        aria-label="Open navigation"
        aria-expanded={drawerShown}
        aria-controls="hs-primary-sidebar"
      >
        <Menu size={20} />
      </button>
      <span className="hs-brand__mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="16" height="16"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 2.3 6.8 3.8L12 12 5.2 8.1 12 4.3Z" fill="currentColor" /></svg>
      </span>
      <b className="hs-mobilebar__title">HSDEEP CORE AI</b>
    </div>
    <AppLauncher open={launcherOpen} onClose={() => setLauncherOpen(false)} groups={visibleGroups} />
    {drawerShown && (
      <div className="hs-drawer-backdrop" aria-hidden="true" onClick={() => setDrawerOpen((o) => drawerReducer(o, { type: "close" }))} />
    )}
    <aside
      id="hs-primary-sidebar"
      className="hs-sidebar"
      data-collapsed={(!mobile && collapsed) || undefined}
      data-drawer-open={drawerShown || undefined}
    >
      <div className="hs-brand">
        <span className="hs-brand__mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 2.3 6.8 3.8L12 12 5.2 8.1 12 4.3Z" fill="currentColor" /></svg>
        </span>
        <span className="hs-brand__text">
          <b>HSDEEP CORE AI</b>
          <small>AI observability</small>
        </span>
        <button
          type="button"
          className="hs-brand__collapse"
          onClick={toggleCollapsed}
          aria-label={collapseRole.label}
          title={collapseRole.label}
        >
          {collapseRole.action === "close" ? <X size={16} /> : collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>
      <button type="button" className="hs-search" onClick={() => setLauncherOpen(true)} title="Search apps (Ctrl+K)">
        <Search size={15} aria-hidden="true" />
        <span>Search apps</span>
        <kbd>Ctrl K</kbd>
      </button>
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
            {activeTenant?.name ?? (activeTenant?.id?.slice(0, 8) || getTenantId().slice(0, 8) || "—")}
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
                      onClick={() => switchTenant(t.id, t.name)}
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
          {canSwitch && pendingTenant && (
            <SupportAccessForm
              tenantId={pendingTenant.id}
              tenantName={pendingTenant.name}
              onOpened={() => grantOpened(pendingTenant.id)}
              onCancel={() => setPendingTenant(null)}
            />
          )}
        </div>

        {NAV_SECTIONS.map((section) => {
          const groups = visibleGroups.filter((g) => g.section === section);
          if (!groups.length) return null;
          return (
            <div key={section} className="hs-sidebar__section" role="group" aria-label={section}>
              <span className="hs-sidebar__section-label">{section}</span>
        {groups.map((g: NavGroup) => {
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
                title={collapsed && !mobile ? g.label : undefined}
              >
                <Icon size={18} />
                <span>{g.label}</span>
                {badges[g.id] ? (
                  <em className="hs-sidebar__badge" aria-label={`${badges[g.id]} open`}>
                    {badges[g.id]! > 99 ? "99+" : badges[g.id]}
                  </em>
                ) : null}
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
            </div>
          );
        })}
      </nav>
      <AppearanceMenu variant="sidebar" />
      <div className="system-status">
        <b>HSDEEP CORE AI<br />SYSTEM STATUS</b>
        <div className="pulse-icon"><Activity /></div>
        <small>
          <i /> {dependencyStatusLine(aiCto?.services)}
        </small>
      </div>
    </aside>
    </>
  );
}
// NAV: Firewall Diagnostic -> /(dashboard)/firewall-diagnostic
