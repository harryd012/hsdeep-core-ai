import type { DrReadiness } from "@/lib/api";
import type { Tone } from "@/components/ui/PageKit";

/** Shared labels for the Business Continuity (disaster recovery) pages. */

export const READINESS: Record<DrReadiness, { text: string; tone: Tone }> = {
  ready: { text: "DR-ready", tone: "good" },
  not_ready: { text: "Not ready", tone: "bad" },
  failed_over: { text: "Failed over", tone: "warn" },
};

/** Site Recovery health (normal / warning / critical); null = not reported. */
export function healthTone(health: string | null): Tone {
  if (health === "normal") return "good";
  if (health === "warning") return "warn";
  if (health === "critical") return "bad";
  return "neutral";
}

export function healthText(health: string | null): string {
  return health ? health.charAt(0).toUpperCase() + health.slice(1) : "Not reported";
}

/** Replication lag: "45 s", "12 min", "1.5 h". */
export function lag(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 120) return `${seconds} s`;
  if (seconds < 7200) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** "westeurope → northeurope" */
export function route(source: string | null, target: string | null): string {
  return `${source ?? "?"} → ${target ?? "?"}`;
}

export const READ_ONLY_NOTE =
  "Failover, test failover, failback and other Site Recovery operations are not started from this platform: it reads Site Recovery with a reader role.";
