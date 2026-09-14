"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getMe } from "@/lib/api";
import {
  SETTINGS_SECTIONS,
  filterSectionsByPermission,
} from "@/lib/settingsSections";
import type { SettingsSection } from "@/lib/settingsSections";
import SettingsPanel from "@/components/settings/SettingsPanel";

/**
 * Settings hub landing page.
 *
 * Replaces the former static, hardcoded card markup with the reusable
 * SettingsPanel component. Navigation is wired to the Next.js router via
 * router.push(section.href), and the visible sections are filtered by the
 * current user's RBAC context (fetched from GET /api/auth/me) so that
 * non-admin users don't see cards they can't act on — "Users & Roles"
 * (requires users.read) and "API Keys" (admin-only).
 */
export default function SettingsPage() {
  const router = useRouter();

  // Start with the full list so the grid renders immediately; RBAC
  // filtering is a progressive enhancement that prunes cards once /me
  // resolves.
  const [sections, setSections] = useState<SettingsSection[]>(SETTINGS_SECTIONS);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (!cancelled) {
          setSections(filterSectionsByPermission(SETTINGS_SECTIONS, me));
        }
      })
      .catch(() => {
        // Can't resolve the principal (no session / backend unreachable).
        // Keep the full list — the sub-pages enforce their own auth guards.
        if (!cancelled) setSections(SETTINGS_SECTIONS);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleNavigate(section: SettingsSection) {
    router.push(section.href);
  }

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>SETTINGS</h2>
          <SettingsPanel sections={sections} onNavigate={handleNavigate} />
        </section>
      </div>
    </div>
  );
}