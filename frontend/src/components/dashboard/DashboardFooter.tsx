import Link from "next/link";
import { API_BASE_URL, getTenantId } from "@/lib/api";

/**
 * DashboardFooter — Zone 5 (Quick Links + System Information), plus the shared
 * `QuickLinkGrid` used by Zone 3 (Infrastructure Operations).
 *
 * P0-4B contract:
 *   - Every href below is a route that ALREADY exists in this application
 *     (the same routes the existing Sidebar renders). No URLs are invented.
 *   - No Documentation/Support links are rendered because the existing app
 *     does not define any.
 *   - System Information renders only real, existing configuration values
 *     (product identity, tenant id, API endpoint from lib/api) and the factual
 *     auto-refresh cadence of the existing panels. Nothing is fabricated and
 *     no operational value (counts, health, status) is asserted here.
 *   - This component performs NO fetching.
 */

export interface QuickLink {
  href: string;
  label: string;
  hint?: string;
}

/** Infrastructure-scoped views (Zone 3) — all existing routes. */
export const INFRASTRUCTURE_LINKS: readonly QuickLink[] = [
  { href: "/infrastructure", label: "Infrastructure", hint: "Sites · devices · topology" },
  { href: "/infrastructure/assets", label: "Asset Inventory", hint: "Device records" },
  { href: "/monitoring", label: "Monitoring", hint: "Sources · sensors · collectors" },
  { href: "/alerts", label: "Alerts", hint: "Open alert queue" },
  { href: "/incidents", label: "Incidents", hint: "Incident lifecycle" },
  { href: "/automation", label: "Automation", hint: "Collector runs" },
];

/** Global quick links (Zone 5) — mirrors the existing sidebar navigation. */
export const QUICK_LINKS: readonly QuickLink[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/infrastructure", label: "Infrastructure" },
  { href: "/monitoring", label: "Monitoring" },
  { href: "/alerts", label: "Alerts" },
  { href: "/incidents", label: "Incidents" },
  { href: "/tickets", label: "Tickets" },
  { href: "/ai-copilot", label: "AI Copilot" },
  { href: "/automation", label: "Automation" },
  { href: "/sop-engine", label: "SOP Engine" },
  { href: "/reports", label: "Reports" },
  { href: "/subscription", label: "Subscription" },
  { href: "/settings", label: "Settings" },
];

/**
 * Minimal, reusable link grid. Presentation only — it renders exactly the
 * links it is given and never derives labels or targets from data.
 */
export function QuickLinkGrid({
  links,
  title,
}: {
  links: readonly QuickLink[];
  title?: string;
}) {
  return (
    <div className="hs-noc__links">
      {title ? <h3>{title}</h3> : null}
      <ul>
        {links.map((link) => (
          <li key={link.label}>
            <Link href={link.href}>
              <span>{link.label}</span>
              {link.hint ? <small>{link.hint}</small> : null}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function DashboardFooter() {
  const tenantId = getTenantId();

  return (
    <footer className="hs-noc__footer">
      <div className="hs-noc__footer-col">
        <QuickLinkGrid links={QUICK_LINKS} title="QUICK LINKS" />
      </div>

      <div className="hs-noc__footer-col">
        <h3>SYSTEM INFORMATION</h3>
        <dl className="hs-noc__sysinfo">
          <div>
            <dt>PLATFORM</dt>
            <dd>HSDEEP CORE AI</dd>
          </div>
          <div>
            <dt>TENANT</dt>
            <dd title={tenantId}>{tenantId}</dd>
          </div>
          <div>
            <dt>API ENDPOINT</dt>
            <dd title={API_BASE_URL}>{API_BASE_URL}</dd>
          </div>
          <div>
            <dt>AUTO REFRESH</dt>
            <dd>5–60s per panel</dd>
          </div>
        </dl>
      </div>
    </footer>
  );
}
