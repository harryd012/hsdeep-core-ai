"use client";

import { useState } from "react";
import ActiveSecurityIncidents from "./ActiveSecurityIncidents";
import AssetsHealth from "./AssetsHealth";
import SecurityEventsTimeline from "./SecurityEventsTimeline";
import type { SocAssetHealth, SocEvent, SocIncidentRow } from "@/lib/soc/socModels";
import type { SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * SecurityOperationsPanel — tabbed wrapper ("SECURITY OPERATIONS" column of
 * the SOC row) around the EXISTING SecurityEventsTimeline /
 * ActiveSecurityIncidents / AssetsHealth components. Presentational only.
 */

const TABS = ["Security Events", "Active Incidents", "Assets Health"] as const;

export default function SecurityOperationsPanel({
  events,
  eventsState,
  incidents,
  incidentsState,
  assets,
}: {
  events: SocEvent[];
  eventsState: SectionState;
  incidents: SocIncidentRow[];
  incidentsState: SectionState;
  assets: SocAssetHealth[];
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Security Events");

  return (
    <section className="soc-ops-tabs" aria-label="Security Operations">
      <div className="soc-tabs soc-ops-tabs__tabs" role="tablist">
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

      <div className="soc-ops-tabs__body">
        {tab === "Security Events" && <SecurityEventsTimeline events={events} state={eventsState} />}
        {tab === "Active Incidents" && <ActiveSecurityIncidents incidents={incidents} state={incidentsState} />}
        {tab === "Assets Health" && <AssetsHealth assets={assets} />}
      </div>
    </section>
  );
}
