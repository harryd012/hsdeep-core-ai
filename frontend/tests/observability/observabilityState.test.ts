/**
 * observabilityState.test.ts - the truth rules for the shared data-lifecycle
 * state machine (Phase 1).
 *
 * Run with:  node --test tests/observability/observabilityState.test.ts
 *
 * `tests/**` is excluded from the project tsconfig on purpose, which is why
 * this file imports the source module with an explicit `.ts` specifier (Next
 * resolves it extensionless; Node needs the extension).
 *
 * These tests exist to lock the two guarantees the whole dashboard depends on:
 *   1. a failed / missing / unavailable source is NEVER rendered as data, and
 *      NEVER as a fabricated zero; and
 *   2. data without a determinable freshness is NEVER labelled live/fresh.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  DATA_STATE_VISUALS,
  DEFAULT_STALE_AFTER_MINUTES,
  dataStateToTone,
  dataStateVisual,
  deriveDataState,
  errorMessage,
  STATUS_TONE_COLOR,
  type DataState,
} from "../../src/lib/observability/observabilityState.ts";

const ALL_STATES: DataState[] = [
  "LOADING",
  "ERROR",
  "EMPTY",
  "STALE",
  "UNAVAILABLE",
  "OK",
];

function nowIso(): string {
  return new Date().toISOString();
}

test("every state has a label, description and a valid tone", () => {
  assert.deepEqual(Object.keys(DATA_STATE_VISUALS).sort(), [...ALL_STATES].sort());
  for (const s of ALL_STATES) {
    const v = DATA_STATE_VISUALS[s];
    assert.ok(v.label.length > 0, s);
    assert.ok(v.description.length > 0, s);
    assert.ok(["healthy", "warning", "critical", "info"].includes(v.tone), s);
    // Every tone maps to an existing design token.
    assert.ok(STATUS_TONE_COLOR[v.tone].startsWith("var(--"), s);
  }
});

test("ERROR outranks everything, even data that is present and fresh", () => {
  assert.equal(deriveDataState({ error: new Error("boom"), data: [1, 2, 3] }), "ERROR");
  // An error must never collapse to a numeric/OK state.
  assert.notEqual(deriveDataState({ error: {}, data: 42, lastCollectedAt: nowIso() }), "OK");
});

test("UNAVAILABLE is its own state, distinct from ERROR and EMPTY", () => {
  assert.equal(deriveDataState({ unavailable: true }), "UNAVAILABLE");
  // A reachable-but-empty source is EMPTY, not UNAVAILABLE.
  assert.equal(deriveDataState({ data: [] }), "EMPTY");
  assert.notEqual(deriveDataState({ unavailable: true }), deriveDataState({ data: [] }));
});

test("LOADING is only the initial load with no data", () => {
  assert.equal(deriveDataState({ loading: true }), "LOADING");
  // With data present and no loading flag, the real data wins.
  assert.equal(deriveDataState({ data: [1], lastCollectedAt: nowIso() }), "OK");
});

test("EMPTY means a reachable source with genuinely no rows", () => {
  assert.equal(deriveDataState({ data: [] }), "EMPTY");
  assert.equal(deriveDataState({ data: {} }), "EMPTY");
  assert.equal(deriveDataState({ data: null }), "EMPTY");
  assert.equal(deriveDataState({ data: undefined }), "EMPTY");
});

test("a real, fresh value is OK", () => {
  assert.equal(deriveDataState({ data: 12, lastCollectedAt: nowIso() }), "OK");
  assert.equal(deriveDataState({ data: [1, 2], lastCollectedAt: nowIso() }), "OK");
});

test("data older than the policy is STALE, never OK", () => {
  const old = new Date(Date.now() - (DEFAULT_STALE_AFTER_MINUTES + 5) * 60_000).toISOString();
  assert.equal(deriveDataState({ data: 12, lastCollectedAt: old }), "STALE");
  // A custom, tighter policy is honoured.
  const slightlyOld = new Date(Date.now() - 10 * 60_000).toISOString();
  assert.equal(
    deriveDataState({ data: 12, lastCollectedAt: slightlyOld, staleAfterMinutes: 5 }),
    "STALE",
  );
});

test("data with no determinable freshness is NEVER labelled fresh (STALE)", () => {
  // Real data but no timestamp: we cannot assert freshness.
  assert.equal(deriveDataState({ data: 12, lastCollectedAt: null }), "STALE");
  // An unparseable timestamp is the same as no timestamp.
  assert.equal(deriveDataState({ data: 12, lastCollectedAt: "not-a-date" }), "STALE");
});

test("never-collected is never presented as live (structural no-false-zero)", () => {
  // There is no input combination that yields a numeric OK from empty data.
  assert.notEqual(deriveDataState({ data: [], lastCollectedAt: nowIso() }), "OK");
  assert.equal(deriveDataState({ data: [], lastCollectedAt: nowIso() }), "EMPTY");
});

test("tones never paint an unknown/failed state as healthy", () => {
  for (const s of ["ERROR", "EMPTY", "UNAVAILABLE", "LOADING", "STALE"] as DataState[]) {
    assert.notEqual(dataStateToTone(s), "healthy", s);
  }
  assert.equal(dataStateToTone("OK"), "healthy");
  assert.equal(dataStateToTone("ERROR"), "critical");
  assert.equal(dataStateToTone("STALE"), "warning");
  assert.equal(dataStateToTone("UNAVAILABLE"), "warning");
});

test("dataStateVisual agrees with deriveDataState", () => {
  assert.equal(dataStateVisual({ error: true }), DATA_STATE_VISUALS.ERROR);
  assert.equal(dataStateVisual({ data: 5, lastCollectedAt: nowIso() }), DATA_STATE_VISUALS.OK);
});

test("errorMessage never leaks a raw error and defaults to unknown", () => {
  assert.equal(errorMessage(null), "Request failed");
  assert.equal(errorMessage(undefined), "Request failed");
  assert.equal(errorMessage("unauthorized"), "Not authorized - session may have expired");
  assert.equal(errorMessage("rate_limited"), "Rate or quota limit reached");
});

test("numeric zero is a real value, not absence", () => {
  // 0 is finite and therefore a genuine reading -> OK when fresh.
  assert.equal(deriveDataState({ data: 0, lastCollectedAt: nowIso() }), "OK");
  // NaN is not a real reading -> EMPTY, never a rendered 0.
  assert.equal(deriveDataState({ data: Number.NaN, lastCollectedAt: nowIso() }), "EMPTY");
});