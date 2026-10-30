/**
 * Deployment Readiness adapter / model tests.
 *
 * Run with:  node --test tests/deploymentReadiness
 *
 * `tests/**` is excluded from the project tsconfig on purpose, which is why
 * these files import the source modules with explicit `.ts` specifiers - the
 * bundler (Next) resolves them extensionless, Node needs the extension.
 *
 * These tests pin the TRUTH contract of the readiness model:
 *   - the 8 status labels and 4 overall states are exhaustive and mapped;
 *   - the overall state is DERIVED from evidence, never authored;
 *   - unknown Kubernetes / deployment state is NEVER PASS;
 *   - a local helm PASS never implies deployed / production ready;
 *   - STALE policy and duplicate (worst-status) merging behave as documented;
 *   - the shipped MANUAL evidence set derives DEGRADED with honest null
 *     verification timestamps (no fabricated times).
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  MANDATORY_CHECK_IDS,
  READINESS_STALE_DAYS,
  buildReadinessSummary,
  overallToPanelStatus,
  readinessOverallDisplay,
} from "../../src/lib/dashboard/deploymentReadiness/deploymentReadinessAdapter.ts";
import {
  READINESS_EVIDENCE,
} from "../../src/lib/dashboard/deploymentReadiness/deploymentReadinessData.ts";
import {
  readinessOverallLabel,
  readinessStatusLabel,
  readinessToPanelStatus,
} from "../../src/lib/dashboard/deploymentReadiness/deploymentReadinessModels.ts";
import type {
  ReadinessEvidenceRecord,
  ReadinessOverall,
  ReadinessStatus,
} from "../../src/lib/dashboard/deploymentReadiness/deploymentReadinessModels.ts";

/** Fixed clock so STALE / derivation tests are deterministic. */
const NOW = new Date("2026-01-15T00:00:00.000Z");

function record(
  over: Partial<ReadinessEvidenceRecord> &
    Pick<ReadinessEvidenceRecord, "id" | "status">,
): ReadinessEvidenceRecord {
  return {
    title: over.id,
    provenance: "MANUAL",
    source: "test",
    detail: "test",
    verificationTime: null,
    ...over,
  };
}

const ALL_STATUSES: ReadinessStatus[] = [
  "PASS",
  "WARNING",
  "FAIL",
  "BLOCKED",
  "NOT_CHECKED",
  "NOT_DEPLOYED",
  "NOT_AVAILABLE",
  "STALE",
];


// ---------------------------------------------------------------------------
// Status model validation + serialization
// ---------------------------------------------------------------------------

test("all 8 readiness statuses have a display label and a panel token", () => {
  for (const status of ALL_STATUSES) {
    assert.ok(readinessStatusLabel[status].length > 0, status);
    assert.ok(
      ["healthy", "warning", "critical", "info"].includes(
        readinessToPanelStatus[status],
      ),
      status,
    );
  }
  assert.deepEqual(
    Object.keys(readinessStatusLabel).sort(),
    ALL_STATUSES.slice().sort(),
  );
});

test("unknown-ish statuses never map to a healthy panel token", () => {
  for (const status of [
    "NOT_CHECKED",
    "NOT_DEPLOYED",
    "NOT_AVAILABLE",
    "BLOCKED",
    "FAIL",
  ] as ReadinessStatus[]) {
    assert.notEqual(readinessToPanelStatus[status], "healthy", status);
  }
});

test("all 4 overall states have a display label", () => {
  for (const overall of ALL_OVERALLS) {
    assert.equal(readinessOverallLabel[overall], overall);
    assert.equal(readinessOverallDisplay[overall], overall);
  }
});

// ---------------------------------------------------------------------------
// Overall readiness derivation
// ---------------------------------------------------------------------------

test("all mandatory PASS with only mandatory checks present => READY", () => {
  const summary = buildReadinessSummary(
    MANDATORY_CHECK_IDS.map((id) => record({ id, status: "PASS" })),
    { now: NOW },
  );
  assert.equal(summary.overall, "READY");
  assert.equal(overallToPanelStatus(summary.overall), "healthy");
});

test("a non-critical WARNING check downgrades READY to DEGRADED", () => {
  const summary = buildReadinessSummary(
    [
      ...MANDATORY_CHECK_IDS.map((id) => record({ id, status: "PASS" })),
      record({ id: "HELM-004", status: "WARNING" }),
    ],
    { now: NOW },
  );
  assert.equal(summary.overall, "DEGRADED");
  assert.equal(overallToPanelStatus(summary.overall), "warning");
});

test("a mandatory FAIL makes the program BLOCKED", () => {
  const summary = buildReadinessSummary(
    [
      record({ id: "HELM-001", status: "PASS" }),
      record({ id: "HELM-002", status: "FAIL" }),
      record({ id: "HELM-003", status: "PASS" }),
    ],
    { now: NOW },
  );
  assert.equal(summary.overall, "BLOCKED");
  assert.equal(overallToPanelStatus(summary.overall), "critical");
});

