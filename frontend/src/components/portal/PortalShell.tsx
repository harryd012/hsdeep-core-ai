"use client";

/**
 * Enterprise Portal shell (Portal V1, Step 1) + reusable states.
 *
 * Reusable, presentation-only chrome: collapsible grouped left nav, top bar,
 * and a main workspace with breadcrumb / title / description / actions and
 * consistent loading, empty and error states.
 *
 * It consumes the EXISTING authenticated API client and the existing design
 * tokens (`hud-panel`, --cyan/--line/--muted/--amber). No second HTTP client,
 * no second tenant mechanism, no fabricated data.
 *
 * It is mounted on /portal rather than imposed on (dashboard)/layout.tsx so the
 * ~45 existing pages keep their current chrome and deep links stay intact.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { PORTAL_NAV, type NavEntry, type NavSection } from "@/lib/portal-nav";

/* ------------------------------------------------------------------ states */

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" style={{ color: "var(--muted)", fontSize: 13 }}>
      {label}
    </p>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <p style={{ color: "var(--muted)", fontSize: 13 }}>{message}</p>;
}

export function ErrorState({ message }: { message: string }) {
  return (
    <p role="alert" style={{ color: "#ff6b6b", fontSize: 13 }}>
      {message}
    </p>
  );
}

/**
 * Health/status pill. Health is communicated by TEXT as well as colour, so it
 * never depends on colour alone (accessibility requirement).
 */
export function ResourceHealth({ label, tone }: { label: string; tone: "ok" | "warn" | "bad" | "idle" }) {
  const color =
    tone === "ok" ? "var(--cyan)" : tone === "warn" ? "var(--amber)" : tone === "bad" ? "#ff6b6b" : "var(--muted)";
  return (
    <span
      style={{
        fontSize: 11,
        letterSpacing: 0.5,
        padding: "2px 8px",
        borderRadius: 6,
        border: `1px solid ${color}`,
        color,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

/* -------------------------------------------------------------- nav pieces */

function NavLeaf({ entry, collapsed, depth }: { entry: NavEntry; collapsed: boolean; depth: number }) {
  const pathname = usePathname();
  const active = entry.route ? pathname === entry.route : false;

  if (!entry.route) {
    // Genuinely unimplemented: explicit unavailable state, not a dead link.
    return (
      <span
        title={entry.note ?? "Not implemented yet"}
        aria-disabled="true"
        style={{
          display: "block",
          padding: "4px 10px",
          paddingLeft: 10 + depth * 12,
          fontSize: 12,
          color: "var(--muted)",
          opacity: 0.7,
          cursor: "not-allowed",
        }}
      >
        {entry.label}
        {!collapsed && <span style={{ fontSize: 10 }}> · coming soon</span>}
      </span>
    );
  }

  return (
    <Link
      href={entry.route}
      title={collapsed ? entry.label : undefined}
      aria-current={active ? "page" : undefined}
      style={{
        display: "block",
        padding: "5px 10px",
        paddingLeft: 10 + depth * 12,
        fontSize: 12.5,
        textDecoration: "none",
        borderRadius: 6,
        color: active ? "var(--cyan)" : "#a7bec7",
        background: active ? "rgba(0,255,247,0.06)" : "transparent",
        borderLeft: active ? "2px solid var(--cyan)" : "2px solid transparent",
      }}
    >
      {collapsed ? entry.label.slice(0, 2).toUpperCase() : entry.label}
    </Link>
  );
}

function NavGroup({ section, collapsed }: { section: NavSection; collapsed: boolean }) {
  return (
    <div style={{ marginBottom: 14 }}>
      {!collapsed && (
        <div
          style={{
            fontSize: 10,
            letterSpacing: 1.2,
            color: "var(--muted)",
            padding: "0 10px 4px",
            textTransform: "uppercase",
          }}
        >
          {section.label}
        </div>
      )}
      <nav aria-label={section.label}>
        {section.entries.length === 0 && section.id === "favorites" ? (
          !collapsed && (
            <span style={{ display: "block", padding: "4px 10px", fontSize: 11, color: "var(--muted)" }}>
              No favorites yet
            </span>
          )
        ) : (
          section.entries.map((e) => (
            <div key={e.id}>
              <NavLeaf entry={e} collapsed={collapsed} depth={0} />
              {e.children?.map((c) => <NavLeaf key={c.id} entry={c} collapsed={collapsed} depth={1} />)}
            </div>
          ))
        )}
      </nav>
    </div>
  );
}

/* ------------------------------------------------------------- shell proper */

export type PortalShellProps = {
  /** Workspace breadcrumb, e.g. ["Services", "Monitoring"]. */
  breadcrumb?: string[];
  title: string;
  description?: string;
  /** Rendered at the right of the title row. */
  actions?: React.ReactNode;
  /** Label for the account slot (real session data passed by the caller). */
  accountLabel?: string;
  /** Global-search submit. Bounded by the caller; the shell itself does not fetch. */
  onSearch?: (query: string) => void;
  /** Optional results panel rendered under the top bar. */
  searchSlot?: React.ReactNode;
  /** Notification panel, shown when the top-bar entry is activated. */
  notificationSlot?: React.ReactNode;
  children: React.ReactNode;
};

export function PortalShell({
  breadcrumb = [],
  title,
  description,
  actions,
  accountLabel,
  onSearch,
  searchSlot,
  notificationSlot,
  children,
}: PortalShellProps) {
  // Persistent collapsed/expanded preference, read in a LAZY INITIALIZER rather
  // than an effect: calling setState inside an effect body is exactly what
  // react-hooks/set-state-in-effect forbids (and it causes cascading renders).
  // Falls back to expanded when storage is unavailable or we are server-side.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("hs-portal-nav-collapsed") === "1";
    } catch {
      return false;
    }
  });
  const [query, setQuery] = useState("");
  const [showNotifications, setShowNotifications] = useState(false);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem("hs-portal-nav-collapsed", next ? "1" : "0");
      } catch {
        /* non-fatal */
      }
      return next;
    });
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* LEFT NAVIGATION */}
      <aside
        style={{
          width: collapsed ? 56 : 232,
          flex: "0 0 auto",
          borderRight: "1px solid var(--line)",
          padding: "10px 8px",
          overflowY: "auto",
          transition: "width 120ms ease",
        }}
      >
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          style={{
            width: "100%",
            textAlign: "left",
            padding: "4px 10px",
            marginBottom: 10,
            fontSize: 11,
            background: "transparent",
            color: "var(--muted)",
            border: "1px solid var(--line)",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          {collapsed ? "»" : "« Collapse"}
        </button>
        {PORTAL_NAV.map((s) => (
          <NavGroup key={s.id} section={s} collapsed={collapsed} />
        ))}
      </aside>

      {/* MAIN COLUMN */}
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        {/* TOP BAR */}
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 16px",
            borderBottom: "1px solid var(--line)",
            flexWrap: "wrap",
          }}
        >
          <Link
            href="/portal"
            style={{ color: "var(--cyan)", fontSize: 13, letterSpacing: 1, textDecoration: "none", fontWeight: 600 }}
          >
            HSDEEP CORE AI
          </Link>
          <span style={{ fontSize: 10, color: "var(--muted)", letterSpacing: 0.5 }}>
            AUTONOMOUS INFRASTRUCTURE INTELLIGENCE
          </span>

          <form
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              onSearch?.(query);
            }}
            style={{ marginLeft: "auto", display: "flex", gap: 6 }}
          >
            <label htmlFor="portal-global-search" style={{ position: "absolute", left: -9999 }}>
              Global resource search
            </label>
            <input
              id="portal-global-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search resources, alerts, incidents..."
              style={searchInput}
            />
            <button type="submit" style={topBarBtn}>
              Search
            </button>
          </form>

          <button
            type="button"
            onClick={() => setShowNotifications((v) => !v)}
            aria-expanded={showNotifications}
            style={topBarBtn}
          >
            Notifications
          </button>

          <span style={{ fontSize: 12, color: "var(--muted)" }} title="Authenticated session">
            {accountLabel ?? "Session"}
          </span>
        </header>

        {searchSlot}
        {showNotifications && notificationSlot}

        {/* WORKSPACE */}
        <main style={{ padding: "14px 16px" }}>
          {breadcrumb.length > 0 && (
            <nav aria-label="Breadcrumb" style={breadcrumbStyle}>
              {breadcrumb.map((crumb, i) => (
                <span key={`${crumb}-${i}`}>
                  {i > 0 && " / "}
                  {crumb}
                </span>
              ))}
            </nav>
          )}
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <h1 style={{ fontSize: 18, margin: 0, color: "var(--cyan)" }}>{title}</h1>
            {actions && <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>{actions}</div>}
          </div>
          {description && (
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>{description}</p>
          )}
          <div style={{ marginTop: 12 }}>{children}</div>
        </main>
      </div>
    </div>
  );
}

