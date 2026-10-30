/**
 * deploymentReadinessAdapter.ts - pure derivation view-model for the
 * deployment readiness panel.
 *
 *   evidence records (deploymentReadinessData.ts, provenance MANUAL)
 *            v
 *   buildReadinessSummary(...)   <- pure, deterministic, no fetching
 *            v
 *   DeploymentReadinessPanel   <- presentational
 *
 * Truth rules:
 *   - The overall state is derived ONLY from the statuses of the records
 *     and the documented MANDATORY set. Unknown kubernetes / deployment
 *     state is never treated as healthy.
 *   - A local helm PASS (HELM-001..003) never implies the application is
 *     deployed or production ready - DEPLOYED/PROD-READY additionally
 *     requires K8S connectivity, authentication, a real deployment and
 *     live pod health.
 *   - Duplicate/overlapping evidence for one check is merged to the worst
 *     status (FAIL > BLOCKED > WARNING > ... > PASS).
 *   - A record whose verificationTime is older than the policy clock
 *     (7 days) is classified STALE. A record with no timestamp is never
 *     claimed stale - the UI shows "not recorded".
 */

import type {
  ReadinessCheckState,
  ReadinessEvidenceRecord,
  ReadinessOverall,
  ReadinessStatus,
  ReadinessSummary,
} from "./deploymentReadinessModels.ts";
import {
  readinessOverallLabel,
  readinessStatusLabel,
  readinessToPanelStatus,
} from "./deploymentReadinessModels.ts";

/**
 * Checks whose PASS is required for the baseline "deployment tooling +
 * chart validity" gate. HELM-004 (chart icon advisory) is intentionally
 * NOT mandatory - it is a non-failing recommendation.
 */
export const MANDATORY_CHECK_IDS = [
  "HELM-001",
  "HELM-002",
  "HELM-003",
] as const;

export const MANDATORY_CHECK_SET: ReadonlySet<string> = new Set(
  MANDATORY_CHECK_IDS,
);

/** Policy clock for STALE classification (days). */
export const READINESS_STALE_DAYS = 7;

/**
 * Merge duplicate records for the same check into the worst status.
 * Deterministic tie-break (worst first):
 *   FAIL > BLOCKED > WARNING > STALE > NOT_AVAILABLE > NOT_DEPLOYED >
 *   NOT_CHECKED > PASS
 */
function mergeDuplicateChecks(
  records: readonly ReadinessEvidenceRecord[],
): Map<string, ReadinessEvidenceRecord> {
  const best = new Map<string, ReadinessEvidenceRecord>();

  for (const record of records) {
    const existing = best.get(record.id);
    if (!existing) {
      best.set(record.id, record);
      continue;
    }
    if (worseStatus(record.status, existing.status)) {
      best.set(record.id, record);
    }
  }

  return best;
}

/** Total order used for duplicate merging (worst first). */
function worseStatus(a: ReadinessStatus, b: ReadinessStatus): boolean {
  const order: ReadinessStatus[] = [
    "FAIL",
    "BLOCKED",
    "WARNING",
    "STALE",
    "NOT_AVAILABLE",
    "NOT_DEPLOYED",
    "NOT_CHECKED",
    "PASS",
  ];
  return order.indexOf(a) < order.indexOf(b);
}

/**
 * Classify a single record, applying the STALE policy when its
 * verificationTime is older than READINESS_STALE_DAYS.
 */
function classifyRecord(
  record: ReadinessEvidenceRecord,
  now: Date,
): ReadinessEvidenceRecord {
  let status = record.status;

  if (record.verificationTime) {
    const parsed = Date.parse(record.verificationTime);
    if (!Number.isNaN(parsed)) {
      const ageDays = (now.getTime() - parsed) / (1000 * 60 * 60 * 24);
      if (ageDays > READINESS_STALE_DAYS) {
        status = "STALE";
      }
    }
  }

  return { ...record, status };
}

/**
 * Build the readiness summary from evidence records.
 *
 * Overall-state policy (documented in deploymentReadinessModels.ts):
 *   READY      - every MANDATORY check has valid PASS evidence, and every
 *                non-mandatory check is also PASS.
 *   DEGRADED   - the required baseline (local helm tooling + chart
 *                validity) passes, but non-critical checks carry
 *                WARNING / STALE / NOT_* evidence. A local helm PASS NEVER
 *                implies the application is deployed or production ready.
 *   BLOCKED    - a MANDATORY check is FAIL or BLOCKED, or a mandatory
 *                prerequisite is unavailable.
 *   UNKNOWN    - insufficient evidence exists to classify (mandatory
 *                evidence missing).
 *
 * Deterministic and pure: the only clock used is `options.now`, defaulting
 * to the current time. Unknown kubernetes / deployment state is never PASS.
 */
