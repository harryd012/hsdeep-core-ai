import { test } from "node:test";
import assert from "node:assert/strict";

import {
  commandsFor,
  currentVersion,
  isSocPlaybook,
  matchesPlaybook,
  normalizeSteps,
  platformLabel,
  platformsOf,
} from "../src/lib/socPlaybooks.ts";

const raw = [
  {
    order: 2,
    action: "Check sessions",
    expected_result: "Session found",
    warnings: ["Filter first", ""],
    troubleshooting_matrix: { no_session: "Traffic not arriving", empty: "" },
    commands: [
      { platform: "fortios", command: "diagnose sys session list" },
      { platform: "panos", command: "show session all" },
      { platform: "fortios", command: "diagnose sys session clear", disruptive: true },
      { platform: "any", command: "Record the session ID" },
      { platform: "fortios", command: "" },
    ],
  },
  { order: 1, action: "Scope", troubleshooting_matrix: {} },
  { order: 3 },
];

test("only active SOPs tagged soc are playbooks", () => {
  assert.equal(isSocPlaybook({ tags: ["firewall", "SOC"] }), true);
  assert.equal(isSocPlaybook({ tags: ["firewall"] }), false);
  assert.equal(isSocPlaybook({ tags: ["soc"], is_active: false }), false);
});

test("current version wins, else the highest version number", () => {
  const vs = [{ id: "a", version_number: 1, steps: [] }, { id: "b", version_number: 3, steps: [] }, { id: "c", version_number: 2, steps: [] }];
  assert.equal(currentVersion("c", vs).id, "c");
  assert.equal(currentVersion(null, vs).id, "b");
  assert.equal(currentVersion("zzz", vs).id, "b");
  assert.equal(currentVersion(null, []), undefined);
});

test("steps are normalised, ordered, and malformed parts dropped", () => {
  const steps = normalizeSteps(raw);
  assert.deepEqual(steps.map((s) => s.order), [1, 2]); // the step without an action is dropped
  const s = steps[1];
  assert.equal(s.expectedResult, "Session found");
  assert.deepEqual(s.warnings, ["Filter first"]);
  assert.deepEqual(s.matrix, [{ condition: "no session", meaning: "Traffic not arriving" }]);
  assert.equal(s.commands.length, 4);
  assert.equal(s.commands.filter((c) => c.disruptive).length, 1);
  assert.equal(steps[0].expectedResult, null);
  assert.deepEqual(normalizeSteps(undefined), []);
});

test("platform filter keeps vendor-neutral commands", () => {
  const [, step] = normalizeSteps(raw);
  assert.deepEqual(platformsOf([step]), ["fortios", "any", "panos"]);
  assert.deepEqual(commandsFor(step, "panos").map((c) => c.command), ["show session all", "Record the session ID"]);
  assert.equal(commandsFor(step, "all").length, 4);
  assert.equal(commandsFor(step, "sophos").length, 1);
});

test("search covers number, name, vendor and tags", () => {
  const sop = { sop_number: "SOP-022", name: "FortiGate Scenarios", description: null, tags: ["vpn", "ha"], vendor: "Fortinet" };
  assert.equal(matchesPlaybook(sop, ""), true);
  assert.equal(matchesPlaybook(sop, "sop-022"), true);
  assert.equal(matchesPlaybook(sop, "fortinet"), true);
  assert.equal(matchesPlaybook(sop, "VPN"), true);
  assert.equal(matchesPlaybook(sop, "palo"), false);
});

test("platform labels", () => {
  assert.equal(platformLabel("fortios"), "FortiGate (FortiOS)");
  assert.equal(platformLabel("some_new_os"), "Some New Os");
});
