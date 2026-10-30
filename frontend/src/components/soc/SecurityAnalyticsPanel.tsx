"use client";

import { useState } from "react";
import AttackVectorsChart from "./AttackVectorsChart";
import MitreCoverage from "./MitreCoverage";
import ThreatTrendChart from "./ThreatTrendChart";
import type {
  SocAttackVector,
  SocMitreCoverage,
  SocTrendPoint,
} from "@/lib/soc/socModels";

/**
 * SecurityAnalyticsPanel — tabbed wrapper ("SECURITY ANALYTICS" column of the
 * SOC row) around the EXISTING chart components. Purely presentational: it
 * owns only the active-tab index and reuses ThreatTrendChart /
 * AttackVectorsChart / MitreCoverage unchanged, so the live-vs-fallback
 * labeling inside those components is preserved.
 */

const TABS = ["Threat Trend", "Top Attack Vectors", "MITRE ATT&CK"] as const;

export default function SecurityAnalyticsPanel({
  trend,
  attackVectors,
  mitre,
}: {
  trend: { points: SocTrendPoint[]; source: "live" | "unavailable" };
  attackVectors: { rows: SocAttackVector[]; source: "live" | "unavailable" };
  mitre: SocMitreCoverage & { source: "live" | "unavailable" };
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Threat Trend");

  return (
    <section className="soc-analytics-tabs" aria-label="Security Analytics">
      <div className="soc-tabs soc-analytics-tabs__tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="soc-analytics-tabs__body">
        {tab === "Threat Trend" && <ThreatTrendChart points={trend.points} source={trend.source} />}
        {tab === "Top Attack Vectors" && <AttackVectorsChart rows={attackVectors.rows} source={attackVectors.source} />}
        {tab === "MITRE ATT&CK" && <MitreCoverage mitre={mitre} />}
      </div>
    </section>
  );
}
