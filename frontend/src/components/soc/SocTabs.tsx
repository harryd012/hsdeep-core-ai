"use client";

/** SocMasthead — SOC page header: title, subtitle, summary cards, tabs. */
import Link from "next/link";
import { ShieldCheck, Activity, Siren, Server, MoreHorizontal } from "lucide-react";

export const SOC_TABS = [
  "Overview", "Threats", "Incidents", "Attack Surface", "Firewall & SASE",
  "Identities", "Endpoints", "Network", "Cloud", "Vulnerabilities",
  "Logs & Analytics", "Reports",
] as const;

export function SocMasthead({
  posture,
  activeThreats,
  openIncidents,
  monitoredAssets,
}: {
  /** null = the counters it is derived from were unavailable. */
  posture: string | null;
  activeThreats: number | null;
  openIncidents: number | null;
  monitoredAssets: number | null;
}) {
  return (
    <div className="soc-masthead">
      <div className="soc-masthead__title-row">
        <div className="soc-masthead__title">
          <span className="soc-masthead__icon"><ShieldCheck size={22} /></span>
          <div>
            <h1>Security Operations Center</h1>
            <p>Detect. Investigate. Respond. Protect.</p>
          </div>
        </div>
        <div className="soc-masthead__summary">
          <div className="soc-summary-chip">
            <small>SECURITY POSTURE</small>
            {/* No tone class when the posture is unavailable - a neutral colour
                must not read as "Fair". */}
            <b
              className={
                posture === "Good" ? "is-good"
                  : posture === "Critical" ? "is-crit"
                    : posture === "Fair" ? "is-warn"
                      : undefined
              }
            >
              <Activity size={13} /> {posture ?? "—"}
            </b>
          </div>
          <div className="soc-summary-chip">
            <small>ACTIVE THREATS</small>
            <b className="is-crit"><Siren size={13} /> {activeThreats ?? "—"}</b>
          </div>
          <div className="soc-summary-chip">
            <small>OPEN INCIDENTS</small>
            <b className="is-warn">{openIncidents ?? "—"}</b>
          </div>
          <div className="soc-summary-chip">
            <small>MONITORED ASSETS</small>
            <b className="is-info"><Server size={13} /> {monitoredAssets?.toLocaleString() ?? "—"}</b>
          </div>
          <Link href="/reports" className="soc-summary-more" aria-label="More summary options">
            <MoreHorizontal size={16} /> More
          </Link>
        </div>
      </div>

      <nav className="soc-tabs" aria-label="SOC sections">
        {SOC_TABS.map((t, i) => (
          <button key={t} type="button" className={i === 0 ? "active" : ""} aria-current={i === 0 ? "page" : undefined}>
            {t}
          </button>
        ))}
      </nav>
    </div>
  );
}
