/**
 * ReadinessCheckCard — one deployment-readiness evidence check rendered
 * with the existing Panel component and design-system tokens (PHASE 2).
 *
 * Presentational. No fetching, no state, no mock values.
 */

import { memo } from "react";
import {
  readinessStatusDisplay,
  readinessToPanelStatus,
} from "@/lib/dashboard/deploymentReadiness/deploymentReadinessAdapter";
import Panel from "./Panel";

/** Lucide icons owned by this panel (decoration only, no data). */
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Globe2,
  HardDrive,
  HelpCircle,
  ShieldAlert,
} from "lucide-react";

import type { ReadinessCheckState } from "@/lib/dashboard/deploymentReadiness/deploymentReadinessModels";

export interface ReadinessCheckCardProps {
  check: ReadinessCheckState;
}

export default memo(function ReadinessCheckCard({
  check,
}: ReadinessCheckCardProps) {
  const token = readinessToPanelStatus[check.status];
  const tone: "healthy" | "warning" | "critical" | "info" =
    check.status === "PASS"
      ? "healthy"
      : check.status === "WARNING" || check.status === "STALE"
        ? "warning"
        : check.status === "FAIL" || check.status === "BLOCKED"
          ? "critical"
          : "info";

  const Icon = {
    "HELM-001": CheckCircle2,
    "HELM-002": CheckCircle2,
    "HELM-003": CheckCircle2,
    "HELM-004": AlertTriangle,
    "K8S-001": Globe2,
    "K8S-002": ShieldAlert,
    "APP-DEPLOY-001": HardDrive,
    "POD-HEALTH-001": Clock,
    "PROD-CONFIG-001": HelpCircle,
  }[check.id] ?? HelpCircle;

  return (
    <Panel title={check.title} status={token} className="readiness-check">
      <div className="readiness-check__head">
        <span className={`readiness-check__dot readiness-check__dot--${tone}`} />
        <Icon className="readiness-check__icon" aria-hidden="true" size={14} />
        <span className="readiness-check__title">{check.title}</span>
        <span
          className={`readiness-check__badge readiness-check__badge--${tone}`}
          aria-label={readinessStatusDisplay[check.status]}
        >
          {readinessStatusDisplay[check.status]}
        </span>
      </div>

      <p className="readiness-check__detail">{check.detail}</p>

      <details
        className="readiness-check__diagnostics"
        open
        aria-label={`Diagnostics for ${check.title}`}
      >
        <summary className="readiness-check__summary">
          <span className="readiness-check__summary-label">Diagnostics</span>
          <span
            className="readiness-check__summary-chevron"
            aria-hidden="true"
          >
            ▸
          </span>
        </summary>

        <div className="readiness-check__diag">
          <div className="readiness-check__diag-row">
            <span>Source</span>
            <code>{check.source}</code>
          </div>
          {check.environment && (
            <div className="readiness-check__diag-row">
              <span>Environment</span>
              <span>{check.environment}</span>
            </div>
          )}
          {check.version != null && (
            <div className="readiness-check__diag-row">
              <span>Version</span>
              <span>{check.version}</span>
            </div>
          )}
          {check.exitCode !== null && (
            <div className="readiness-check__diag-row">
              <span>Exit code</span>
              <span>{check.exitCode}</span>
            </div>
          )}
          <div className="readiness-check__diag-row">
            <span>Provenance</span>
            <span className="readiness-check__provenance">
              {check.provenance === "MANUAL" ? "MANUAL" : "AUTOMATED"}
            </span>
          </div>
          <div className="readiness-check__diag-row">
            <span>Verified at</span>
            <span>{check.verificationTime ?? "not recorded"}</span>
          </div>
        </div>
      </details>
    </Panel>
  );
});
