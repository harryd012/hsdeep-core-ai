/** Traffic formatting, direction and animation-gating tests (Phase 9.5). */
import assert from "node:assert/strict";
import test from "node:test";

import {
  animationBudget,
  formatBps,
  formatPercent,
  shouldAnimate,
  trafficDirection,
  utilizationBand,
} from "../../src/lib/topology/traffic.ts";

test("throughput picks a unit instead of hardcoding Mbps", () => {
  assert.equal(formatBps(0), "0 bps");
  assert.equal(formatBps(950), "950 bps");
  assert.equal(formatBps(320_000), "320 Kbps");
  assert.equal(formatBps(210_000_000), "210 Mbps");
  assert.equal(formatBps(650_000_000), "650 Mbps");
  assert.equal(formatBps(1_200_000_000), "1.2 Gbps");
  assert.equal(formatBps(2_500_000_000_000), "2.5 Tbps");
});

test("a missing reading is null, never zero", () => {
  assert.equal(formatBps(null), null);
  assert.equal(formatBps(undefined), null);
  assert.equal(formatBps(Number.NaN), null);
  assert.equal(formatPercent(null), null);
  assert.equal(utilizationBand(null), null);
  assert.equal(utilizationBand(undefined), null);
});

test("percentages are clamped and trimmed", () => {
  assert.equal(formatPercent(69.4), "69.4%");
  assert.equal(formatPercent(120), "100%");
  assert.equal(formatPercent(-5), "0%");
});

test("utilisation bands follow the documented thresholds", () => {
  assert.equal(utilizationBand(0), "normal");
  assert.equal(utilizationBand(49.9), "normal");
  assert.equal(utilizationBand(50), "moderate");
  assert.equal(utilizationBand(79), "moderate");
  assert.equal(utilizationBand(80), "high");
  assert.equal(utilizationBand(89.9), "high");
  assert.equal(utilizationBand(90), "critical");
  assert.equal(utilizationBand(100), "critical");
});

test("direction is evidenced by counters, not assumed", () => {
  assert.equal(trafficDirection({}), "none");
  assert.equal(trafficDirection({ localTxBps: 320_000_000 }), "forward");
  assert.equal(trafficDirection({ remoteRxBps: 320_000_000 }), "forward");
  assert.equal(trafficDirection({ remoteTxBps: 210_000_000 }), "reverse");
  assert.equal(trafficDirection({ localRxBps: 210_000_000 }), "reverse");
  assert.equal(trafficDirection({ localTxBps: 1, remoteTxBps: 1 }), "bidirectional");
  assert.equal(trafficDirection({ localRxBps: 0, localTxBps: 0 }), "none");
});

test("animation requires measured traffic and current telemetry", () => {
  const base = {
    animateEnabled: true,
    reducedMotion: false,
    budget: 5,
    freshnessCurrent: true,
  };
  assert.equal(shouldAnimate({ ...base, bps: 1_000_000 }), true);
  assert.equal(shouldAnimate({ ...base, bps: 0 }), false);
  assert.equal(shouldAnimate({ ...base, bps: null }), false);
  assert.equal(shouldAnimate({ ...base, bps: undefined }), false);
  assert.equal(shouldAnimate({ ...base, bps: Number.NaN }), false);
  assert.equal(shouldAnimate({ ...base, bps: 1, freshnessCurrent: false }), false);
  assert.equal(shouldAnimate({ ...base, bps: 1, reducedMotion: true }), false);
  assert.equal(shouldAnimate({ ...base, bps: 1, animateEnabled: false }), false);
  assert.equal(shouldAnimate({ ...base, bps: 1, budget: 0 }), false);
  // Nothing enabled by default: a link never animates just because it exists.
  assert.equal(shouldAnimate({ bps: 1_000_000 }), false);
});

test("the animation budget is bounded and deterministic", () => {
  assert.equal(animationBudget(0), 0);
  assert.equal(animationBudget(-3), 0);
  assert.equal(animationBudget(4), 1);
  assert.equal(animationBudget(100), 25);
  assert.equal(animationBudget(10_000), 40);
  assert.equal(animationBudget(100), animationBudget(100));
});