"use client";

import { memo } from "react";

import MetricTile from "@/components/ui/MetricTile";
import type { CloudOperations } from "@/lib/api";
import { resolveKpi } from "@/lib/cloudOperations/format";

interface CloudOperationsPanelProps {
  data?: CloudOperations;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}

const TILES: ReadonlyArray<{ key: keyof CloudOperations; label: string }> = [
  { key: "cloud_resources", label: "CLOUD RESOURCES" },
  { key: "resource_health", label: "RESOURCE HEALTH" },
  { key: "active_incidents", label: "ACTIVE CLOUD INCIDENTS" },
  { key: "cloud_spend", label: "CLOUD SPEND" },
  { key: "policy_compliance", label: "POLICY COMPLIANCE" },
  { key: "backup_readiness", label: "BACKUP READINESS" },
];

/**
 * Cloud Operations - six KPI cards.
 *
 * Reuses MetricTile for its honest loading / error / empty / stale /
 * unavailable states. The three unconfigured integrations (spend, policy,
 * backup) render as "N/A" with their explicit reason, never a fabricated zero.
 */
export default memo(function CloudOperationsPanel({
  data,
  loading,
  error,
  onRetry,
}: CloudOperationsPanelProps) {
  if (loading && !data) {
    return (
      <section aria-label="Cloud Operations KPIs" className="cloud-ops">
        <h2 className="cloud-ops__title">Cloud Operations</h2>
        <div className="cloud-ops__grid">
          {TILES.map((t) => (
            <MetricTile key={t.key} label={t.label} value={null} />
          ))}
        </div>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section aria-label="Cloud Operations KPIs" className="cloud-ops">
        <h2 className="cloud-ops__title">Cloud Operations</h2>
        <div className="cloud-ops__error" role="alert">
          <span>Unable to load cloud operations data.</span>
          <span className="cloud-ops__error-detail">
            {error instanceof Error ? error.message : String(error)}
          </span>
          {onRetry ? (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Cloud Operations KPIs" className="cloud-ops">
      <h2 className="cloud-ops__title">Cloud Operations</h2>
      <div className="cloud-ops__grid">
        {data
          ? TILES.map(({ key, label }) => {
              const kpi = data[key];
              const render = resolveKpi(kpi);
              return (
                <MetricTile
                  key={key}
                  label={label}
                  value={render.value}
                  note={render.note}
                  unavailable={render.unavailable}
                  lastCollectedAt={kpi.last_collected_at ?? null}
                />
              );
            })
          : null}
      </div>
    </section>
  );
});
