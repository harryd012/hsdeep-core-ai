/**
 * Settings hub navigation configuration.
 *
 * This is the single, typed source of truth for the settings landing-page
 * grid — it replaces the former inline `SETTINGS_SECTIONS` mock array that
 * was hardcoded inside the page component. Each entry's `href` corresponds
 * to a real App Router route under `src/app/(dashboard)/settings/*`, and the
 * RBAC fields mirror the FastAPI guards in:
 *
 *   - backend/app/core/auth.py        →  require_admin (is_superuser)
 *   - backend/app/core/permissions.py →  permission codes (e.g. "users.read")
 *   - backend/app/api/routes/settings.py & auth.py
 */

import type { LucideIcon } from "lucide-react";
import { Settings, ShieldCheck, Bell, Users, Key } from "lucide-react";

export interface SettingsSection {
  /** Stable key for React rendering and analytics. */
  id: string;
  /** Card title — shown in bold beneath the icon. */
  title: string;
  /** Short description shown beneath the title. */
  description: string;
  /** Lucide icon rendered at the top of the card. */
  icon: LucideIcon;
  /** App Router destination. Must match an existing route on disk. */
  href: string;
  /**
   * Permission code required to view this section (e.g. "users.read").
   * When omitted the section is visible to every authenticated user.
   */
  permission?: string;
  /**
   * When true, only superusers (is_superuser === true) may see this section.
   * Maps to the backend's `require_admin` guard.
   */
  adminOnly?: boolean;
}

/**
 * The canonical set of settings sections.
 *
 * Sourced from the actual routes that exist on disk and the permission
 * model in `app/core/permissions.py`:
 *
 *  - General, Security, Notifications: read access is open to all
 *    authenticated tenants (the GET endpoints use `Depends(get_tenant_id)`
 *    with no `require_permission` guard).
 *  - Users & Roles: the sub-page calls `listUsers` / `listRoles`, both of
 *    which require the `users.read` permission (granted to every role from
 *    Viewer upward in DEFAULT_ROLES).
 *  - API Keys: create + delete are guarded by `require_admin` in
 *    `settings.py`, so only superusers can manage keys.
 */
export const SETTINGS_SECTIONS: SettingsSection[] = [
  {
    id: "general",
    title: "General",
    description: "Platform name, timezone, branding",
    icon: Settings,
    href: "/settings/general",
  },
  {
    id: "security",
    title: "Security",
    description: "Password policy, MFA, session timeout",
    icon: ShieldCheck,
    href: "/settings/security",
  },
  {
    id: "notifications",
    title: "Notifications",
    description: "Email, Slack, PagerDuty channels",
    icon: Bell,
    href: "/settings/notifications",
  },
  {
    id: "users-roles",
    title: "Users & Roles",
    description: "Tenant users, RBAC permissions",
    icon: Users,
    href: "/settings/users-roles",
    permission: "users.read",
  },
  {
    id: "api-keys",
    title: "API Keys",
    description: "Manage integration tokens",
    icon: Key,
    href: "/settings/api-keys",
    adminOnly: true,
  },
];

/**
 * Prune the section list to only those the current principal is authorised
 * to see.
 *
 *  - Sections with no permission requirement are always visible.
 *  - Sections marked `adminOnly` are shown only to superusers.
 *  - Sections with a `permission` code are shown to superusers or anyone
 *    holding that exact code in their resolved permission set.
 *
 * If `me` is `null` (e.g. the principal couldn't be resolved because there
 * is no auth session yet, or the `/me` call failed) every section is
 * returned so the hub still renders. Individual sub-pages enforce their own
 * backend guards independently, so this is a progressive-enhancement only.
 */
export function filterSectionsByPermission(
  sections: SettingsSection[],
  me: { is_superuser: boolean; permissions: string[] } | null,
): SettingsSection[] {
  if (!me) return sections;
  return sections.filter((s) => {
    if (s.adminOnly) return me.is_superuser;
    if (s.permission)
      return me.is_superuser || me.permissions.includes(s.permission);
    return true;
  });
}
