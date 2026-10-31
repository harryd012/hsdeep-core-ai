import { test } from "node:test";
import assert from "node:assert/strict";

import { gatewayEntries, groupByCategory, GATEWAY_FRESHNESS_MS } from "../src/lib/gateway.ts";

const NOW = Date.parse("2026-10-30T12:00:00Z");
const type = (source_type, category, implemented = true) => ({
  source_type, display_name: source_type.toUpperCase(), implemented, category, firewall_capabilities: [],
});

test("connected needs a fresh successful poll; configured-but-silent is stale", () => {
  const entries = gatewayEntries(
    [type("fortigate", "firewall"), type("paloalto", "firewall"), type("prtg", "monitoring"), type("wip", "other", false)],
    [
      { source_type: "fortigate", is_active: true, last_successful_run_at: new Date(NOW - 60_000).toISOString() },
      { source_type: "paloalto", is_active: true, last_successful_run_at: new Date(NOW - GATEWAY_FRESHNESS_MS - 1).toISOString() },
      { source_type: "prtg", is_active: false, last_successful_run_at: new Date(NOW).toISOString() },
    ],
    NOW,
  );
  assert.deepEqual(entries.map((e) => [e.source_type, e.state]), [
    ["fortigate", "connected"], ["paloalto", "stale"], ["prtg", "available"],
  ]);
});

test("a source that never succeeded is stale, not connected", () => {
  const [e] = gatewayEntries([type("snmp", "network")], [{ source_type: "snmp", is_active: true, last_successful_run_at: null }], NOW);
  assert.equal(e.state, "stale");
  assert.equal(e.lastSuccessAt, null);
});

test("groups follow the category order", () => {
  const groups = groupByCategory(gatewayEntries([type("aws", "cloud"), type("fortigate", "firewall"), type("snmp", "network")], [], NOW));
  assert.deepEqual(groups.map((g) => g.label), ["Firewalls", "Network", "Cloud & virtualization"]);
});