export function buildReadinessSummary(
  records: readonly ReadinessEvidenceRecord[],
  options: { now?: Date } = {},
): ReadinessSummary {
  const now = options.now ?? new Date();
  const merged = mergeDuplicateChecks(records);

  const checkStates: ReadinessCheckState[] = [];
  const reasons: string[] = [];

  // Mandatory coverage gate: every mandatory id must be present.
  const missingMandatory: string[] = [];
  for (const id of MANDATORY_CHECK_IDS) {
    if (!merged.has(id)) {
      missingMandatory.push(id);
    }
  }

  // Classify every supplied record (keeps non-mandatory + duplicates visible).
  for (const record of records) {
    const classified = classifyRecord(record, now);
    checkStates.push({
      id: classified.id,
      title: classified.title,
      status: classified.status,
      provenance: classified.provenance,
      source: classified.source,
      detail: classified.detail,
      environment: classified.environment,
      version: classified.version,
      exitCode: classified.exitCode ?? null,
      verificationTime: classified.verificationTime,
      renderedAt: now.toISOString(),
    });
  }

  // ---- overall state derivation (from worst-status MERGED states only) ----
  // Decide from the merged (worst-status) record per id, classified for
  // STALE like the visible check list - so duplicate/overlapping evidence
  // collapses to its worst status instead of a last-writer-wins map.
  const byId = new Map<string, ReadinessCheckState>();
  for (const record of merged.values()) {
    const classified = classifyRecord(record, now);
    byId.set(classified.id, {
      id: classified.id,
      title: classified.title,
      status: classified.status,
      provenance: classified.provenance,
      source: classified.source,
      detail: classified.detail,
      environment: classified.environment,
      version: classified.version,
      exitCode: classified.exitCode ?? null,
      verificationTime: classified.verificationTime,
      renderedAt: now.toISOString(),
    });
  }

  let overall: ReadinessOverall = "UNKNOWN";
  let blocked = false;
  let allMandatoryPass = true;

  for (const id of MANDATORY_CHECK_IDS) {
    const state = byId.get(id);
    if (!state) {
      allMandatoryPass = false;
      continue;
    }
    if (state.status === "FAIL" || state.status === "BLOCKED") {
      blocked = true;
    }
    if (state.status !== "PASS") {
      allMandatoryPass = false;
    }
  }

  if (missingMandatory.length > 0) {
    reasons.push(
      "UNKNOWN - mandatory evidence missing for " + missingMandatory.join(", "),
    );
  } else if (blocked) {
    overall = "BLOCKED";
    reasons.push("BLOCKED - a mandatory check is FAIL or BLOCKED");
  } else if (allMandatoryPass) {
    const nonMandatory = [...byId.values()].filter(
      (c) => !MANDATORY_CHECK_SET.has(c.id),
    );
    const degradedSignals = nonMandatory.some(
      (c) =>
        c.status === "WARNING" ||
        c.status === "NOT_DEPLOYED" ||
        c.status === "NOT_AVAILABLE" ||
        c.status === "NOT_CHECKED" ||
        c.status === "STALE",
    );
    if (degradedSignals) {
      overall = "DEGRADED";
      reasons.push(
        "DEGRADED - baseline (helm) passes but live estate/deployment evidence is not ready",
      );
    } else {
      overall = "READY";
      reasons.push("READY - all checks pass");
    }
  }

  return {
    overall,
    checkStates,
    reasons,
  };
}

/**
 * Overall -> existing Panel status token (the shared 4-token vocabulary).
 * UNKNOWN renders neutral "info"; it is never painted healthy.
 */
export function overallToPanelStatus(
  overall: ReadinessOverall,
): "healthy" | "warning" | "critical" | "info" {
  switch (overall) {
    case "READY":
      return "healthy";
    case "DEGRADED":
      return "warning";
    case "BLOCKED":
      return "critical";
    case "UNKNOWN":
    default:
      return "info";
  }
}

/** Re-exported display labels + panel-token mapper for presentation. */
export const readinessStatusDisplay = readinessStatusLabel;
export const readinessOverallDisplay = readinessOverallLabel;
export { readinessToPanelStatus };