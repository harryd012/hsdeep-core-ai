/**
 * DeviceState / LinkState projection tests (Phase 9.5).
 *
 * Run with:  node --test tests/topology
 *
 * `tests/**` is excluded from the project tsconfig on purpose, which is why these
 * files import the source modules with explicit `.ts` specifiers — the bundler
 * (Next) resolves them extensionless, Node needs the extension.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEVICE_STATE_VISUALS,
  LINK_STATE_VISUALS,
  deviceState,
  freshnessLabel,
  isFreshnessCurrent,
  isNeverCollected,
  linkState,
} from "../../src/lib/topology/status.ts";

test("all six device states exist and carry a non-colour signal", () => {
  assert.deepEqual(Object.keys(DEVICE_STATE_VISUALS).sort(), [
    "MAINTENANCE",
    "OFFLINE",
    "ONLINE",
    "STALE",
    "UNKNOWN",
    "WARNING",
  ]);
  for (const visual of Object.values(DEVICE_STATE_VISUALS)) {
    assert.ok(visual.label.length > 0, visual.state);
    assert.ok(visual.color.startsWith("var(--"), visual.state);
    assert.ok(visual.marker.length > 0, visual.state);
    assert.ok(visual.description.length > 0, visual.state);
  }
});

test("all five link states exist and are visually distinguishable", () => {
  assert.deepEqual(Object.keys(LINK_STATE_VISUALS).sort(), [
    "DEGRADED",
    "DOWN",
    "STALE",
    "UNKNOWN",
    "UP",
  ]);
  assert.notEqual(LINK_STATE_VISUALS.UP.marker, LINK_STATE_VISUALS.DOWN.marker);
  assert.notEqual(LINK_STATE_VISUALS.UP.dash, LINK_STATE_VISUALS.DOWN.dash);
  assert.ok(LINK_STATE_VISUALS.DOWN.dash.length > 0);
});

test("online is visibly green", () => {
  assert.equal(deviceState({ operationalState: "up", freshnessState: "fresh" }), "ONLINE");
  assert.equal(DEVICE_STATE_VISUALS.ONLINE.color, "var(--green, #22c55e)");
});

test("warning is amber from degradation or a bad health bucket", () => {
  assert.equal(deviceState({ operationalState: "degraded" }), "WARNING");
  assert.equal(deviceState({ operationalState: "up", health: "critical" }), "WARNING");
  assert.equal(deviceState({ operationalState: "up", health: "warning" }), "WARNING");
  assert.equal(DEVICE_STATE_VISUALS.WARNING.color, "var(--amber, #eab308)");
  // A reachable device with a critical health bucket is NOT reported as offline.
  assert.notEqual(deviceState({ operationalState: "up", health: "critical" }), "OFFLINE");
});

test("offline is red from a stored down state", () => {
  assert.equal(deviceState({ operationalState: "down" }), "OFFLINE");
  assert.equal(DEVICE_STATE_VISUALS.OFFLINE.color, "var(--red, #ef4444)");
});

test("unknown is grey when nothing has been established", () => {
  assert.equal(deviceState({ operationalState: "unknown" }), "UNKNOWN");
  assert.equal(deviceState({}), "UNKNOWN");
  assert.equal(DEVICE_STATE_VISUALS.UNKNOWN.color, "var(--muted, #64748b)");
});

test("stale is muted and requires old telemetry", () => {
  assert.equal(
    deviceState({ operationalState: "unknown", freshnessState: "stale" }),
    "STALE"
  );
  assert.equal(
    deviceState({ operationalState: "unknown", freshnessState: "expired" }),
    "STALE"
  );
  assert.equal(DEVICE_STATE_VISUALS.STALE.color, "var(--muted, #64748b)");
  assert.ok(DEVICE_STATE_VISUALS.STALE.dash.length > 0);
});

test("never-collected telemetry is NOT stale", () => {
  // "No telemetry has ever arrived" is a different fact from "the data is old".
  assert.equal(
    deviceState({ operationalState: "unknown", freshnessState: "never_collected" }),
    "UNKNOWN"
  );
  assert.equal(
    deviceState({ operationalState: "up", freshnessState: "never_collected" }),
    "ONLINE"
  );
});

test("maintenance wins over an outage or degradation", () => {
  assert.equal(
    deviceState({ operationalState: "down", lifecycleState: "maintenance" }),
    "MAINTENANCE"
  );
  assert.equal(deviceState({ operationalState: "up", health: "maintenance" }), "MAINTENANCE");
});

test("a recorded outage outranks stale telemetry", () => {
  assert.equal(deviceState({ operationalState: "down", freshnessState: "expired" }), "OFFLINE");
});

test("stale telemetry outranks an asserted online state", () => {
  // An old reading cannot support a claim of health.
  assert.equal(deviceState({ operationalState: "up", freshnessState: "expired" }), "STALE");
});

test("link up requires the relationship to say active", () => {
  assert.equal(linkState({ statusRaw: "active" }), "UP");
  assert.equal(linkState({ statusRaw: "unknown" }), "UNKNOWN");
  assert.equal(linkState({}), "UNKNOWN");
});

test("link down comes from a failed relationship or a down endpoint", () => {
  assert.equal(linkState({ statusRaw: "failed" }), "DOWN");
  assert.equal(linkState({ statusRaw: "active", localOperStatus: "down" }), "DOWN");
  assert.equal(linkState({ statusRaw: "active", remoteOperStatus: "DOWN" }), "DOWN");
});

test("link degraded is its own state", () => {
  assert.equal(linkState({ statusRaw: "degraded" }), "DEGRADED");
});

test("link stale needs stale telemetry, not merely absent telemetry", () => {
  assert.equal(linkState({ statusRaw: "active", freshnessState: "expired" }), "STALE");
  // A relationship with no interface telemetry is still UP when it says active:
  // the relationship is the evidence, the interfaces are detail.
  assert.equal(
    linkState({ statusRaw: "active", freshnessState: "never_collected" }),
    "UP"
  );
});

test("a recorded link failure outranks stale telemetry", () => {
  assert.equal(linkState({ statusRaw: "failed", freshnessState: "expired" }), "DOWN");
});

test("freshness helpers classify only current states as current", () => {
  assert.equal(isFreshnessCurrent("fresh"), true);
  assert.equal(isFreshnessCurrent("aging"), true);
  assert.equal(isFreshnessCurrent("stale"), false);
  assert.equal(isFreshnessCurrent("expired"), false);
  assert.equal(isFreshnessCurrent(null), false);
  assert.equal(isNeverCollected("never_collected"), true);
  assert.equal(isNeverCollected("unavailable"), true);
  assert.equal(isNeverCollected("fresh"), false);
});

test("freshness labels never invent a value", () => {
  assert.equal(freshnessLabel("never_collected"), "No telemetry collected");
  assert.equal(freshnessLabel("expired"), "Expired");
  assert.equal(freshnessLabel("something_new"), "something_new");
  assert.equal(freshnessLabel(null), "Unknown");
});