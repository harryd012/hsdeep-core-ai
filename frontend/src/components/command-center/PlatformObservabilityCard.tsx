"use client";

/**
 * Platform Observability card for the Executive Command Center.
 *
 * Brings the platform's own health and trends (the Prometheus/Grafana data)
 * onto the main dashboard: overall health state, the services that are not
 * healthy, and four trend charts. It reads the same backend endpoints as the
 * Platform Observability page, so the two always agree.
 *
 * Platform internals are superuser-only on the backend, so the card renders
 * nothing for anyone else: customer-facing users keep a customer-only
 * dashboard. When Prometheus is down the charts fall back to the backend
 * process's own short history and the card says so.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import SeriesChart from "@/components/observability/SeriesChart";
import { Pill } from "@/components/ui/PageKit";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  HEALTH_TONE,
  PLATFORM_HEALTH_PATH,
  SERIES_THRESHOLDS,
  TIMESERIES_PATH,
  formatUnit,
  healthCounts,
  latestPoint,
  timeseriesSourceNote,
  type PlatformHealth,
  type Timeseries,
} from "@/lib/observability/platformMetrics";
import { CardShell, type CardState } from "./primitives";

const POLL_MS = 60_000;
/** The four trends an operator checks first, in reading order. */
const CHARTS = [
  { key: "http_request_rate", line: "total" },
  { key: "http_latency", line: "p95" },
  { key: "http_error_ratio", line: "total" },
  { key: "collection_success_ratio", line: "total" },
] as const;

export default function PlatformObservabilityCard() {
  const { user } = useAuth();
  const allowed = Boolean(user?.is_superuser);

  const health = useQuery({
    queryKey: ["dashboard", "platform-health"],
    queryFn: () => apiFetch<PlatformHealth>(PLATFORM_HEALTH_PATH),
    refetchInterval: POLL_MS,
    retry: false,
    enabled: allowed,
  });
  const trends = useQuery({
    queryKey: ["dashboard", "platform-timeseries"],
    queryFn: () => apiFetch<Timeseries>(`${TIMESERIES_PATH}?range=1h`),
    refetchInterval: POLL_MS,
    retry: false,
    enabled: allowed,
  });

  if (!allowed) return null;

  const state: CardState =
    health.isLoading && trends.isLoading ? "loading" : health.error && trends.error ? "error" : "ready";
  const h = health.data;
  const t = trends.data;
  const unhealthy = h ? h.services.filter((s) => s.status !== "HEALTHY") : [];

  return (
    <CardShell
      title="Platform Observability"
      info="Health and trends of the HSDEEP platform itself (API, workers, scheduler, database), from Prometheus. Visible to platform administrators only."
      aside={
        <Link className="ecc-link" href="/observability">
          Open observability →
        </Link>
      }
      state={state}
      onRetry={() => {
        void health.refetch();
        void trends.refetch();
      }}
      skeletonHeight={260}
      className="ecc-span-full ecc-platform"
    >
      <div className="ecc-platform__health" role="status">
        {h ? (
          <>
            <span className="ecc-platform__overall">
              <Pill tone={HEALTH_TONE[h.overall]}>{h.overall}</Pill>
              <span className="ecc-platform__muted">overall</span>
            </span>
            {healthCounts(h.services)
              .filter((c) => c.count > 0)
              .map((c) => (
                <span key={c.status} className="ecc-platform__count">
                  <strong>{c.count}</strong> {c.status.toLowerCase()}
                </span>
              ))}
          </>
        ) : (
          <span className="ecc-platform__muted">
            {health.error ? "NOT AVAILABLE: platform health could not be read" : "Reading platform health…"}
          </span>
        )}
      </div>

      {unhealthy.length > 0 && (
        <ul className="ecc-platform__services">
          {unhealthy.slice(0, 6).map((s) => (
            <li key={s.key} title={s.reason}>
              <Pill tone={HEALTH_TONE[s.status]}>{s.status}</Pill>
              <span className="ecc-platform__name">{s.name}</span>
              <span className="ecc-platform__muted ecc-platform__reason">{s.reason}</span>
            </li>
          ))}
          {unhealthy.length > 6 && (
            <li className="ecc-platform__muted">and {unhealthy.length - 6} more on the observability page</li>
          )}
        </ul>
      )}

      {t ? (
        <>
          <div className="ecc-platform__charts">
            {CHARTS.map(({ key, line }) => {
              const series = t.series.find((s) => s.key === key);
              if (!series) return null;
              const latest = latestPoint(series, line);
              return (
                <figure key={key} className="ecc-platform__chart">
                  <figcaption>
                    <span>{series.label}{line !== "total" ? ` (${line})` : ""}</span>
                    <strong>{latest === null ? "Not measured" : formatUnit(series.unit, latest)}</strong>
                  </figcaption>
                  <SeriesChart
                    lines={series.lines}
                    label={series.label}
                    height={120}
                    stepSeconds={t.step_seconds}
                    format={(v) => formatUnit(series.unit, v)}
                    thresholds={SERIES_THRESHOLDS[key]}
                  />
                </figure>
              );
            })}
          </div>
          <p className="ecc-platform__foot">{timeseriesSourceNote(t)} · last hour</p>
        </>
      ) : (
        <p className="ecc-platform__foot">
          {trends.error ? "NOT AVAILABLE: platform trends could not be read" : "Reading platform trends…"}
        </p>
      )}
    </CardShell>
  );
}
