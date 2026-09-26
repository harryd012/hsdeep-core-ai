/**
 * Empty-state composition for the live topology (Phase 9.7A).
 *
 * Built from the read model, so the screen states the platform's actual
 * position: how many devices exist, that zero relationships are recorded, and
 * what the backend itself says is missing. Every number is real; the notes are
 * the backend's own `limitations` verbatim, because they already carry the
 * collector/capability evidence.
 *
 * No action is offered: there is no backend workflow this view can trigger, and
 * inventing one (or a fake "Run discovery" button) would misrepresent the
 * platform.
 */

export type EmptyCountTone = "neutral" | "good" | "warn" | "bad" | "muted";

export interface EmptyStateCount {
  label: string;
  value: number;
  tone: EmptyCountTone;
}

export interface TopologyEmptyState {
  heading: string;
  explanation: string;
  counts: EmptyStateCount[];
  nextStep: string;
  /** The backend's own limitations, verbatim and de-duplicated. */
  evidenceNotes: string[];
}

export interface EmptyStateInput {
  devices: number;
  relationships: number;
  interfaces: number;
  online: number;
  offline: number;
  unidentified: number;
  limitations: readonly string[];
}

export const EMPTY_HEADING = "Topology relationships not yet discovered";

export const EMPTY_EXPLANATION =
  "Devices are available, but no validated network relationship evidence has been collected for this tenant.";

export const EMPTY_NEXT_STEP =
  "Topology will populate automatically when a topology-capable collector produces interface and adjacency evidence. This view is informational only.";

/** True when the canvas has nothing to draw (no recorded relationship at all). */
export function isGraphEmpty(edgeCount: number): boolean {
  return edgeCount === 0;
}

export function buildEmptyState(input: EmptyStateInput): TopologyEmptyState {
  const counts: EmptyStateCount[] = [
    { label: "Devices", value: input.devices, tone: "neutral" },
    { label: "Relationships", value: input.relationships, tone: input.relationships ? "good" : "warn" },
    { label: "Interfaces", value: input.interfaces, tone: input.interfaces ? "good" : "warn" },
  ];

  const notes = [...new Set(input.limitations.filter((line) => line && line.trim()))];

  if (input.devices === 0) {
    return {
      heading: EMPTY_HEADING,
      explanation:
        "No devices have been recorded for this tenant yet, so there is nothing to map.",
      counts,
      nextStep:
        "Discovery must record devices before any topology relationship can be derived. This view is informational only.",
      evidenceNotes: notes,
    };
  }

  return {
    heading: EMPTY_HEADING,
    explanation: `${EMPTY_EXPLANATION} ${input.devices} device(s) are recorded (${input.online} online, ${input.offline} offline, ${input.unidentified} unidentified).`,
    counts,
    nextStep: EMPTY_NEXT_STEP,
    evidenceNotes: notes,
  };
}
