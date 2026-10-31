"use client";

/** MitreCoverage — donut visualization + technique coverage summary. */
import Link from "next/link";
import type { SocMitreCoverage } from "@/lib/soc/socModels";

export default function MitreCoverage({ mitre }: { mitre: SocMitreCoverage & { source: "live" | "unavailable" } }) {
  const R = 54, C = 2 * Math.PI * R;
  const filled = (mitre.percent / 100) * C;
  return (
    <div className="soc-panel soc-mitre">
      <div className="soc-panel__head"><h2>MITRE ATT&amp;CK Coverage</h2></div>
      <div className="soc-mitre__body">
        <svg viewBox="0 0 140 140" className="soc-mitre__donut" role="img" aria-label={`MITRE ATT&CK coverage ${mitre.percent}%`}>
          <circle cx="70" cy="70" r={R} className="soc-mitre__track" />
          <circle
            cx="70" cy="70" r={R}
            className="soc-mitre__fill"
            strokeDasharray={`${filled} ${C - filled}`}
            transform="rotate(-90 70 70)"
          />
          <text x="70" y="66" textAnchor="middle" className="soc-mitre__pct">{mitre.percent}%</text>
          <text x="70" y="84" textAnchor="middle" className="soc-mitre__pct-label">COVERAGE</text>
        </svg>
        <dl className="soc-mitre__stats">
          <div><dt>Identified</dt><dd>{mitre.identified}</dd></div>
          <div><dt>Monitored</dt><dd>{mitre.monitored}</dd></div>
          <div><dt>Total</dt><dd>{mitre.total}</dd></div>
        </dl>
      </div>
      <div className="soc-mitre__foot">
        <span>Technique Coverage</span>
        <Link href="/reports" className="soc-btn-ghost">View ATT&amp;CK Matrix</Link>
      </div>
      {mitre.source === "unavailable" && <small className="soc-demo-note">demo data — MITRE coverage API pending</small>}
    </div>
  );
}