test("missing mandatory evidence => UNKNOWN (never READY/BLOCKED)", () => {
  const summary = buildReadinessSummary(
    [record({ id: "HELM-001", status: "PASS" })],
    { now: NOW },
  );
  assert.equal(summary.overall, "UNKNOWN");
  assert.equal(overallToPanelStatus(summary.overall), "info");
  assert.ok(
    summary.reasons.some((r) => r.includes("HELM-002")),
    "reasons should name the missing mandatory check",
  );
});

test("unknown cluster state (NOT_CHECKED) never yields READY", () => {
  const summary = buildReadinessSummary(
    [
      ...MANDATORY_CHECK_IDS.map((id) => record({ id, status: "PASS" })),
      record({ id: "K8S-001", status: "NOT_CHECKED" }),
      record({ id: "K8S-002", status: "NOT_CHECKED" }),
    ],
    { now: NOW },
  );
  assert.notEqual(summary.overall, "READY");
  assert.equal(summary.overall, "DEGRADED");
});


// ---------------------------------------------------------------------------
// Conflicting (duplicate) evidence -> worst status wins
// ---------------------------------------------------------------------------

test("conflicting evidence for one mandatory check collapses to worst", () => {
  const summary = buildReadinessSummary(
    [
      record({ id: "HELM-001", status: "PASS", detail: "run A" }),
      record({ id: "HELM-001", status: "FAIL", detail: "run B" }),
      record({ id: "HELM-002", status: "PASS" }),
      record({ id: "HELM-003", status: "PASS" }),
    ],
    { now: NOW },
  );
  assert.equal(summary.overall, "BLOCKED");
});

test("duplicate worst-status holds regardless of record order", () => {
  const base = [
    record({ id: "HELM-001", status: "PASS" }),
    record({ id: "HELM-001", status: "WARNING" }),
    record({ id: "HELM-002", status: "PASS" }),
    record({ id: "HELM-003", status: "PASS" }),
  ];
  const forward = buildReadinessSummary(base, { now: NOW });
  const reversed = buildReadinessSummary([...base].reverse(), { now: NOW });
  assert.equal(forward.overall, reversed.overall);
});

// ---------------------------------------------------------------------------
// Stale evidence policy
// ---------------------------------------------------------------------------

test("verification older than the policy clock is classified STALE", () => {
  const old = new Date(
    NOW.getTime() - (READINESS_STALE_DAYS + 1) * 24 * 60 * 60 * 1000,
  ).toISOString();
  const summary = buildReadinessSummary(
    [record({ id: "HELM-001", status: "PASS", verificationTime: old })],
    { now: NOW },
  );
  const state = summary.checkStates.find((c) => c.id === "HELM-001");
  assert.ok(state);
  assert.equal(state?.status, "STALE");
});

test("evidence with no timestamp is never claimed STALE", () => {
  const summary = buildReadinessSummary(
    [record({ id: "HELM-001", status: "PASS", verificationTime: null })],
    { now: NOW },
  );
  const state = summary.checkStates.find((c) => c.id === "HELM-001");
  assert.ok(state);
  assert.equal(state?.status, "PASS");
});

// ---------------------------------------------------------------------------
// Provenance
// ---------------------------------------------------------------------------

test("provenance is preserved verbatim on every check state", () => {
  const summary = buildReadinessSummary(
    [
      record({ id: "HELM-001", status: "PASS", provenance: "MANUAL" }),
      record({ id: "HELM-002", status: "PASS", provenance: "AUTOMATED" }),
      record({ id: "HELM-003", status: "PASS", provenance: "MANUAL" }),
    ],
    { now: NOW },
  );
  const auto = summary.checkStates.find((c) => c.id === "HELM-002");
  assert.equal(auto?.provenance, "AUTOMATED");
});

// ---------------------------------------------------------------------------
// The shipped MANUAL evidence set (no fabricated timestamps)
// ---------------------------------------------------------------------------

test("shipped evidence derives DEGRADED with honest null timestamps", () => {
  for (const r of READINESS_EVIDENCE) {
    assert.equal(r.provenance, "MANUAL", r.id);
    assert.equal(r.verificationTime, null, r.id);
  }
  const summary = buildReadinessSummary(READINESS_EVIDENCE, { now: NOW });
  assert.equal(summary.overall, "DEGRADED");
  assert.notEqual(summary.overall, "READY");
  for (const state of summary.checkStates) {
    assert.equal(state.verificationTime, null, state.id);
  }
});

test("NOT_DEPLOYED live estate keeps the program DEGRADED, not READY", () => {
  const summary = buildReadinessSummary(
    [
      ...MANDATORY_CHECK_IDS.map((id) => record({ id, status: "PASS" })),
      record({ id: "APP-DEPLOY-001", status: "NOT_DEPLOYED" }),
      record({ id: "POD-HEALTH-001", status: "NOT_AVAILABLE" }),
    ],
    { now: NOW },
  );
  assert.equal(summary.overall, "DEGRADED");
});

const ALL_OVERALLS: ReadinessOverall[] = [
  "READY",
  "DEGRADED",
  "BLOCKED",
  "UNKNOWN",
];
