"use client";

import React from "react";

import type { DashboardData, SectionState } from "@/lib/dashboard/dashboardModels";

import type { SocData } from "@/lib/soc/socModels";

import DashboardMasthead from "@/components/dashboard/DashboardMasthead";

import DeploymentReadinessPanel from "@/components/dashboard/DeploymentReadinessPanel";

import OperationalHealth from "@/components/dashboard/OperationalHealth";

import AlertSummaryPanel from "@/components/dashboard/AlertSummaryPanel";

import AiOperations from "@/components/dashboard/AiOperations";

import CommandCenterTopology from "@/components/dashboard/CommandCenterTopology";

import InfrastructureOverview from "@/components/dashboard/InfrastructureOverview";

import AssetsSummary from "@/components/dashboard/AssetsSummary";
import NetworkIntelligenceWidget from "@/components/network-intelligence/NetworkIntelligenceWidget";

import AttentionRequired from "@/components/dashboard/AttentionRequired";

import LiveInfrastructureIntelligence from "@/components/dashboard/LiveInfrastructureIntelligence";

import SocKpiCards from "@/components/soc/SocKpiCards";

import SecurityOperationsPanel from "@/components/soc/SecurityOperationsPanel";

/**
 * UnifiedCommandCenter - the /dashboard presentation layer, ordered so an
 * operator sees what needs them before anything else, then progressively less
 * urgent (and less certain) information further down the page.
 *
 * PHASE 2 level order (matches the captions rendered by AttentionRequired):
 *   L1-3  ATTENTION REQUIRED - attention, impact and health counters + the
 *         incidents/devices behind them + the backend's own evidence aggregates
 *   L2-3  LIVE INFRASTRUCTURE INTELLIGENCE - the infrastructure involved, what
 *         it affects and the evidence for it, between the counters and the
 *         estate KPIs (existing graph + dependency/impact + correlation reads)
 *   L3    OPERATIONAL HEALTH (NOC) - KPI strip + estate totals
 *   L3    ALERT SUMMARY + SECURITY OVERVIEW - the alert situation and SOC counters
 *   L6    INFRASTRUCTURE OPERATIONS - overview tabs + Network Intelligence
 *   L6    SECURITY OPERATIONS - real events / incidents / asset health
 *   L7    AI OPERATIONS / AI CTO - AI panel + Command Center topology, LAST so
 *         assistance never competes with a measured signal
 *
 * Every panel is an EXISTING component fed by useDashboardData / useSocData.
 * This file owns no data, no fetching and no mock values, and PHASE 2 only moved
 * these sections and added level captions - no panel was rewritten.
 *
 * Removed from this page on purpose (density + truth, see AttentionRequired):
 *   - threat trend / attack vectors / MITRE coverage panels and the global threat
 *     map, which render labelled demo series because no backend endpoint exists;
 *   - the static security workflow diagram and the SOC quick-action buttons;
 *   - the footer's Quick Links (they duplicate the sidebar) and System
 *     Information (platform/tenant/API metadata) blocks.
 */
