/** Recovery confirmation text. */
import assert from "node:assert/strict";
import test from "node:test";

import { recoveryConfirmText, unmetChecks, type RecoveryCheck } from "../../src/lib/incidents/recovery.ts";

const base: RecoveryCheck = {
  incident_id: "i1",
  since: "2026-10-30T10:00:00Z",
  evaluated_at: "2026-10-30T10:30:00Z",
  verdict: "recovered",
  checks: [
    { kind: "alert", id: "a", name: "Ping down", status: "resolved", observed_at: null, result: "pass", detail: "alert resolved" },
  ],
};

test("verified recovery needs no confirmation", () => {
  assert.equal(recoveryConfirmText(base), null);
});

test("failures are listed before inconclusive checks", () => {
  const check: RecoveryCheck = {
    ...base,
    verdict: "not_recovered",
    checks: [
      { kind: "device", id: "d", name: "sw1", status: "up", observed_at: null, result: "inconclusive", detail: "device has never reported" },
      { kind: "sensor", id: "s", name: "Ping", status: "down", observed_at: null, result: "fail", detail: "sensor is down" },
    ],
  };
  assert.deepEqual(unmetChecks(check), [
    "Failing: sensor Ping (sensor is down)",
    "No fresh evidence: device sw1 (device has never reported)",
  ]);
  assert.match(recoveryConfirmText(check) ?? "", /^Telemetry does not show recovery\./);
});

test("an incident without linked evidence still asks", () => {
  const text = recoveryConfirmText({ ...base, verdict: "inconclusive", checks: [] });
  assert.match(text ?? "", /No alerts, sensors or devices are linked/);
});