const topBarBtn: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const searchInput: React.CSSProperties = {
  padding: "4px 8px",
  fontSize: 12,
  background: "transparent",
  color: "inherit",
  border: "1px solid var(--line)",
  borderRadius: 6,
  minWidth: 220,
};
const breadcrumbStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--muted)",
  marginBottom: 6,
};

/**
 * Catalogue card. `status` is derived by the caller from what ACTUALLY exists:
 * "Available capability" must only be used when a real route backs it.
 */
export function ServiceCard({
  title,
  description,
  status,
  href,
  note,
}: {
  title: string;
  description: string;
  status: "Available capability" | "Coming soon";
  href?: string;
  note?: string;
}) {
  const available = status === "Available capability";
  const body = (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{title}</strong>
        <ResourceHealth label={status} tone={available ? "ok" : "idle"} />
      </div>
      <p style={{ fontSize: 12, color: "#a7bec7", margin: "6px 0 0" }}>{description}</p>
      {note && <p style={{ fontSize: 11, color: "var(--muted)", margin: "4px 0 0" }}>{note}</p>}
    </>
  );

  if (!available || !href) {
    // Honest unavailable state - never a dead link, never fabricated content.
    return (
      <div className="hud-panel" style={{ padding: 12, opacity: 0.75 }}>
        {body}
      </div>
    );
  }
  return (
    <Link href={href} className="hud-panel" style={{ padding: 12, display: "block", textDecoration: "none" }}>
      {body}
    </Link>
  );
}

