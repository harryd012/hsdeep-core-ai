"use client";

/**
 * TopologyEmptyStatePanel — the designed zero-relationship state (Phase 9.7A).
 *
 * A blank canvas is a design failure: this panel states exactly what the platform
 * knows (real counts), repeats the backend's own limitations as evidence, and
 * explains the informational next step. It offers no button, because there is no
 * backend workflow this view can trigger, and it draws no placeholder topology.
 */
import { Link2, Network, Server, ShieldAlert } from "lucide-react";

import type { EmptyCountTone, TopologyEmptyState } from "../../lib/topology/emptyState";

const TONE_COLOR: Record<EmptyCountTone, string> = {
  good: "var(--green)",
  warn: "var(--amber)",
  bad: "var(--red)",
  muted: "var(--muted)",
  neutral: "var(--fg)",
};

const COUNT_ICON = {
  Devices: Server,
  Relationships: Link2,
  Interfaces: Network,
} as const;

export default function TopologyEmptyStatePanel({
  state,
  variant = "full",
}: {
  state: TopologyEmptyState;
  /**
   * `full` fills an otherwise-empty canvas; `compact` is a card over a canvas
   * that already shows real (unconnected) device nodes, so the workspace is
   * never blank and the nodes stay usable.
   */
  variant?: "full" | "compact";
}) {
  const compact = variant === "compact";
  return (
    <div
      data-testid="topology-empty-state"
      data-variant={variant}
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: compact ? 8 : 14,
        height: compact ? undefined : "100%",
        padding: compact ? "10px 14px" : "28px 20px",
        textAlign: "center",
        background: compact ? "rgba(2,6,23,0.86)" : "transparent",
        border: compact ? "1px solid var(--border)" : "none",
        borderRadius: compact ? 10 : 0,
        position: compact ? "absolute" : "relative",
        top: compact ? 12 : undefined,
        left: compact ? "50%" : undefined,
        transform: compact ? "translateX(-50%)" : undefined,
        maxWidth: compact ? "min(560px, calc(100% - 230px))" : undefined,
        zIndex: compact ? 5 : undefined,
      }}
    >
      <Network size={34} aria-hidden="true" style={{ color: "var(--muted)", opacity: 0.75 }} />
      <div>
        <div
          data-testid="topology-empty-heading"
          style={{
            fontSize: 14,
            fontWeight: 600,
            letterSpacing: "1.2px",
            color: "var(--fg)",
            textTransform: "uppercase",
          }}
        >
          {state.heading}
        </div>
        <p
          style={{
            margin: "8px auto 0",
            maxWidth: 620,
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--muted)",
          }}
        >
          {state.explanation}
        </p>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
        {state.counts.map((count) => {
          const Icon = COUNT_ICON[count.label as keyof typeof COUNT_ICON] ?? Network;
          const color = TONE_COLOR[count.tone];
          return (
            <div
              key={count.label}
              data-testid={`topology-empty-count-${count.label.toLowerCase()}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                minWidth: 128,
                padding: "8px 12px",
                border: "1px solid var(--border)",
                borderLeft: `2px solid ${color}`,
                borderRadius: 8,
                background: "rgba(15,23,42,0.55)",
                textAlign: "left",
              }}
            >
              <Icon size={15} aria-hidden="true" style={{ color }} />
              <span style={{ display: "flex", flexDirection: "column" }}>
                <span style={{ fontSize: 16, fontWeight: 600, color, lineHeight: 1.1 }}>
                  {count.value}
                </span>
                <span style={{ fontSize: 10, letterSpacing: "0.5px", color: "var(--muted)" }}>
                  {count.label.toUpperCase()}
                </span>
              </span>
            </div>
          );
        })}
      </div>

      <p style={{ maxWidth: 620, fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
        {state.nextStep}
      </p>

      {state.evidenceNotes.length ? (
        <div
          data-testid="topology-empty-evidence"
          style={{
            maxWidth: 680,
            width: "100%",
            padding: "8px 10px",
            border: "1px dashed var(--border)",
            borderRadius: 8,
            textAlign: "left",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 10,
              letterSpacing: "0.5px",
              color: "var(--muted)",
            }}
          >
            <ShieldAlert size={12} aria-hidden="true" />
            WHAT THE BACKEND REPORTS
          </div>
          <ul style={{ margin: "6px 0 0", paddingLeft: 16, fontSize: 11, color: "var(--muted)" }}>
            {state.evidenceNotes.map((note) => (
              <li key={note} style={{ lineHeight: 1.5 }}>
                {note}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}