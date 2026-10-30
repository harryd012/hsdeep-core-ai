"use client";

import { memo } from "react";

/**
 * TimeRangeControl — the shared time-range selector.
 *
 * Emits a single TimeRange the whole dashboard can read. Most current backend
 * APIs expose current snapshots or fixed 24h windows rather than arbitrary
 * historical ranges; callers must surface that limitation honestly (see the
 * notes on each tile) instead of pretending a selected range was honored.
 *
 * Custom ranges are supported structurally but require APIs that accept
 * start/end — flagged as unsupported until such an endpoint exists.
 */

export type TimeRangePreset = "15m" | "30m" | "1h" | "6h" | "24h" | "7d";

export interface TimeRange {
  preset: TimeRangePreset;
  /** Inclusive start (ms epoch). Computed from preset for presets. */
  startMs: number;
  /** Exclusive end (ms epoch). Now. */
  endMs: number;
}

export const PRESETS: { id: TimeRangePreset; label: string; minutes: number }[] = [
  { id: "15m", label: "15 min", minutes: 15 },
  { id: "30m", label: "30 min", minutes: 30 },
  { id: "1h", label: "1 hour", minutes: 60 },
  { id: "6h", label: "6 hours", minutes: 360 },
  { id: "24h", label: "24 hours", minutes: 1440 },
  { id: "7d", label: "7 days", minutes: 10_080 },
];

/** Build a TimeRange from a preset (end = now). */
export function rangeFromPreset(preset: TimeRangePreset, now = Date.now()): TimeRange {
  const p = PRESETS.find((x) => x.id === preset) ?? PRESETS[4]; // default 24h
  return { preset, startMs: now - p.minutes * 60_000, endMs: now };
}

export interface TimeRangeControlProps {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
  /** True when the backing APIs honor the selected range. When false the
   *  control shows an honest "data is a current/24h snapshot" note. */
  rangeSupported?: boolean;
  /** Optional auto-refresh seconds; 0 disables. */
  refreshSeconds?: number;
  onRefresh?: () => void;
}

export default memo(function TimeRangeControl({
  value,
  onChange,
  rangeSupported = false,
  refreshSeconds = 0,
  onRefresh,
}: TimeRangeControlProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        flexWrap: "wrap",
        padding: "8px 12px",
        border: "1px solid var(--line, #1f2d38)",
        borderRadius: 8,
        background: "var(--surface, rgba(7,19,27,0.4))",
      }}
    >
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(rangeFromPreset(p.id))}
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "4px 10px",
              borderRadius: 6,
              cursor: "pointer",
              border: `1px solid ${value.preset === p.id ? "var(--cyan)" : "var(--line, #1f2d38)"}`,
              background: value.preset === p.id ? "rgba(0,217,255,.08)" : "transparent",
              color: value.preset === p.id ? "var(--cyan)" : "var(--muted)",
            }}
          >
            {p.label}
          </button>
        ))}
      </div>
      <span style={{ fontSize: 10, color: "var(--muted)" }}>
        {new Date(value.startMs).toLocaleString()} – {new Date(value.endMs).toLocaleString()}
      </span>
      {refreshSeconds > 0 && (
        <span style={{ fontSize: 10, color: "var(--muted)" }}>· auto-refresh {refreshSeconds}s</span>
      )}
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          style={{
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: 6,
            border: "1px solid var(--line, #1f2d38)",
            background: "transparent",
            color: "var(--muted)",
            cursor: "pointer",
          }}
        >
          Refresh
        </button>
      )}
      {!rangeSupported && (
        <span style={{ fontSize: 10, color: "var(--amber)", marginLeft: "auto" }} title="The backing APIs currently expose current snapshots or fixed 24h windows, not arbitrary historical ranges.">
          Snapshot/24h data — historical ranges not yet supported
        </span>
      )}
    </div>
  );
});
