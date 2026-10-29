/**
 * health.ts — single authoritative mapping from the backend /health contract
 * to display state.
 *
 * Every status surface (TopBar, masthead, AI CTO card, sidebar) derives its
 * label/tone from the SAME `status` value ("ok" | "degraded" | "unavailable")
 * reported by GET /health, so the UI can never contradict itself.
 */

/** The authoritative platform state reported by GET /health. */
export type SystemStatus = "ok" | "degraded" | "unavailable";

/** Colour bucket used across the status surfaces. */
export type SystemTone = "ok" | "degraded" | "down" | "pending";

function normalizeStatus(
  status: string | null | undefined,
): SystemStatus | undefined {
  if (status === "ok" || status === "degraded" || status === "unavailable") {
    return status;
  }
  return undefined;
}

/** Map the backend status to a single display tone (colour bucket). */
export function systemTone(status: string | null | undefined): SystemTone {
  const s = normalizeStatus(status);
  if (s === "ok") return "ok";
  if (s === "degraded") return "degraded";
  if (s === "unavailable") return "down";
  return "pending";
}

/** Human label for the system status pill. */
export function systemStatusText(status: string | null | undefined): string {
  const s = normalizeStatus(status);
  if (s === "ok") return "SYSTEMS OPERATIONAL";
  if (s === "degraded") return "SYSTEM DEGRADED";
  if (s === "unavailable") return "SYSTEM OFFLINE";
  return "CHECKING";
}

/** The AI CTO summary-card state: HEALTHY / DEGRADED / UNAVAILABLE / UNKNOWN. */
export function ctoState(
  status: string | null | undefined,
): { text: string; tone: SystemTone } {
  const s = normalizeStatus(status);
  if (s === "ok") return { text: "HEALTHY", tone: "ok" };
  if (s === "degraded") return { text: "DEGRADED", tone: "degraded" };
  if (s === "unavailable") return { text: "UNAVAILABLE", tone: "down" };
  return { text: "UNKNOWN", tone: "pending" };
}

const DEPENDENCIES = ["database", "redis", "ollama"] as const;
const DEPENDENCY_LABEL: Record<(typeof DEPENDENCIES)[number], string> = {
  database: "Database",
  redis: "Redis",
  ollama: "Ollama",
};

/**
 * Per-dependency status line for the sidebar. Preserves the useful per-service
 * breakdown while using terminology aligned with the health contract
 * ("unreachable" / "down" / "unavailable").
 */
export function dependencyStatusLine(services?: Record<string, string>): string {
  if (!services) return "Checking system status…";
  const unhealthy = DEPENDENCIES.filter((dep) => services[dep] !== "up");
  if (unhealthy.length === 0) return "All Systems Operational";
  return unhealthy
    .map((dep) => {
      const state = services[dep];
      if (state === "unreachable") return `${DEPENDENCY_LABEL[dep]} unreachable`;
      if (state === "unavailable") return `${DEPENDENCY_LABEL[dep]} unavailable`;
      return `${DEPENDENCY_LABEL[dep]} down`;
    })
    .join(" · ");
}
