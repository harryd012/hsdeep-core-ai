"use client";

import React from "react";

/**
 * Freshness badge: never present stale data as live.
 *
 * The bucket is derived from the timestamp of the last REAL collection — not
 * from when the page happened to render. `NEVER_COLLECTED` and `UNKNOWN` are
 * deliberately muted rather than green: absence of evidence is not freshness.
 */

export type FreshnessBucket =
  | "LIVE" // < 5 min
  | "RECENT" // 5–30 min
  | "AGING" // 30 min – 2 hr
  | "STALE" // 2 hr – 24 hr
  | "EXPIRED" // > 24 hr
  | "NEVER_COLLECTED" // last_collected_at is null
  | "UNKNOWN"; // cannot determine

interface BucketConfig {
  label: string;
  color: string;
  bg: string;
}

const BUCKET_CONFIG: Record<FreshnessBucket, BucketConfig> = {
  LIVE: { label: "LIVE", color: "var(--green)", bg: "rgba(0,217,100,.12)" },
  RECENT: { label: "RECENT", color: "var(--cyan)", bg: "rgba(0,217,255,.10)" },
  AGING: { label: "AGING", color: "var(--amber)", bg: "rgba(255,180,0,.10)" },
  STALE: { label: "STALE", color: "var(--amber)", bg: "rgba(255,140,0,.12)" },
  EXPIRED: { label: "EXPIRED", color: "var(--red)", bg: "rgba(255,50,50,.10)" },
  NEVER_COLLECTED: { label: "NEVER COLLECTED", color: "var(--muted)", bg: "transparent" },
  UNKNOWN: { label: "UNKNOWN", color: "var(--muted)", bg: "transparent" },
};

/**
 * Classify a UTC ISO timestamp (or null) into a FreshnessBucket.
 * Pass null when the data has never been collected.
 */
export function classifyFreshness(lastCollectedAt: string | null | undefined): FreshnessBucket {
  if (!lastCollectedAt) return "NEVER_COLLECTED";
  const ageMs = Date.now() - new Date(lastCollectedAt).getTime();
  if (Number.isNaN(ageMs)) return "UNKNOWN";
  const min = ageMs / 60_000;
  if (min < 5) return "LIVE";
  if (min < 30) return "RECENT";
  if (min < 120) return "AGING";
  if (min < 1440) return "STALE";
  return "EXPIRED";
}

interface FreshnessIndicatorProps {
  /** UTC ISO timestamp of last collection, or null if never collected. */
  lastCollectedAt: string | null | undefined;
  /** Override the computed bucket (e.g. from a backend-computed freshness state). */
  bucket?: FreshnessBucket;
  /** Show the human-relative age alongside the label, e.g. "LIVE · 2m ago" */
  showAge?: boolean;
  /** Compact mode: dot only, no text label. */
  compact?: boolean;
}

function relativeAge(lastCollectedAt: string): string {
  const ageMs = Date.now() - new Date(lastCollectedAt).getTime();
  if (Number.isNaN(ageMs)) return "";
  const s = Math.floor(ageMs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function FreshnessIndicator({
  lastCollectedAt,
  bucket: bucketOverride,
  showAge = false,
  compact = false,
}: FreshnessIndicatorProps) {
  const bucket = bucketOverride ?? classifyFreshness(lastCollectedAt);
  const cfg = BUCKET_CONFIG[bucket];

  if (compact) {
    return (
      <i
        title={`${cfg.label}${lastCollectedAt ? ` — ${new Date(lastCollectedAt).toLocaleString()}` : ""}`}
        style={{
          display: "inline-block",
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: cfg.color,
          flexShrink: 0,
        }}
      />
    );
  }

  const age = showAge && lastCollectedAt ? ` · ${relativeAge(lastCollectedAt)}` : "";

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 6px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.3,
        color: cfg.color,
        background: cfg.bg,
        whiteSpace: "nowrap",
      }}
      title={lastCollectedAt ? new Date(lastCollectedAt).toLocaleString() : "Never collected"}
    >
      {cfg.label}{age}
    </span>
  );
}
