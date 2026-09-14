"use client";

import { useRouter } from "next/navigation";
import { API_BASE_URL, getTenantId } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DashboardHeaderData } from "@/lib/dashboard/dashboardModels";

/**
 * DashboardMasthead — Zone 0 (Enterprise header + global context bar).
 *
 * PRESENTATIONAL: receives already-fetched context via the optional `header`
 * prop (from useDashboardData) and renders ONLY what the backend actually
 * provides. No user, tenant or status value is ever invented; when the
 * backend has not answered yet the strip simply shows the static product
 * identity. No fetching happens here.
 *
 * Context rendered (all from existing app data):
 *   - TENANT   → getTenantId() (existing lib/api export)
 *   - USER     → GET /api/auth/me (existing getMe) — only when available
 *   - API      → API_BASE_URL (existing build config)
 */

function apiHost(): string {
  try {
    return new URL(API_BASE_URL).host;
  } catch {
    return API_BASE_URL;
  }
}

export default function DashboardMasthead({ header }: { header?: DashboardHeaderData }) {
  const tenantId = header?.tenantId ?? getTenantId();
  const user = header?.user ?? null;
  const { logout, isAuthenticated } = useAuth();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.replace("/login");
  }

  return (
    <header className="hs-noc__masthead">
      {/* Brand identity — mirrors the approved login-page header language */}
      <div className="hs-noc__masthead-brand">
        <svg
          viewBox="0 0 48 48"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="hs-noc__masthead-logo"
          aria-hidden="true"
        >
          <circle cx="24" cy="24" r="22" stroke="currentColor" strokeWidth="1.5" opacity="0.6" />
          <circle cx="24" cy="24" r="16" stroke="currentColor" strokeWidth="1" opacity="0.3" />
          <path d="M16 24h16M24 16v16M18.34 18.34l11.32 11.32M29.66 18.34L18.34 29.66" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.5" />
          <circle cx="24" cy="24" r="4" fill="currentColor" opacity="0.8" />
        </svg>
        <div className="hs-noc__masthead-id">
          <h1>HSDEEP CORE AI</h1>
          <p>ENTERPRISE AIOPS CONTROL PLANE · MULTI-TENANT OPERATIONS</p>
        </div>
      </div>

      <dl className="hs-noc__context">
        {user && (
          <div>
            <dt>USER</dt>
            <dd title={user.email}>{user.fullName || user.email}</dd>
          </div>
        )}
        <div>
          <dt>TENANT</dt>
          <dd title={tenantId}>{tenantId}</dd>
        </div>
        <div>
          <dt>API</dt>
          <dd title={API_BASE_URL}>{apiHost()}</dd>
        </div>
        <div>
          <dt>AUTO REFRESH</dt>
          <dd>ACTIVE · 5–60S PER PANEL</dd>
        </div>
      </dl>

      {isAuthenticated && (
        <button
          type="button"
          onClick={handleLogout}
          className="hs-noc__masthead-signout"
          aria-label="Sign out"
          title="Sign out"
        >
          Sign out
        </button>
      )}
    </header>
  );
}
