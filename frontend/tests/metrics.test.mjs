import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyDevice,
  countDeviceStates,
  collectorSuccessPct,
  collectorRuns24h,
  DEVICE_FRESHNESS_THRESHOLD_MS,
} from "../src/lib/dashboard/metrics.ts";

const NOW = Date.parse("2026-06-01T00:00:00Z");
const recent = new Date(NOW - 60_000).toISOString();
const old = new Date(NOW - DEVICE_FRESHNESS_THRESHOLD_MS - 60_000).toISOString();

test("classifyDevice distinguishes up/down/unknown/stale", () => {
  assert.equal(classifyDevice({ status: "up", last_seen_at: recent }, NOW), "up");
  assert.equal(classifyDevice({ status: "down", last_seen_at: recent }, NOW), "down");
  assert.equal(classifyDevice({ status: "warning", last_seen_at: recent }, NOW), "down");
  assert.equal(classifyDevice({ status: "down", last_seen_at: old }, NOW), "stale");
  assert.equal(classifyDevice({ status: "down", last_seen_at: null }, NOW), "unknown");
  assert.equal(classifyDevice({ status: "down", last_seen_at: "" }, NOW), "unknown");
  assert.equal(classifyDevice({ status: "unknown", last_seen_at: recent }, NOW), "unknown");
});

test("a stale device is never labelled down", () => {
  // Confirmed down with recent evidence => down; but the same status with no
  // recent evidence must NOT be "down".
  assert.equal(classifyDevice({ status: "down", last_seen_at: recent }, NOW), "down");
  assert.equal(classifyDevice({ status: "down", last_seen_at: old }, NOW), "stale");
});

test("countDeviceStates is internally consistent and total-preserving", () => {
  const devices = [
    { status: "up", last_seen_at: recent },
    { status: "down", last_seen_at: recent },
    { status: "down", last_seen_at: old },
    { status: "down", last_seen_at: null },
    { status: "offline", last_seen_at: recent },
  ];
  const c = countDeviceStates(devices, NOW);
  assert.deepEqual(c, { up: 1, down: 2, unknown: 1, stale: 1, total: 5 });
  assert.equal(c.up + c.down + c.unknown + c.stale, c.total);
});

test("collectorSuccessPct is null with zero runs, rounded otherwise", () => {
  assert.equal(collectorSuccessPct(undefined), null);
  assert.equal(collectorSuccessPct({ runs_last_24h: 0, success_rate_pct: 100 }), null);
  assert.equal(collectorSuccessPct({ runs_last_24h: 10, success_rate_pct: 87.6 }), 88);
});

test("collectorRuns24h returns the window size or null", () => {
  assert.equal(collectorRuns24h(undefined), null);
  assert.equal(collectorRuns24h({ runs_last_24h: 618 }), 618);
});

test("countDeviceStates matches backend counts_by_state for the same fixture", () => {
  // The SAME 18 rows are used in backend/tests/test_device_state_parity.py
  // (counts_by_state) so the backend aggregate and this selector cannot drift.
  const now = Date.parse("2026-06-01T00:00:00Z");
  const recent = new Date(now - 60_000).toISOString();
  const old = new Date(now - DEVICE_FRESHNESS_THRESHOLD_MS - 60_000).toISOString();
  const boundary = new Date(now - DEVICE_FRESHNESS_THRESHOLD_MS).toISOString();

  const rows = [
    // up
    { status: "up", last_seen_at: recent },
    { status: "online", last_seen_at: recent },
    { status: "UP", last_seen_at: recent },
    { status: "  up  ", last_seen_at: recent },
    { status: "up", last_seen_at: boundary },
    // down
    { status: "down", last_seen_at: recent },
    { status: "offline", last_seen_at: recent },
    { status: "critical", last_seen_at: recent },
    { status: "error", last_seen_at: recent },
    { status: "unreachable", last_seen_at: recent },
    { status: "degraded", last_seen_at: recent },
    { status: "warning", last_seen_at: recent },
    // stale
    { status: "up", last_seen_at: old },
    { status: "down", last_seen_at: old },
    // unknown
    { status: "up", last_seen_at: null },
    { status: "mystery", last_seen_at: recent },
    { status: "unknown", last_seen_at: recent },
    { status: "", last_seen_at: recent },
  ];

  const c = countDeviceStates(rows, now);
  assert.deepEqual(c, { up: 5, down: 7, stale: 2, unknown: 4, total: 18 });
});
