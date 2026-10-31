import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import {
  NOT_MEASURED,
  computeDelta,
  formatAge,
  formatCount,
  formatKpiCount,
  formatMinutes,
  formatPct,
  isStale,
  parseRange,
  parseSeverity,
  pct,
  rangeDays,
} from "../src/lib/commandCenter/format.ts";

test("null is not measured, zero is a real zero", () => {
  assert.equal(formatCount(null), NOT_MEASURED);
  assert.equal(formatCount(undefined), NOT_MEASURED);
  assert.equal(formatCount(0), "0");
  assert.equal(formatCount(1248), "1,248");
  assert.equal(formatMinutes(null), NOT_MEASURED);
  assert.equal(formatMinutes(0), "0 min");
  assert.equal(formatPct(null), NOT_MEASURED);
  assert.equal(formatPct(0), "0%");
});

test("KPI counts stay exact below 100k and go compact above", () => {
  assert.equal(formatKpiCount(99_999), "99,999");
  assert.equal(formatKpiCount(2_670_401), "2.67M");
  assert.equal(formatKpiCount(1_081_591), "1.08M");
  assert.equal(formatKpiCount(0), "0");
  assert.equal(formatKpiCount(null), NOT_MEASURED);
});

test("pct has no denominator -> null, never 0% or 100%", () => {
  assert.equal(pct(0, 0), null);
  assert.equal(pct(48, 50), 96);
  assert.equal(pct(1, 3), 33.3);
});

test("minutes scale to hours and days", () => {
  assert.equal(formatMinutes(14.2), "14 min");
  assert.equal(formatMinutes(168), "2.8 hrs");
  assert.equal(formatMinutes(60 * 30), "30 hrs");
  assert.equal(formatMinutes(60 * 24 * 3), "3.0 days");
});

test("delta tone encodes good/bad, not direction", () => {
  assert.deepEqual(computeDelta(28, 21, "lower-is-better"), { label: "33%", direction: "up", tone: "bad" });
  assert.deepEqual(computeDelta(10, 14, "lower-is-better"), { label: "29%", direction: "down", tone: "good" });
  assert.deepEqual(computeDelta(1086, 1000, "higher-is-better"), { label: "8.6%", direction: "up", tone: "good" });
  assert.deepEqual(computeDelta(5, 5, "lower-is-better"), { label: "0%", direction: "flat", tone: "neutral" });
});

test("delta needs both windows; zero baseline is an absolute change", () => {
  assert.equal(computeDelta(null, 3, "lower-is-better"), null);
  assert.equal(computeDelta(3, null, "lower-is-better"), null);
  assert.equal(computeDelta(3, undefined, "lower-is-better"), null);
  assert.deepEqual(computeDelta(3, 0, "lower-is-better"), { label: "+3", direction: "up", tone: "bad" });
});

test("URL filters fall back to defaults on invalid input", () => {
  assert.equal(parseRange("30d"), "30d");
  assert.equal(parseRange("999d"), "7d");
  assert.equal(parseRange(null), "7d");
  assert.equal(rangeDays("14d"), 14);
  assert.equal(parseSeverity("critical"), "critical");
  assert.equal(parseSeverity("high"), null); // not in the backend vocabulary
  assert.equal(parseSeverity("'; drop"), null);
});

test("age and staleness", () => {
  const now = Date.parse("2026-10-29T12:00:00Z");
  assert.equal(formatAge("2026-10-29T11:48:00Z", now), "12 min");
  assert.equal(formatAge("2026-10-29T08:44:00Z", now), "3 h 16 min");
  assert.equal(formatAge("2026-10-27T08:00:00Z", now), "2 d 4 h");
  assert.equal(formatAge(null, now), NOT_MEASURED);
  assert.equal(isStale(now - 60_000, now, 60_000), false);
  assert.equal(isStale(now - 121_000, now, 60_000), true);
  assert.equal(isStale(null, now, 60_000), false);
});

test("command-center components contain no placeholder or mock values", () => {
  const dir = new URL("../src/components/command-center/", import.meta.url);
  for (const file of readdirSync(dir)) {
    const src = readFileSync(join(dir.pathname.replace(/^\/([A-Za-z]:)/, "$1"), file), "utf8");
    assert.ok(!/API data/i.test(src), `${file} contains "API data"`);
    assert.ok(!/\b(mock|dummy|lorem|fake)\b/i.test(src), `${file} references mock data`);
  }
});
