import { test } from "node:test";
import assert from "node:assert/strict";

import {
  formatHealthValue,
  healthBreakdown,
  resolveKpi,
} from "../src/lib/cloudOperations/format.ts";

// --- formatHealthValue: genuine zero vs null vs value ----------------------
test("formatHealthValue shows online/total and preserves zero total", () => {
  assert.equal(formatHealthValue({ online: 5, total: 10 }), "5/10 online");
  assert.equal(formatHealthValue({ online: 0, total: 0 }), "0");
  assert.equal(formatHealthValue(null), "N/A");
  assert.equal(formatHealthValue(undefined), "N/A");
});

// --- healthBreakdown: unknown/stale/offline buckets ------------------------
test("healthBreakdown surfaces offline/stale/unknown but omits zero buckets", () => {
  assert.equal(
    healthBreakdown({ online: 5, offline: 1, stale: 3, unknown: 1, total: 10 }),
    "5 online · 1 offline · 3 stale · 1 unknown"
  );
  assert.equal(healthBreakdown({ online: 5, total: 5 }), "5 online");
});

// --- resolveKpi: unavailable vs zero vs value vs no-data -------------------
test("resolveKpi marks an unavailable card with null value and its reason", () => {
  const r = resolveKpi({
    value: null,
    unavailable: true,
    unavailable_reason: "billing integration not configured (no CostManagementClient)",
    detail: null,
  });
  assert.deepEqual(r, {
    value: null,
    unavailable: true,
    note: "billing integration not configured (no CostManagementClient)",
  });
});

test("resolveKpi keeps a genuine zero distinct from unavailable", () => {
  const r = resolveKpi({
    value: 0,
    unavailable: false,
    unavailable_reason: null,
    detail: null,
  });
  assert.equal(r.value, 0);
  assert.equal(r.unavailable, false);
  assert.equal(r.note, undefined);
});

test("resolveKpi renders a measured number", () => {
  const r = resolveKpi({
    value: 42,
    unavailable: false,
    unavailable_reason: null,
    detail: null,
  });
  assert.equal(r.value, 42);
  assert.equal(r.unavailable, false);
});

test("resolveKpi renders a null non-unavailable value as no-data", () => {
  const r = resolveKpi({
    value: null,
    unavailable: false,
    unavailable_reason: null,
    detail: null,
  });
  assert.equal(r.value, null);
  assert.equal(r.unavailable, false);
  assert.equal(r.note, "No data reported.");
});

test("resolveKpi formats a health object and its breakdown", () => {
  const r = resolveKpi({
    value: { online: 5, offline: 1, stale: 2, unknown: 1, total: 9 },
    unavailable: false,
    unavailable_reason: null,
    detail: null,
  });
  assert.equal(r.value, "5/9 online");
  assert.equal(r.unavailable, false);
  assert.equal(r.note, "5 online · 1 offline · 2 stale · 1 unknown");
});

test("resolveKpi surfaces incident detail as a note", () => {
  const r = resolveKpi({
    value: 12,
    unavailable: false,
    unavailable_reason: null,
    detail: { total: 12, critical: 3 },
  });
  assert.equal(r.value, 12);
  assert.equal(r.note, "12 total · 3 critical");
});

test("cloud operations payload has exactly the six cards", () => {
  const cards = [
    "cloud_resources",
    "resource_health",
    "active_incidents",
    "cloud_spend",
    "policy_compliance",
    "backup_readiness",
  ];
  assert.equal(cards.length, 6);
  assert.ok(cards.includes("cloud_resources"));
  assert.ok(cards.includes("backup_readiness"));
});
