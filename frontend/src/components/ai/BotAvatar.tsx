"use client";

import { memo, type CSSProperties } from "react";

/**
 * BotAvatar — reusable HSDEEP CORE AI robot avatar for the AI CTO,
 * department managers, and L1/L2 agents.
 *
 * Pure SVG + CSS (no external assets). Status is communicated with the
 * existing design tokens:
 *   online     → green   (available)
 *   busy       → red     (working / assigned — matches topology statusColor)
 *   degraded   → amber
 *   error      → red
 *   offline    → muted
 *   controller → cyan    (AI CTO / system controller)
 *
 * `working` enables a subtle activity pulse. The pulse is driven ONLY by
 * real assignment state passed by the caller (e.g. an active task assigned
 * to the agent) — never by timers or random values — and is disabled under
 * prefers-reduced-motion (see dashboard.css).
 */

export type BotAvatarStatus =
  | "online"
  | "busy"
  | "offline"
  | "degraded"
  | "error"
  | "controller";

export type BotAvatarSize = "sm" | "md" | "lg";

const STATUS_COLOR: Record<BotAvatarStatus, string> = {
  online: "var(--green, #2ecc71)",
  busy: "var(--red, #ff5c5c)",
  offline: "var(--muted, #6b8494)",
  degraded: "var(--amber, #f5a623)",
  error: "var(--red, #ff5c5c)",
  controller: "var(--cyan, #17d9ed)",
};

const BotAvatar = memo(function BotAvatar({
  status = "online",
  size = "md",
  working = false,
  label,
  className,
}: {
  status?: BotAvatarStatus;
  size?: BotAvatarSize;
  /** True only when real backend data shows an active assignment/task. */
  working?: boolean;
  /** Accessible label; defaults to a status description. */
  label?: string;
  className?: string;
}) {
  const color = STATUS_COLOR[status];
  const classes = [
    "bot-avatar",
    `bot-avatar--${size}`,
    `bot-avatar--${status}`,
    working ? "bot-avatar--working" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={classes}
      style={{ "--bot-accent": color } as CSSProperties}
      role="img"
      aria-label={label ?? `AI agent · ${status}`}
      title={label ?? `AI agent · ${status}`}
    >
      {working && <span className="bot-avatar__pulse" aria-hidden="true" />}
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
        {/* antenna */}
        <line x1="12" y1="2" x2="12" y2="4.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <circle cx="12" cy="2" r="1.15" fill="currentColor" className="bot-avatar__beacon" />
        {/* head */}
        <rect x="4" y="4.2" width="16" height="12.6" rx="3.4" stroke="currentColor" strokeWidth="1.3" />
        {/* visor */}
        <rect x="6.4" y="7.1" width="11.2" height="4.8" rx="2.2" fill="currentColor" opacity="0.14" />
        {/* eyes */}
        <circle cx="9.4" cy="9.5" r="1.25" fill="currentColor" className="bot-avatar__eye" />
        <circle cx="14.6" cy="9.5" r="1.25" fill="currentColor" className="bot-avatar__eye" />
        {/* mouth / voice grill */}
        <line x1="9.6" y1="14.2" x2="14.4" y2="14.2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" opacity="0.65" />
        {/* ears */}
        <line x1="2.4" y1="9" x2="4" y2="9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <line x1="20" y1="9" x2="21.6" y2="9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        {/* shoulders */}
        <path d="M7.8 19.6c1.2.9 2.7 1.4 4.2 1.4s3-.5 4.2-1.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
      <span className="bot-avatar__status" aria-hidden="true" />
    </span>
  );
});

export default BotAvatar;

/** Maps the real backend availability/status strings used by CCAgent and
 *  CommandCenterTopology onto BotAvatar status variants. */
export function botStatusFromAvailability(
  availability: string | null | undefined,
): BotAvatarStatus {
  const s = (availability || "").toLowerCase();
  if (["available", "ready", "online", "healthy", "operational", "active", "idle"].includes(s))
    return "online";
  if (["busy", "working", "assigned", "accepted", "executing", "investigating", "verifying"].includes(s))
    return "busy";
  if (s === "degraded" || s === "waiting" || s === "retrying") return "degraded";
  if (["failed", "error", "escalated"].includes(s)) return "error";
  if (["offline", "cancelled", "unavailable", "stopped"].includes(s)) return "offline";
  return "offline";
}
