"use client";

/**
 * SocDashboard — the Security Operations Center composition rendered at
 * /dashboard. Replaces the previous NOC/CommandCenter presentation while
 * reusing the same shell ((dashboard) layout: AuthGuard + Sidebar + main +
 * CopilotBar) and the same React Query cache / APIs.
 *
 * Structure (reference design):
 *   Global top header
 *   Page header (title + summary chips) + SOC tab navigation
 *   6 threat KPI cards
 *   Row: Threat Trend | Top Attack Vectors | MITRE ATT&CK Coverage
 *   Row: Security Events Timeline | Active Incidents | Assets Health
 *   Row: Global Threat Map | Security Workflow + Quick Actions
 */
import "@/app/styles/soc.css";
import { useSocData } from "@/hooks/useSocData";
import SocHeader from "./SocHeader";
import { SocMasthead } from "./SocTabs";
import SocKpiCards from "./SocKpiCards";
import ThreatTrendChart from "./ThreatTrendChart";
import AttackVectorsChart from "./AttackVectorsChart";
import MitreCoverage from "./MitreCoverage";
import SecurityEventsTimeline from "./SecurityEventsTimeline";
import ActiveSecurityIncidents from "./ActiveSecurityIncidents";
import AssetsHealth from "./AssetsHealth";
import GlobalThreatMap from "./GlobalThreatMap";
import SecurityWorkflow from "./SecurityWorkflow";
import QuickActions from "./QuickActions";

export default function SocDashboard() {
  const { data, isLoading, hasError } = useSocData();

  return (
    <div className="soc">
      <SocHeader openAlerts={data.kpis[0].value} />

      {hasError && (
        <div className="soc-error-banner" role="alert">
          One or more backend APIs failed — affected panels show their own error states.
        </div>
      )}

      <SocMasthead
        posture={data.summary.posture}
        activeThreats={data.kpis[0].value}
        openIncidents={data.summary.openIncidents}
        monitoredAssets={data.summary.monitoredAssets}
      />

      {isLoading ? (
        <div className="soc-loading">Loading security operations data…</div>
      ) : (
        <>
          <SocKpiCards kpis={data.kpis} />

          <div className="soc-grid soc-grid--analytics">
            <ThreatTrendChart points={data.trend.points} source={data.trend.source} />
            <AttackVectorsChart rows={data.attackVectors.rows} source={data.attackVectors.source} />
            <MitreCoverage mitre={data.mitre} />
          </div>

          <div className="soc-grid soc-grid--ops">
            <SecurityEventsTimeline events={data.events} state={data.states.events} />
            <ActiveSecurityIncidents incidents={data.incidents} state={data.states.incidents} />
            <AssetsHealth assets={data.assets} />
          </div>

          <div className="soc-grid soc-grid--bottom">
            <GlobalThreatMap
              points={data.threatMap.points}
              arcs={data.threatMap.arcs}
              source={data.threatMap.source}
            />
            <div className="soc-bottom-col">
              <SecurityWorkflow stages={data.workflow} />
              <QuickActions />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
