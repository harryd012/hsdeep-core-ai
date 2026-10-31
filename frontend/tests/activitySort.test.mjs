import { test } from "node:test";
import assert from "node:assert/strict";
import { toEpochMs, sortActivityItems } from "../src/lib/dashboard/activitySort.ts";

test("toEpochMs returns null for missing/null/invalid timestamps", () => {
  assert.equal(toEpochMs(undefined), null);
  assert.equal(toEpochMs(null), null);
  assert.equal(toEpochMs(""), null);
  assert.equal(toEpochMs("   "), null);
  assert.equal(toEpochMs("not-a-date"), null);
  assert.equal(toEpochMs({}), null);
  assert.equal(toEpochMs(NaN), null);
  assert.equal(toEpochMs(Infinity), null);
});

test("toEpochMs parses ISO strings, unix seconds and milliseconds", () => {
  assert.equal(toEpochMs("2026-01-02T03:04:05Z"), Date.parse("2026-01-02T03:04:05Z"));
  // Unix seconds are promoted to milliseconds (the 1e12 heuristic).
  assert.equal(toEpochMs(1700000000), 1700000000000);
  // Values already in milliseconds pass through unchanged.
  assert.equal(toEpochMs(1700000000000), 1700000000000);
});

test("sortActivityItems drops invalid timestamps and sorts newest-first", () => {
  const items = [
    { id: "missing", timestamp: undefined },
    { id: "null", timestamp: null },
    { id: "invalid", timestamp: "garbage" },
    { id: "old", timestamp: "2026-01-01T00:00:00Z" },
    { id: "new", timestamp: "2026-02-01T00:00:00Z" },
    { id: "middle", timestamp: "2026-01-15T00:00:00Z" },
  ];
  const sorted = sortActivityItems(items);
  assert.deepEqual(
    sorted.map((i) => i.id),
    ["new", "middle", "old"],
  );
});

test("sortActivityItems does not throw when every item is invalid", () => {
  const items = [
    { id: "a", timestamp: null },
    { id: "b", timestamp: undefined },
  ];
  assert.deepEqual(sortActivityItems(items), []);
});

test("sortActivityItems orders equal timestamps stably", () => {
  const items = [
    { id: "a", timestamp: "2026-01-01T00:00:00Z" },
    { id: "b", timestamp: "2026-01-01T00:00:00Z" },
  ];
  const sorted = sortActivityItems(items);
  assert.deepEqual(sorted.map((i) => i.id), ["a", "b"]);
});
