"use client";

/**
 * (portal) route group shell.
 *
 * The /portal* pages render their own chrome through `PortalShell`, so this
 * layout deliberately does NOT mount the legacy `Sidebar` or `CopilotBar` - that
 * is precisely what removes the double navigation (two sidebars) that appeared
 * while these routes still lived inside `(dashboard)`.
 *
 * It DOES still mount `AuthGuard`, so the portal stays authenticated exactly like
 * every other authenticated area, and imports the same stylesheets the
 * `(dashboard)` group imports so the shared classes (`hud-panel`) and CSS
 * variables (--cyan/--line/--muted/--amber) keep resolving.
 *
 * Route groups do not affect the URL, so these pages still resolve to /portal,
 * /portal/operations and /portal/resources/*.
 *
 * /dashboard is untouched: it keeps the original (dashboard) chrome.
 */

import AuthGuard from "@/components/auth/AuthGuard";
import "@/app/styles/dashboard.css";
import "@/app/styles/dashboard-command-center.css";
import "@/app/styles/soc.css";
import "@/app/styles/dashboard-space-fix.css";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return <AuthGuard>{children}</AuthGuard>;
}
