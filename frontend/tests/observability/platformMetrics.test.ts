/**
 * platformMetrics.test.ts - formatting and health tones for the live
 * Platform Observability figures.
 *
 * Run with:  node --test tests/observability/platformMetrics.test.ts
 *
 * Locks the truth rule: an unobserved value (null) renders as the kit's
 * not-measured marker with a neutral tone, never as 0 or as healthy.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  NOT_MEASURED,
  THRESHOLDS,
  averagePerMinute,
  formatRatio,
  formatSeconds,
  formatUptime,
  toneHigherWorse,
  toneLowerWorse,
} from "../../src/lib/observability/platformMetrics.ts";

test("null and non-finite values are not measured, never zero", () => {
  assert.equal(formatSeconds(null), NOT_MEASURED);
  assert.equal(formatSeconds(undefined), NOT_MEASURED);
  assert.equal(formatSeconds(Number.NaN), NOT_MEASURED);
  assert.equal(formatRatio(null), NOT_MEASURED);
  assert.equal(toneHigherWorse(null, 1, 2), "neutral");
  assert.equal(toneLowerWorse(null, 0.95, 0.8), "neutral");
  assert.equal(averagePerMinute(0, 3600), null);
  assert.equal(averagePerMinute(100, 30), null, "under a minute of uptime is not a rate");
});

test("durations, ratios and uptime format for people", () => {
  assert.equal(formatSeconds(0.0123), "12 ms");
  assert.equal(formatSeconds(2.5), "2.50 s");
  assert.equal(formatSeconds(90), "1.5 min");
  assert.equal(formatRatio(0.0042), "0.42%");
  assert.equal(formatRatio(0.75, 1), "75.0%");
  assert.equal(formatUptime(45), "45 s");
  assert.equal(formatUptime(3723), "1 h 2 min");
  assert.equal(formatUptime(3 * 86400 + 7200), "3 d 2 h");
  assert.equal(averagePerMinute(600, 600), 60);
});

test("tones follow the alert thresholds", () => {
  assert.equal(toneHigherWorse(0.001, THRESHOLDS.errorRatioWarn, THRESHOLDS.errorRatioBad), "good");
  assert.equal(toneHigherWorse(0.02, THRESHOLDS.errorRatioWarn, THRESHOLDS.errorRatioBad), "warn");
  assert.equal(toneHigherWorse(0.05, THRESHOLDS.errorRatioWarn, THRESHOLDS.errorRatioBad), "bad");
  assert.equal(toneLowerWorse(0.99, THRESHOLDS.collectionSuccessWarn, THRESHOLDS.collectionSuccessBad), "good");
  assert.equal(toneLowerWorse(0.9, THRESHOLDS.collectionSuccessWarn, THRESHOLDS.collectionSuccessBad), "warn");
  assert.equal(toneLowerWorse(0.5, THRESHOLDS.collectionSuccessWarn, THRESHOLDS.collectionSuccessBad), "bad");
});
