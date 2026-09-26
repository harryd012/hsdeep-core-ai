/** Empty-state composition tests (Phase 9.7A). */
import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_HEADING,
  buildEmptyState,
  isGraphEmpty,
  type EmptyStateInput,
} from "../../src/lib/topology/emptyState.ts";

const BASE: EmptyStateInput = {
  devices: 265,
  relationships: 0,
  interfaces: 0,
  online: 11,
  offline: 254,
  unidentified: 265,
  limitations: [
    "No network relationship data has been discovered for this tenant.",
    "No interfaces have been collected for this tenant, so no interface-level links can be derived.",
  ],
};

test("the empty state states the real position with real counts", () => {
  const state = buildEmptyState(BASE);

  assert.equal(state.heading, EMPTY_HEADING);
  assert.match(state.explanation, /no validated network relationship evidence/);
  assert.match(state.explanation, /265 device/);
  assert.match(state.explanation, /11 online/);
  assert.match(state.explanation, /254 offline/);

  const counts = Object.fromEntries(state.counts.map((count) => [count.label, count.value]));
  assert.deepEqual(counts, { Devices: 265, Relationships: 0, Interfaces: 0 });
  assert.equal(state.counts.length, 3);
});

test("the next step is informational and names the missing capability", () => {
  const state = buildEmptyState(BASE);
  assert.match(state.nextStep, /topology-capable collector/);
  assert.match(state.nextStep, /informational only/);
});

test("backend limitations are surfaced verbatim and de-duplicated", () => {
  const state = buildEmptyState({
    ...BASE,
    limitations: ["a", "a", "", "   ", "b"],
  });
  assert.deepEqual(state.evidenceNotes, ["a", "b"]);
});

test("a tenant with no devices is described differently, not padded", () => {
  const state = buildEmptyState({ ...BASE, devices: 0, online: 0, offline: 0, unidentified: 0 });
  assert.match(state.explanation, /No devices have been recorded/);
  assert.equal(
    state.counts.find((count) => count.label === "Devices")!.value,
    0
  );
});

test("isGraphEmpty is true only when no relationship is recorded", () => {
  assert.equal(isGraphEmpty(0), true);
  assert.equal(isGraphEmpty(1), false);
});

test("the empty state never offers a workflow it cannot perform", () => {
  const state = buildEmptyState(BASE);
  assert.equal(Object.prototype.hasOwnProperty.call(state, "action"), false);
  assert.match(state.nextStep, /will populate automatically/);
});