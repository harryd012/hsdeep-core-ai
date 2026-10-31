/**
 * deploymentReadinessModels.ts — typed, documented model for the
 * Deployment Readiness panel (PHASE 2/3).
 *
 * These types describe WHAT the panel displays. They do not describe
 * WHERE the data comes from: readiness evidence is static, manually
 * verified evidence (provenance = MANUAL), and no backend payload can
 * settle a deployment-readiness question (local Helm, cluster
 * connectivity, live pod health). The panel therefore owns no fetching
 * and invents nothing.
 *
 * Status model (8 labels):
 *   PASS          — the check ran and the result is a positive finding.
 *   WARNING       — the check ran; a non-blocking advisory is raised.
 *   FAIL          — the check ran and the result is negative (blocking).
 *   BLOCKED       — the check's precondition is unavailable (run blocked).
 *   NOT_CHECKED   — no evidence for this check exists in the program.
 *   NOT_DEPLOYED  — the application is not deployed in this program.
 *   NOT_AVAILABLE — evidence not available right now (probe failed,
 *                   no cluster, no credentials).
 *   STALE         — evidence older than the policy clock.
 *
 * Provenance model:
 *   MANUAL — executed outside the app (operator release engineer) and
 *            recorded into this evidence set by hand.
 *   AUTOMATED — produced by a first-party collector/agent inside the app.
 *   Manual evidence is authoritative for this sprint because the
 *   application cannot discover a developer's local Helm installation,
 *   kubeconfig, or deployed clusters.
 *
 * Overall state policy (derived ONLY from the 8 statuses above):
 *   READY      — every MANDATORY check has valid PASS evidence and every
 *                non-mandatory estate check is also PASS.
 *   DEGRADED   — the required baseline (local helm tooling + chart
 *                validity) passes, but non-critical checks carry
 *                WARNING / NOT_* evidence (deployment, pod health or
 *                cluster not observed). A local helm PASS NEVER
 *                implies the application is deployed or production ready.
 *   BLOCKED    — a MANDATORY check is FAIL or BLOCKED, or a mandatory
 *                prerequisite is unavailable.
 *   UNKNOWN    — insufficient evidence exists to classify (mandatory
 *                evidence is missing).
 *
 * Rule of thumb: unknown Kubernetes / deployment state is never PASS.
 */

export type ReadinessStatus =
  | "PASS"
  | "WARNING"
  | "FAIL"
  | "BLOCKED"
  | "NOT_CHECKED"
  | "NOT_DEPLOYED"
  | "NOT_AVAILABLE"
  | "STALE";

export type ReadinessProvenance = "MANUAL" | "AUTOMATED";

/** Stable evidence id; keys the program's hand-book, never the data. */
export interface ReadinessEvidenceRecord {
  id: string;
  /** Human-readable title for operators. */
  title: string;
  status: ReadinessStatus;
  provenance: ReadinessProvenance;
  source: string;
  /** One measured/observed detail, or an explicit "not observed"/"not run" note. */
  detail: string;
  environment?: string | null;
  version?: string | null;
  exitCode?: number | null;
  /** ISO-8601 verified-at, or null when not independently established. */
  verificationTime: string | null;
}

export type ReadinessOverall = "READY" | "DEGRADED" | "BLOCKED" | "UNKNOWN";

export interface ReadinessCheckState {
  id: string;
  title: string;
  status: ReadinessStatus;
  provenance: ReadinessProvenance;
  source: string;
  detail: string;
  environment?: string | null;
  version?: string | null;
  exitCode: number | null;
  verificationTime: string | null;
  renderedAt: string | null;
}

export interface ReadinessSummary {
  overall: ReadinessOverall;
  checkStates: ReadinessCheckState[];
  /** Human-readable reasons (never metrics, never claims of health). */
  reasons: string[];
}

/** Panel status token for reuse of the existing Panel component. */
export type PanelStatus = "healthy" | "warning" | "critical" | "info";

/** Mapping readiness labels onto the existing panel tokens. */
export const readinessToPanelStatus: Record<ReadinessStatus, PanelStatus> = {
  PASS: "healthy",
  WARNING: "warning",
  FAIL: "critical",
  BLOCKED: "critical",
  NOT_CHECKED: "info",
  NOT_DEPLOYED: "info",
  NOT_AVAILABLE: "info",
  STALE: "warning",
};

export const readinessStatusLabel: Record<ReadinessStatus, string> = {
  PASS: "PASS",
  WARNING: "WARNING",
  FAIL: "FAIL",
  BLOCKED: "BLOCKED",
  NOT_CHECKED: "NOT CHECKED",
  NOT_DEPLOYED: "NOT DEPLOYED",
  NOT_AVAILABLE: "NOT AVAILABLE",
  STALE: "STALE",
};

export const readinessOverallLabel: Record<ReadinessOverall, string> = {
  READY: "READY",
  DEGRADED: "DEGRADED",
  BLOCKED: "BLOCKED",
  UNKNOWN: "UNKNOWN",
};
