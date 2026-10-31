/**
 * operationalIntelligenceModel.ts — PHASE 6A semantic model.
 *
 * Data comes from ONE backend aggregate
 * (GET /api/dashboard/operational-intelligence, via `getOperationalIntelligence`
 * in `@/lib/api`). This module is a PURE view-model: it turns the payload
 * into title/value/context cards using the project's existing CommandKpi
 * shape, so the existing card renderer can be reused unchanged.
 *
 * TRUTH RULES (mirror the backend's `_pct` rule):
 *   pct === null  -> the denominator did not exist (no runs / no sources /
 *                    no timestamps) -> render N/A, never 0% or 100%.
 *                    A measured 0 stays a measured 0.
 */
import type { CommandKpi } from "./commandKpis";
import type { OperationalIntelligence } from "@/lib/api";

/** Re-exported so zone components import model + builder from one module. */
export type { OperationalIntelligence };

/**
 * KPI N (AI automation rate) is deliberately ABSENT from this model: no
 * authoritative definition of an "automatable eligible task" exists, so no
 * fabricated ratio is carried.
 */

/* ---------------------------------------------------------------------------
 * View-model helpers + builder.
 * ------------------------------------------------------------------------- */

/** Percentage without a denominator is null, not 0% and not 100%. */
function rateState(pct: number | null): CommandKpi["state"] {
  if (pct === null) return "unavailable";
  if (pct >= 90) return "healthy";
  return pct >= 70 ? "degraded" : "critical";
}

/** A "count of problems": a measured 0 means nothing needs attention. */
function problemState(value: number): CommandKpi["state"] {
  return value === 0 ? "healthy" : "critical";
}


/**
 * Turn the backend snapshot into the card view model the dashboard renders.
 * Every card carries its own definition in `context`, so a value cannot be
 * misread. Missing sections stay null (the card renders N/A); a measured 0
 * stays 0.
 */
