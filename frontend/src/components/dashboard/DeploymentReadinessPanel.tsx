/**
 * DeploymentReadinessPanel - the Deployment Readiness section of the
 * enterprise dashboard (PHASE 2).
 *
 * "Deployment readiness" means what the release engineer has manually
 * verified about the deployment pipeline (local Helm version, helm lint,
 * helm template rendering, chart advisory) versus the checked but
 * still-unobserved estate prerequisites (Kubernetes connectivity,
 * Kubernetes authentication, application deployment, live pod health,
 * production configuration validation).
 *
 * Data flow:
 *   - Evidence records from deploymentReadinessData.ts (MANUAL
 *     provenance, real results, no fabricated timestamps).
 *   - Overall state from buildReadinessSummary() (pure adapter).
 *   - Application health (apiHealth) is passed in from the EXISTING
 *     UnifiedCommandCenter feed (useDashboardData / GET /health) - reused,
 *     never re-fetched here.
 *
 * Rules:
 *   - Unknown Kubernetes / deployment state is NEVER rendered as PASS.
 *   - Overall state is derived, never authored.
 *   - Every evidence record keeps its provenance and verification
 *     timestamp; null means "not recorded".
 *   - Local helm PASS (HELM-001..003) never implies deployed or prod-ready.
 *
 * Presentational only: no fetching, no state, no mock values.
 */

import { memo, useMemo } from "react";
import type { SystemStatus } from "@/lib/health";
import {
  buildReadinessSummary,
  overallToPanelStatus,
  readinessOverallDisplay,
} from "@/lib/dashboard/deploymentReadiness/deploymentReadinessAdapter";
import {
  READINESS_EVIDENCE,
  readinessPolicyNote,
} from "@/lib/dashboard/deploymentReadiness/deploymentReadinessData";
import Panel from "./Panel";
import ReadinessCheckCard from "./ReadinessCheckCard";

import "@/app/styles/deployment-readiness.css";

/** Turn the app health pill into an honest section lifecycle label. */
function sectionLabelOf(apiHealth: SystemStatus | null): string {
  if (apiHealth === null) {
    return "Application health pending";
  }
  if (apiHealth === "degraded") {
    return "Application health degraded";
  }
  if (apiHealth === "unavailable") {
    return "Application health unavailable";
  }
  return "Application health ok";
}

export default memo(function DeploymentReadinessPanel({
  apiHealth,
}: {
  apiHealth: SystemStatus | null;
}) {
  const sectionLabel = sectionLabelOf(apiHealth);

  const summary = useMemo(
    () => buildReadinessSummary(READINESS_EVIDENCE),
    [],
  );

  const panelTone = overallToPanelStatus(summary.overall);

  return (
    <section
      id="readiness"
      className="hs-zone hs-zone--readiness"
      aria-labelledby="readiness-title"
    >
      <header className="hs-zone__header">
        <div>
          <h2 id="readiness-title" className="hs-zone__title">
            Deployment Readiness
          </h2>
          <p className="hs-zone__subtitle">{readinessPolicyNote}</p>
        </div>
        <span
          className={`readiness-overview__badge readiness-overview__badge--${summary.overall.toLowerCase()}`}
          role="status"
          aria-live="polite"
        >
          {readinessOverallDisplay[summary.overall]}
        </span>
      </header>

      <div className="hs-zone__body">
        <Panel
          title="OVERALL READINESS"
          status={panelTone}
          className="readiness-overview"
        >
          <div className="readiness-overview__row">
            <span className="readiness-overview__label">State</span>
            <span
              className={`readiness-overview__value readiness-overview__value--${summary.overall.toLowerCase()}`}
            >
              {readinessOverallDisplay[summary.overall]}
            </span>
          </div>
          <p className="readiness-overview__note">
            {sectionLabel}
            {" | "}
            {summary.reasons.length > 0
              ? summary.reasons.join(" | ")
              : "No evidence evaluated."}
          </p>
          <details
            className="readiness-overview__details"
            aria-label="Readiness derivation details"
          >
            <summary className="readiness-overview__summary">
              Derivation rules for this program
            </summary>
            <ul className="readiness-overview__rules">
              <li>
                READY requires every mandatory check (HELM-001..003) to be
                PASS and every estate check to be PASS.
              </li>
              <li>
                DEGRADED requires the baseline to pass while live estate or
                deployment evidence is WARNING / NOT_*.
              </li>
              <li>
                BLOCKED requires a mandatory check to be FAIL or BLOCKED.
              </li>
              <li>
                UNKNOWN requires mandatory evidence to be missing.
              </li>
            </ul>
          </details>
        </Panel>

        <div className="readiness-checks">
          {summary.checkStates.map((check) => (
            <ReadinessCheckCard key={check.id} check={check} />
          ))}
        </div>

        <p className="readiness-section__note">
          {sectionLabel}
          {" | "}
          This panel is derived from the evidence records above.{" "}
          {readinessPolicyNote}
        </p>
      </div>
    </section>
  );
});
