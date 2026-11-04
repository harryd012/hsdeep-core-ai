import { test } from "node:test";
import assert from "node:assert/strict";

import { estimateRetention, horizonLabel } from "../src/lib/backupRetention.ts";

const none = { daily_days: null, weekly_weeks: null, monthly_months: null, yearly_years: null };

test("daily-only retention keeps one point per day", () => {
  assert.deepEqual(estimateRetention({ ...none, daily_days: 30 }), { horizon_days: 30, daily: 30, weekly: 0, monthly: 0, yearly: 0, total: 30 });
});

test("30 -> 90 day daily retention triples points and horizon", () => {
  const before = estimateRetention({ ...none, daily_days: 30 });
  const after = estimateRetention({ ...none, daily_days: 90 });
  assert.equal(after.total, before.total * 3);
  assert.equal(after.horizon_days, 90);
});

test("longer tiers only add points older than the shorter tiers keep", () => {
  // 30 daily covers the 4 newest weekly points; 12 weekly keeps 8 more.
  const e = estimateRetention({ daily_days: 30, weekly_weeks: 12, monthly_months: 12, yearly_years: 7 });
  assert.equal(e.daily, 30);
  assert.equal(e.weekly, 8);
  assert.equal(e.monthly, 10); // 12 weeks = 84 days covers the 2 newest monthly points
  assert.equal(e.yearly, 6); // 12 months covers the newest yearly point
  assert.equal(e.total, 54);
  assert.equal(e.horizon_days, 7 * 365);
});

test("a weekly schedule has no daily tier", () => {
  const e = estimateRetention({ daily_days: 30, weekly_weeks: 4, monthly_months: null, yearly_years: null }, "weekly");
  assert.equal(e.daily, 0);
  assert.equal(e.weekly, 4);
  assert.equal(e.horizon_days, 28);
});

test("horizon labels", () => {
  assert.equal(horizonLabel(30), "30 days");
  assert.equal(horizonLabel(365), "1 year");
  assert.equal(horizonLabel(7 * 365), "7 years");
  assert.equal(horizonLabel(0), "none");
});
