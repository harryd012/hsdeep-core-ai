import { test } from "node:test";
import assert from "node:assert/strict";

import { evidenceLabel, haLabel, licenceLabel, sessionsText, tunnelsText } from "../src/lib/firewalls.ts";

const base = {
  evidence: "fresh", ha_mode: "active-passive", ha_sync: "in_sync", ha_role: "active",
  session_count: 950, session_limit: 1000, licences: [{ status: "valid" }], tunnels_up: 1, tunnels_total: 2,
};

test("a firewall with no reported state reads No data everywhere", () => {
  const none = { ...base, evidence: "none", ha_mode: null, ha_sync: null, session_count: null, session_limit: null,
    licences: null, tunnels_up: null, tunnels_total: null };
  assert.equal(evidenceLabel(none).text, "No data");
  assert.equal(haLabel(none).text, "No data");
  assert.equal(sessionsText(none), "No data");
  assert.equal(licenceLabel(none).text, "No data");
  assert.equal(tunnelsText(none), "No data");
});

test("HA states", () => {
  assert.deepEqual(haLabel(base), { tone: "good", text: "In sync · active" });
  assert.deepEqual(haLabel({ ...base, ha_sync: "out_of_sync" }).tone, "bad");
  assert.equal(haLabel({ ...base, ha_mode: "standalone" }).text, "Standalone");
  assert.equal(haLabel({ ...base, ha_sync: null, ha_role: null }).text, "Sync not reported");
});

test("sessions, licences and tunnels", () => {
  assert.equal(sessionsText(base), "950 / 1,000 (95%)");
  assert.equal(sessionsText({ ...base, session_limit: null }), "950");
  assert.deepEqual(licenceLabel({ ...base, licences: [{ status: "valid" }, { status: "expired" }] }), { tone: "bad", text: "1 expired" });
  assert.equal(licenceLabel({ ...base, licences: [{ status: "unknown" }] }).text, "No data");
  assert.equal(tunnelsText(base), "1 of 2 up");
  assert.equal(tunnelsText({ ...base, tunnels_total: 0, tunnels_up: 0 }), "None configured");
});
