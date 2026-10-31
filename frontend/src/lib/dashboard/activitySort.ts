/**
 * activitySort.ts — pure, dependency-free timestamp normalization + sort.
 *
 * Extracted so the activity feed can be sorted by PARSED date value (never by
 * string `localeCompare`, which crashes on a missing/invalid timestamp and
 * sorts "2026-01-02" before "2026-11-01" lexicographically). It is also the
 * one place that decides whether a timestamp is usable, so a single unit test
 * covers the missing/null/invalid cases for the whole feed.
 *
 * No React / Next / API imports here: it is testable with Node's native runner
 * in isolation.
 */

/**
 * Parse any timestamp shape (ISO string, Unix seconds, Unix milliseconds, or a
 * `Date`) to epoch milliseconds, or `null` when the value is unusable.
 *
 * Numeric values smaller than 1e12 are treated as Unix SECONDS (the common
 * backend mismatch where `new Date(seconds)` silently resolves to 1970);
 * larger values are already milliseconds.
 */
export function toEpochMs(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    const ms = value < 1e12 ? value * 1000 : value;
    return Number.isFinite(ms) ? ms : null;
  }
  if (value instanceof Date) {
    const t = value.getTime();
    return Number.isFinite(t) ? t : null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    // Accept both an ISO date and a purely numeric epoch string.
    const parsed = /^\d+$/.test(trimmed) ? Number(trimmed) : Date.parse(trimmed);
    if (typeof parsed !== "number" || !Number.isFinite(parsed)) return null;
    return toEpochMs(parsed);
  }
  return null;
}

/**
 * Sort activity items newest-first by their PARSED timestamp. Items whose
 * timestamp is missing, null or invalid are dropped (they cannot be ordered
 * honestly) rather than crashing the feed.
 */
export function sortActivityItems<T extends { timestamp: unknown }>(items: T[]): T[] {
  return items
    .filter((item) => toEpochMs(item.timestamp) !== null)
    .sort(
      (a, b) =>
        (toEpochMs(b.timestamp) as number) - (toEpochMs(a.timestamp) as number),
    );
}
