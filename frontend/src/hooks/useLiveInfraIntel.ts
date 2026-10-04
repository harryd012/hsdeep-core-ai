"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getResourceDependencies } from "@/lib/dependencyImpact";
import { postCorrelationContext } from "@/lib/infrastructureIntelligence";
import { getTopologyGraph } from "@/lib/topologyApi";
import type { DashboardData } from "@/lib/dashboard/dashboardModels";
import {
  buildActivityRows,
  buildCollection,
  buildCorrelation,
  buildGraphEvidence,
  buildImpact,
  buildPath,
  buildRelationRows,
  buildTelemetry,
  selectFocus,
  type LiveInfraIntel,
} from "@/lib/dashboard/liveInfraIntelModel";

/**
 * useLiveInfraIntel — the data behind the LIVE INFRASTRUCTURE INTELLIGENCE zone.
 *
 * It follows the SAME pattern as `useDashboardData`: one React Query composition
 * on the shared `["dashboard", ...]` namespace, a `refetchInterval` matching the
 * existing infrastructure cadence (60s), and shared cache keys, so there is no
 * independent polling loop and no `setInterval` in any component.
 *
 * REQUEST BUDGET (deliberately small, fixed and non-duplicating):
 *   1. `getTopologyGraph()`          — the canonical graph's own counts/status.
 *   2. `getResourceDependencies()`   — ONE bounded read for the focus resource;
 *                                      supplies the relationship context AND the
 *                                      impact/path. Not one call per node.
 *   3. `postCorrelationContext()`    — the HSI-GNCE correlation read.
 * Everything else (which resource is in focus, telemetry, collection health,
 * recent activity, the recorded root cause) is REUSED from the dashboard data
 * already in the cache — no additional request is made for it.
 */

/** Bounded window for a command-centre load. The service's own ceiling is
 *  6 deep / 1000 nodes; the dashboard deliberately asks for far less. */
const FOCUS_MAX_DEPTH = 2;
const FOCUS_MAX_NODES = 120;

/** Ask HSI-GNCE about the focus plus its nearest impacted resources. */
const MAX_CORRELATION_RESOURCES = 10;

/** Same cadence group as the rest of the dashboard's infrastructure reads. */
const REFRESH_MS = 60_000;

export function useLiveInfraIntel(nocData: DashboardData): LiveInfraIntel {
  // Focus is derived from the dashboard data the page already holds.
  const focus = useMemo(() => selectFocus(nocData), [nocData]);

  const graph = useQuery({
    queryKey: ["dashboard", "infra-intel", "graph"],
    queryFn: () => getTopologyGraph(),
    refetchInterval: REFRESH_MS,
  });

  const dependencies = useQuery({
    queryKey: ["dashboard", "infra-intel", "review", focus?.id ?? "none"],
    queryFn: () =>
      getResourceDependencies(focus!.id, { maxDepth: FOCUS_MAX_DEPTH, maxNodes: FOCUS_MAX_NODES }),
    enabled: Boolean(focus),
    refetchInterval: REFRESH_MS,
  });

  // Resource set for the correlation read: the focus first, then its nearest
  // impacted resources. Bounded and de-duplicated; the key is the joined string
  // so the query is stable across renders.
  const correlationIds = useMemo(() => {
    const ids = [
      focus?.id,
      ...(dependencies.data?.impact ?? [])
        .slice(0, MAX_CORRELATION_RESOURCES - 1)
        .map((hop) => hop.to_resource_id),
    ].filter((value): value is string => Boolean(value));
    return Array.from(new Set(ids));
  }, [focus?.id, dependencies.data]);

  const correlationKey = correlationIds.join(",");

  const correlation = useQuery({
    queryKey: ["dashboard", "infra-intel", "correlation", correlationKey],
    queryFn: () => postCorrelationContext(correlationIds),
    enabled: correlationIds.length > 0,
    refetchInterval: REFRESH_MS,
  });

  return useMemo<LiveInfraIntel>(() => {
    const graphReady = graph.data !== undefined;
    const depsReady = dependencies.data !== undefined;

    const correlationLoading = correlationIds.length > 0 && !correlation.data && correlation.isLoading;

    const correlationModel = buildCorrelation({
      focus,
      loading: correlationLoading,
      failed: Boolean(correlation.error) && !correlation.data,
      context: correlation.data,
    });

    let state: LiveInfraIntel["state"] = "success";
    if ((!graphReady && graph.isLoading) || (focus && !depsReady && dependencies.isLoading)) {
      state = "loading";
    } else if (!graphReady && graph.isError && (!focus || (!depsReady && dependencies.isError))) {
      state = "error";
    } else if (!focus) {
      state = "empty";
    }

    return {
      state,
      focus,
      relations: buildRelationRows(dependencies.data),
      impact: buildImpact(dependencies.data),
      path: buildPath(dependencies.data),
      correlation: correlationModel,
      graph: buildGraphEvidence(graph.data),
      telemetry: buildTelemetry(nocData),
      collection: buildCollection(nocData.attention.evidence),
      activity: buildActivityRows(nocData),
      impactUnavailable: Boolean(focus) && !depsReady,
    };
  }, [
    focus,
    graph.data,
    graph.isLoading,
    graph.isError,
    dependencies.data,
    dependencies.isLoading,
    dependencies.isError,
    correlation.data,
    correlation.error,
    correlation.isLoading,
    correlationIds.length,
    nocData,
  ]);
}