export function buildOperationalIntelligenceKpis(
  oi: OperationalIntelligence | undefined,
): CommandKpi[] {
  const { sources, collection, freshness, sensors } =
    oi ?? ({} as Partial<OperationalIntelligence>);
  const devices = oi?.devices;
  const alerts = oi?.alerts;
  const ai = oi?.ai;

  return [
    {
      key: "oi-sources",
      label: "MONITORING SOURCES",
      value: sources ? sources.active : null,
      unit: null,
      state:
        sources === undefined
          ? "unavailable"
          : sources.total === 0
            ? "unknown"
            : sources.active === sources.total
              ? "healthy"
              : "degraded",
      context:
        sources === undefined
          ? "source API did not respond"
          : sources.total === 0
            ? "no sources configured"
            : `${sources.active} of ${sources.total} active`,
      href: "/monitoring",
    },
    {
      key: "oi-collection-health",
      label: "COLLECTION HEALTH",
      value: collection?.health_percent ?? null,
      unit: collection?.health_percent == null ? null : "%",
      state:
        collection === undefined
          ? "unavailable"
          : collection.runs_24h === 0
            ? "unknown"
            : rateState(collection.health_percent),
      context:
        collection === undefined
          ? "collector API did not respond"
          : collection.runs_24h === 0
            ? `no collector runs in the last ${collection.window_hours}h`
            : `${collection.succeeded_24h + collection.partial_24h} of ${collection.runs_24h} succeeded · ${collection.window_hours}h`,
      href: "/monitoring",
    },
    {
      key: "oi-coverage",
      label: "COLLECTION COVERAGE",
      value: collection?.coverage_percent ?? null,
      unit: collection?.coverage_percent == null ? null : "%",
      state:
        collection === undefined
          ? "unavailable"
          : collection.coverage_configured === 0
            ? "unknown"
            : rateState(collection.coverage_percent),
      context:
        collection === undefined
          ? "collector API did not respond"
          : collection.coverage_configured === 0
            ? "no enabled sources to cover"
            : `${collection.coverage_collected} of ${collection.coverage_configured} enabled sources collected`,
      href: "/monitoring",
    },
    {
      key: "oi-freshness",
      label: "DATA FRESHNESS",
      value: freshness?.fresh_percent ?? null,
      unit: freshness?.fresh_percent == null ? null : "%",
      state:
        freshness === undefined
          ? "unavailable"
          : freshness.total === 0
            ? "unknown"
            : rateState(freshness.fresh_percent),
      context:
        freshness === undefined
          ? "sensor API did not respond"
          : freshness.total === 0
            ? "no sensors to judge"
            : `${freshness.fresh} of ${freshness.total} fresh · stale after ${Math.round(freshness.stale_after_seconds / 60)}m`,
      href: "/monitoring",
    },
    {
      key: "oi-sensors-down",
      label: "SENSORS DOWN",
      value: sensors?.down ?? null,
      unit: null,
      state:
        sensors === undefined
          ? "unavailable"
          : sensors.total === 0
            ? "unknown"
            : problemState(sensors.down),
      context:
        sensors === undefined
          ? "sensor API did not respond"
          : sensors.total === 0
            ? "no sensors registered"
            : `of ${sensors.total} sensors${sensors.unknown > 0 ? ` · ${sensors.unknown} unknown` : ""}`,
      href: "/monitoring",
    },
    {
      key: "oi-sensors-warning",
      label: "SENSORS WARNING",
      value: sensors?.warning ?? null,
      unit: null,
      state:
        sensors === undefined
          ? "unavailable"
          : sensors.total === 0
            ? "unknown"
            : sensors.warning === 0
              ? "healthy"
              : "degraded",
      context:
        sensors === undefined
          ? "sensor API did not respond"
          : sensors.total === 0
            ? "no sensors registered"
            : `${sensors.healthy} healthy of ${sensors.total}`,
      href: "/monitoring",
    },
    {
      key: "oi-devices",
      label: "DEVICES ONLINE",
      value: devices?.canonical?.up ?? null,
      unit: null,
      state:
        devices === undefined
          ? "unavailable"
          : devices.total === 0
            ? "unknown"
            : (devices.canonical?.down ?? 0) === 0
              ? "healthy"
              : "critical",
      context:
        devices === undefined
          ? "device API did not respond"
          : devices.total === 0
            ? "no devices registered"
            : `of ${devices.total} · ${devices.canonical?.down ?? 0} down · ${devices.canonical?.stale ?? 0} stale · ${devices.canonical?.unknown ?? 0} unknown`,
      href: "/infrastructure",
    },
    {
      key: "oi-runs-24h",
      label: "COLLECTOR RUNS 24H",
      value: collection?.runs_24h ?? null,
      unit: null,
      state:
        collection === undefined
          ? "unavailable"
          : collection.runs_24h === 0
            ? "unknown"
            : collection.failed_24h === 0
              ? "healthy"
              : "degraded",
      context:
        collection === undefined
          ? "collector API did not respond"
          : collection.runs_24h === 0
            ? `no runs in the last ${collection.window_hours}h`
            : `${collection.failed_24h} failed of ${collection.runs_24h}`,
      href: "/monitoring",
    },
    {
      key: "oi-alerts-24h",
      label: "ALERTS LAST 24H",
      value: alerts?.last_24h ?? null,
      unit: null,
      state:
        alerts === undefined
          ? "unavailable"
          : alerts.last_24h === 0
            ? "healthy"
            : "degraded",
      context:
        alerts === undefined
          ? "alert API did not respond"
          : `${alerts.unacknowledged} unacknowledged · ${alerts.auto_resolved_24h} auto-resolved`,
      href: "/alerts",
    },
    {
      key: "oi-ai-tasks",
      label: "AI TASKS",
      value: ai == null ? null : ai.queued + ai.running,
      unit: null,
      state:
        ai === undefined
          ? "unavailable"
          : ai.failed > 0
            ? "degraded"
            : "healthy",
      context:
        ai === undefined
          ? "AI metrics did not respond"
          : `${ai.queued} queued · ${ai.running} running · ${ai.completed} completed`,
      href: "/ai-copilot",
    },
  ];
}