export default function UnifiedCommandCenter({
  nocData,
  socData,
}: {
  nocData: DashboardData;
  socData: SocData | undefined;
}) {
  const socReady = !!socData;

  const socEventsState: SectionState =
    !socData || socData.states?.events === "loading" ? "loading" :
    !socData.events || socData.events.length === 0 ? "empty" :
    socData.states?.events === "error" ? "error" : "success";

  const socIncidentsState: SectionState =
    !socData || socData.states?.incidents === "loading" ? "loading" :
    !socData.incidents || socData.incidents.length === 0 ? "empty" :
    socData.states?.incidents === "error" ? "error" : "success";

  return (
    <>
      <DashboardMasthead
        header={{
          tenantId: nocData.header.tenantId,
          user: nocData.header.user,
          apiHost: nocData.header.apiHost,
        }}
        apiHealthy={nocData.apiHealthy}
        apiHealth={nocData.apiHealth}
      />

      <div className="hs-dashboard">
        {/** DEPLOYMENT READINESS (PHASE 2): local helm baseline, chart
            validity and configured prerequisites. A local helm PASS
            never means the application is deployed or production ready. */}
        <DeploymentReadinessPanel apiHealth={nocData.apiHealth} />

        {/* LEVEL 1-3 - the three questions, answered before anything else. */}
        <AttentionRequired
          data={nocData.attention}
          kpis={nocData.commandKpis}
          state={nocData.states.attention}
        />

        {/* LIVE INFRASTRUCTURE INTELLIGENCE - the layer between the attention
            counters and the estate KPI grid. It answers "what is happening,
            what is involved, what is affected, and why believe it" by reusing
            the existing topology graph, dependency/impact and HSI-GNCE
            correlation reads. It adds no new system and fabricates nothing. */}
        <LiveInfrastructureIntelligence nocData={nocData} />

        <nav className="dashboard-section-nav" aria-label="Dashboard sections">
          <a href="#readiness">Deployment Readiness</a>
          <a href="#attention">Attention</a>
          <a href="#infrastructure-intelligence">Infrastructure Intel</a>
          <a href="#operational-health">NOC Overview</a>
          {socReady && <a href="#security-overview">SOC Overview</a>}
          <a href="#infrastructure">Infrastructure</a>
          {socReady && <a href="#security-operations">Security Operations</a>}
          <a href="#ai-operations">AI Operations</a>
        </nav>

        {/* LEVEL 3 - HEALTH: the NOC KPI grid + estate totals */}
        <section id="operational-health" className="hs-zone hs-zone--health" aria-labelledby="operational-health-title">
          <div className="hs-zone__header">
            <h2 id="operational-health-title" className="hs-zone__title">Operational Health (NOC)</h2>
            <p className="hs-zone__subtitle">Real-time infrastructure KPIs and estate totals</p>
          </div>

          <div className="unified-health-row">
            <div className="unified-health-row__kpi">
              <OperationalHealth data={nocData.operationalHealth} state={nocData.states.operationalHealth} />
            </div>
            <div className="unified-health-row__assets">
              <AssetsSummary
                summary={nocData.infrastructure.summary}
                devices={nocData.infrastructure.devices}
                state={nocData.states.infrastructure}
              />
            </div>
          </div>
        </section>

        {/* ROW 2 - combined band: ALERT SUMMARY (left) + SECURITY OVERVIEW (right) */}
        <section id="security-overview" className="hs-zone hs-zone--alert-soc" aria-labelledby="soc-overview-title">
          <div className="alert-soc-row">
            <div className="alert-soc-row__summary">
              <AlertSummaryPanel data={nocData.alertSummary} state={nocData.states.alertSummary} />
            </div>

            {socReady && socData && (
              <div className="alert-soc-row__soc">
                <div className="hs-zone__header">
                  <h2 id="soc-overview-title" className="hs-zone__title">Security Overview (SOC)</h2>
                  <p className="hs-zone__subtitle">Threat KPIs across the estate</p>
                </div>
                <SocKpiCards kpis={socData.kpis} />
              </div>
            )}
          </div>
        </section>

        {/* LEVEL 6 - DETAIL: infrastructure operations + assets summary */}
        <section id="infrastructure" className="hs-zone hs-zone--infra-soc" aria-labelledby="infra-zone-title">
          <div className="hs-zone__header">
            <h2 id="infra-zone-title" className="hs-zone__title">Infrastructure Operations</h2>
            <p className="hs-zone__subtitle">Devices, sensors, collectors, network, and cloud status</p>
          </div>

          {/* Compact Network Intelligence summary (SD-WAN + SASE). Reads the last
              REAL evaluation; performs no evaluation of its own. */}
          <div className="unified-infra-row__ni">
            <NetworkIntelligenceWidget />
          </div>

          {/* AssetsSummary was moved to Row 1 (unified-health-row__assets) so the
              Operational Health zone uses its right-side horizontal space. This row
              is now a single full-width column for the tabbed infrastructure view. */}
          <div className="unified-infra-row">
            <div className="unified-infra-row__overview">
              <InfrastructureOverview data={nocData.infrastructure} state={nocData.states.infrastructure} />
            </div>
          </div>
        </section>

        {/* ROW 6 - SECURITY OPERATIONS (real events / incidents / asset health).
            The demo-series analytics panels, the placeholder threat map, the static
            workflow diagram and the SOC quick actions were removed: they either had
            no backend source or duplicated the queues linked from Attention. */}
        {socReady && socData && (
          <section id="security-operations" className="hs-zone hs-zone--soc-row" aria-labelledby="soc-zone-title">
            <div className="hs-zone__header">
              <h2 id="soc-zone-title" className="hs-zone__title">Security Operations</h2>
              <p className="hs-zone__subtitle">Security events, active incidents, and asset health from live APIs</p>
            </div>

            <div className="unified-soc-row">
              <SecurityOperationsPanel
                events={socData.events}
                eventsState={socEventsState}
                incidents={socData.incidents}
                incidentsState={socIncidentsState}
                assets={socData.assets}
              />
            </div>
          </section>
        )}

        {/* LEVEL 7 - AI: AI CTO recommendations, copilot and live topology.
            Deliberately LAST (PHASE 2): AI assistance must not compete with the
            measured attention/impact/health signals above it. */}
        <section id="ai-operations" className="hs-zone hs-zone--ai" aria-labelledby="ai-zone-title">
          <div className="hs-zone__header">
            <h2 id="ai-zone-title" className="hs-zone__title">AI Operations / AI CTO</h2>
            <p className="hs-zone__subtitle">AI CTO recommendations, copilot, and live topology</p>
          </div>

          <div className="unified-ai-row">
            <div className="unified-ai-row__ai">
              <AiOperations data={nocData.aiOperations} state={nocData.states.aiOperations} />
            </div>
            <div className="unified-ai-row__topology">
              <CommandCenterTopology />
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
