"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getAlertSummary,
  getAutomationStatus,
  getBackendHealth,
  getCloudStatus,
  getCollectorRuns,
  getDashboardSummary,
  getDevices,
  getEvents,
  getMe,
  getNetworkStatus,
  getAIInsights,
  listAlerts,
  listSensors,
  API_BASE_URL,
  getTenantId,
} from "@/lib/api";
import { useCommandCenterOverview } from "@/hooks/useAiQueries";
import {
  buildActivity,
  buildAiOperations,
  buildAlertSummaryData,
  buildHeader,
  buildInfrastructureOverview,
  buildOperationalHealth,
  buildTrends,
} from "@/lib/dashboard/dashboardDataAdapter";
import type { DashboardData, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * useDashboardData — SINGLE SOURCE OF TRUTH for the /dashboard page.
 *
 *   existing APIs (lib/api.ts) + existing AI hook (useAiQueries)
 *        ↓  ONE React Query composition (this file, shared cache keys)
 *   dashboardDataAdapter (pure transforms) → semantic models
 *        ↓  presentational zone components
 *
 * Every widget consumes the semantic models produced here, so no component
 * performs its own fetch and no value can appear twice with different
 * numbers. Cadences mirror the pre-existing panels: AI 5s (existing hook),
 * alerts 15s, infra/events/trends/activity 60s, me on mount.
 */

function state(loading: boolean, error: unknown, hasData: boolean): SectionState {
  if (loading) return "loading";
  if (error) return "error";
  return hasData ? "success" : "empty";
}

export function useDashboardData(): DashboardData {
  const aiOverview = useCommandCenterOverview(); // existing P0-5 hook (5s)

  const alerts = useQuery({ queryKey: ["dashboard", "alerts-open"], queryFn: () => listAlerts({ status: "open", limit: 100 }), refetchInterval: 15_000 });
  const alertSummary = useQuery({ queryKey: ["dashboard", "alert-summary"], queryFn: () => getAlertSummary(), refetchInterval: 15_000 });
  const devices = useQuery({ queryKey: ["dashboard", "devices"], queryFn: () => getDevices(), refetchInterval: 60_000 });
  const sensors = useQuery({ queryKey: ["dashboard", "sensors"], queryFn: () => listSensors(), refetchInterval: 60_000 });
  const automation = useQuery({ queryKey: ["dashboard", "automation-status"], queryFn: () => getAutomationStatus(), refetchInterval: 60_000 });
  const network = useQuery({ queryKey: ["dashboard", "network-status"], queryFn: () => getNetworkStatus(), refetchInterval: 60_000 });
  const cloud = useQuery({ queryKey: ["dashboard", "cloud-status"], queryFn: () => getCloudStatus(), refetchInterval: 60_000 });
  const apiUp = useQuery({ queryKey: ["dashboard", "backend-health"], queryFn: () => getBackendHealth(), refetchInterval: 60_000 });
  const summary = useQuery({ queryKey: ["dashboard", "summary"], queryFn: () => getDashboardSummary(), refetchInterval: 60_000 });
  const events = useQuery({ queryKey: ["dashboard", "events"], queryFn: () => getEvents({ limit: 100 }), refetchInterval: 60_000 });
  const runs = useQuery({ queryKey: ["dashboard", "collector-runs"], queryFn: () => getCollectorRuns({ limit: 20 }), refetchInterval: 60_000 });
  const me = useQuery({ queryKey: ["dashboard", "me"], queryFn: () => getMe(), staleTime: 5 * 60_000 });
  const aiInsights = useQuery({ queryKey: ["dashboard", "ai-insights"], queryFn: () => getAIInsights(), refetchInterval: 60_000 });

  const { data: alertsData } = alerts;
  const { data: devicesData } = devices;
  const { data: sensorsData } = sensors;
  const { data: automationData } = automation;
  const { data: eventsData } = events;
  const { data: runsData } = runs;

  const operationalHealth = buildOperationalHealth({
    alerts: alertsData, summary: alertSummary.data, devices: devicesData,
    sensors: sensorsData, automation: automationData, network: network.data,
  });
  const infrastructure = buildInfrastructureOverview({
    devices: devicesData, sensors: sensorsData, automation: automationData,
    network: network.data, cloud: cloud.data, apiUp: apiUp.data,
    dashboardSummary: summary.data, events: eventsData,
  });
  const trends = buildTrends(eventsData, runsData);
  const activity = buildActivity(eventsData, runsData, devicesData);
  const aiOperations = buildAiOperations(aiOverview.data, aiInsights.data);
  const alertSummaryData = buildAlertSummaryData(alertsData, alertSummary.data);

  const infraLoading = devices.isLoading || sensors.isLoading || automation.isLoading || summary.isLoading;
  const infraError = devices.error || sensors.error || automation.error || summary.error;

  return {
    header: buildHeader({ tenantId: getTenantId(), user: me.data, apiHost: API_BASE_URL }),
    operationalHealth,
    alertSummary: alertSummaryData,
    aiOperations,
    infrastructure,
    trends,
    activity,
    states: {
      operationalHealth: state(alerts.isLoading, alerts.error && devices.error, kpisWithValues(operationalHealth) > 0),
      alertSummary: state(alerts.isLoading, alerts.error, alertSummaryData.total > 0),
      aiOperations: state(aiOverview.isLoading, aiOverview.error, aiOverview.data !== undefined),
      infrastructure: state(infraLoading, infraError, (devicesData?.length ?? 0) > 0 || infrastructure.summary !== null),
      trends: state(events.isLoading, events.error, trends.some((t) => t.data.length > 0)),
      activity: state(events.isLoading, events.error, activity.length > 0),
    },
  };
}

/** How many KPIs currently have a real (non-null) backend value. */
function kpisWithValues(health: DashboardData["operationalHealth"]): number {
  return Object.values(health).filter((kpi) => kpi.value !== null).length;
}
