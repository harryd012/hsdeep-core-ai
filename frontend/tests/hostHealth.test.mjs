import { test } from "node:test";
import assert from "node:assert/strict";

import { buildHostVerdict, downsample, sensorFill, sensorState, seriesStats } from "../src/lib/hostHealth.ts";

const NOW = Date.parse("2026-10-29T12:00:00Z");
const fresh = new Date(NOW - 5 * 60_000).toISOString();
const sensor = (over = {}) => ({
  id: "s1", name: "CPU", status: "up", unit: "%", current_value: 20,
  warning_threshold: 80, critical_threshold: 95, last_value_at: fresh, ...over,
});

test("no device -> neutral, never healthy", () => {
  assert.equal(buildHostVerdict({ device: null }).tone, "neutral");
});

test("up host with no telemetry is neutral, not healthy", () => {
  const v = buildHostVerdict({ device: { status: "up", last_seen_at: fresh }, sensors: [], alerts: [], now: NOW });
  assert.equal(v.tone, "neutral");
});

test("up host with sensors inside limits is healthy", () => {
  const v = buildHostVerdict({ device: { status: "up", last_seen_at: fresh }, sensors: [sensor()], alerts: [], now: NOW });
  assert.equal(v.tone, "good");
  assert.match(v.headline, /all 1 sensor within limits/);
});

test("down host leads with the down reason and goes bad", () => {
  const v = buildHostVerdict({
    device: { status: "down", last_seen_at: new Date(NOW - 2 * 3_600_000).toISOString() },
    sensors: [sensor()],
    now: NOW,
  });
  assert.equal(v.tone, "bad");
  assert.equal(v.headline, "Host is down");
  assert.equal(v.reasons[0].id, "down");
});

test("a host out of contact leads with staleness, whatever else is wrong", () => {
  const v = buildHostVerdict({
    device: { status: "up", last_seen_at: new Date(NOW - 31 * 86_400_000).toISOString() },
    alerts: [{ title: "Ping", severity: "critical", status: "open", occurrence_count: 3, last_occurred_at: fresh }],
    now: NOW,
  });
  assert.equal(v.tone, "bad", "open critical alerts still set the tone");
  assert.match(v.headline, /^Data is 31 d old/);
  assert.equal(v.reasons[0].id, "stale-host");
});

test("open critical alerts and a flapping alert are both explained", () => {
  const v = buildHostVerdict({
    device: { status: "up", last_seen_at: fresh },
    alerts: [
      { title: "Ping down", severity: "critical", status: "open", occurrence_count: 400, last_occurred_at: fresh },
      { title: "Old", severity: "critical", status: "resolved", occurrence_count: 1, last_occurred_at: fresh },
    ],
    now: NOW,
  });
  assert.equal(v.tone, "bad");
  const alerts = v.reasons.find((r) => r.id === "alerts");
  assert.match(alerts.text, /^1 critical alert open/);
  assert.ok(v.reasons.find((r) => r.id === "flapping"));
});

test("sensor threshold direction is inferred from the thresholds", () => {
  assert.equal(sensorState(sensor({ current_value: 96 })), "critical");
  assert.equal(sensorState(sensor({ current_value: 85 })), "warning");
  // Lower is worse: free space with warning 20, critical 10.
  const free = sensor({ current_value: 8, warning_threshold: 20, critical_threshold: 10 });
  assert.equal(sensorState(free), "critical");
  assert.equal(sensorState({ ...free, current_value: 50 }), "ok");
  assert.equal(sensorState(sensor({ current_value: null, warning_threshold: null, critical_threshold: null, status: "down" })), "critical");
  assert.equal(sensorState(sensor({ current_value: null, warning_threshold: null, critical_threshold: null, status: "paused" })), "unknown");
});

test("sensorFill is clamped and null without a limit", () => {
  assert.equal(sensorFill(sensor({ current_value: 190 })), 1);
  assert.equal(sensorFill(sensor({ warning_threshold: null, critical_threshold: null })), null);
});

test("enabled-but-down interfaces and stale sensors are flagged", () => {
  const v = buildHostVerdict({
    device: { status: "up", last_seen_at: fresh },
    sensors: [sensor({ last_value_at: new Date(NOW - 3 * 3_600_000).toISOString() })],
    interfaces: [{ name: "eth0", admin_status: "up", oper_status: "down" }],
    now: NOW,
  });
  assert.ok(v.reasons.find((r) => r.id === "interfaces"));
  assert.ok(v.reasons.find((r) => r.id === "stale-sensors"));
  assert.equal(v.tone, "warn");
});

test("seriesStats and downsample keep spikes", () => {
  assert.equal(seriesStats([]), null);
  assert.deepEqual(seriesStats([1, 3, 2]), { latest: 2, min: 1, max: 3, avg: 2, count: 3 });
  const pts = Array.from({ length: 1000 }, (_, i) => ({ t: i, v: i === 500 ? 99 : 1 }));
  const ds = downsample(pts, 100);
  assert.equal(ds.length, 100);
  assert.ok(ds.some((p) => p.v === 99), "the spike survives");
});
