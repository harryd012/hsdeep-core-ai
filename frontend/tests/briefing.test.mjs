import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBriefing } from "../src/lib/commandCenter/briefing.ts";

const healthyDevices = { total: 100, healthy: 95, warning: 3, critical: 1, down: 1, unknown: 0 };
const healthyCollectors = { total: 5, healthy: 5, degraded: 0, failed: 0, stale: 0, unknown: 0 };

test("no inputs -> no score, never a fabricated 100", () => {
  const b = buildBriefing({});
  assert.equal(b.score, null);
  assert.equal(b.grade, "No data");
  assert.equal(b.findings.length, 0);
});

test("a clean estate scores 100 with no findings", () => {
  const b = buildBriefing({
    devices: healthyDevices,
    collectors: healthyCollectors,
    incidents: { open: 0, critical: 0, opened24h: 2, resolved24h: 2 },
    sites: { total: 4, unassignedDevices: 0 },
    alerts: { open: 0, critical: 0 },
  });
  assert.equal(b.score, 100);
  assert.equal(b.grade, "Healthy");
  assert.equal(b.rulesEvaluated, 5);
});

test("dev-tenant shape: blind collection reframes the mass outage as a data problem", () => {
  const b = buildBriefing({
    devices: { total: 269, healthy: 13, warning: 0, critical: 0, down: 255, unknown: 1 },
    collectors: { total: 5, healthy: 0, degraded: 0, failed: 5, stale: 0, unknown: 0 },
    sourceErrors: [{ name: "Dev PRTG", error: "Could not reach PRTG" }],
    incidents: { open: 2670401, critical: 1081591, opened24h: 0, resolved24h: 0 },
    sites: { total: 0, unassignedDevices: 269 },
    alerts: { open: 52, critical: 360 },
  });
  const ids = b.findings.map((f) => f.id);
  assert.equal(ids[0], "collection", "the root cause ranks first");
  const outage = b.findings.find((f) => f.id === "estate-down");
  assert.equal(outage.severity, "warning");
  assert.match(outage.title, /collection artefact/);
  assert.equal(outage.action.href, "/monitoring");
  assert.ok(b.findings.find((f) => f.id === "backlog"), "stagnant backlog is called out");
  assert.ok(b.findings.find((f) => f.id === "collection").evidence.some((e) => e.includes("Could not reach PRTG")));
  // Every lost point is attributable to a finding.
  assert.equal(b.score, Math.max(0, 100 - b.findings.reduce((s, f) => s + f.weight, 0)));
  assert.ok(["Critical", "Degraded"].includes(b.grade));
});

test("a real outage (collection healthy) stays critical and points at hosts", () => {
  const b = buildBriefing({
    devices: { total: 10, healthy: 2, warning: 0, critical: 3, down: 5, unknown: 0 },
    collectors: healthyCollectors,
  });
  const outage = b.findings.find((f) => f.id === "estate-down");
  assert.equal(outage.severity, "critical");
  assert.match(outage.action.href, /\/infrastructure\/hosts/);
});

test("critical incidents with activity are flagged instead of the stagnant rule", () => {
  const b = buildBriefing({ incidents: { open: 12, critical: 3, opened24h: 4, resolved24h: 1 } });
  assert.deepEqual(b.findings.map((f) => f.id), ["critical-incidents"]);
});

test("findings are ordered critical, warning, info", () => {
  const b = buildBriefing({
    collectors: { total: 4, healthy: 3, degraded: 1, failed: 0, stale: 0, unknown: 0 },
    incidents: { open: 5, critical: 2, opened24h: 1, resolved24h: 0 },
    alerts: { open: 3, critical: 0 },
  });
  const rank = { critical: 0, warning: 1, info: 2 };
  const order = b.findings.map((f) => rank[f.severity]);
  assert.deepEqual(order, [...order].sort((a, c) => a - c));
});
