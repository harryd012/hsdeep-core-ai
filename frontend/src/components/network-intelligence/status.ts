/**
 * Shared presentation semantics for the Network Intelligence surfaces
 * (the /infrastructure panel and the compact /dashboard widget).
 *
 * These helpers only MAP values that the existing API already returns into
 * the dashboard's existing colour vocabulary. They never invent a status, a
 * reading, or a telemetry value.
 */
import type { NetworkEvaluateResult, SaseStage, ScoredWanPath } from "@/lib/api";

export type Tone = "ok" | "warn" | "bad" | "neutral";

/** SD-WAN path health: HEALTHY | DEGRADED | DOWN (from the API). */
export function pathTone(health: string | undefined): Tone {
  switch ((health ?? "").toUpperCase()) {
    case "HEALTHY":
      return "ok";
    case "DEGRADED":
      return "warn";
    case "DOWN":
      return "bad";
    default:
      return "neutral";
  }
}

/** SASE stage verdicts: ALLOW | DENY | BLOCK | NOT_EVALUATED (from the API). */
export function saseTone(decision: string | undefined): Tone {
  switch ((decision ?? "").toUpperCase()) {
    case "ALLOW":
      return "ok";
    case "DENY":
    case "BLOCK":
      return "bad";
    case "NOT_EVALUATED":
    case "NOT EVALUATED":
      return "neutral";
    default:
      return "neutral";
  }
}

/** "NOT_EVALUATED" renders as "NOT EVALUATED" for readability. */
export function readable(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/_/g, " ").toUpperCase();
}

export interface NetworkVerdict {
  /** Top-level operational status line for the header. */
  overall: string;
  overallTone: Tone;
  wanLabel: string;
  wanTone: Tone;
  saseLabel: string;
  saseTone: Tone;
  recommendationLabel: string;
  recommendationTone: Tone;
  selectedPath: ScoredWanPath | null;
  /** Only stages actually returned by the API. */
  pipeline: SaseStage[];
  needsHumanDecision: boolean;
}

/**
 * Derive the executive summary purely from an evaluation response.
 * Returns null when there is no evaluation yet — callers render an honest
 * "READY / no evaluation executed yet" state instead of fake values.
 */
export function deriveVerdict(
  result: NetworkEvaluateResult | null | undefined,
): NetworkVerdict | null {
  // A malformed/absent payload must yield "no verdict", never a thrown error.
  if (!isValidEvaluation(result)) return null;

  const sdwan = result.sdwan;
  const sase = result.sase;
  const selected =
    sdwan.paths.find((p) => p.name === sdwan.selected_path) ?? null;
  const saseBlocked = sase.decision === "BLOCK" || sase.decision === "DENY";
  const rec = result.recommendation;

  const overall = saseBlocked
    ? "SECURITY BLOCK"
    : sdwan.decision === "NO_AVAILABLE_PATH"
      ? "NO AVAILABLE PATH"
      : sase.decision === "ALLOW"
        ? "ALLOWED"
        : readable(sase.decision);

  return {
    overall,
    overallTone: saseBlocked ? "bad" : overall === "ALLOWED" ? "ok" : "warn",
    wanLabel: selected
      ? `${selected.name} · ${(selected.health ?? "").toUpperCase()}`
      : readable(sdwan.decision),
    wanTone: selected ? pathTone(selected.health) : "warn",
    saseLabel: readable(sase.decision),
    saseTone: saseTone(sase.decision),
    recommendationLabel: rec ? readable(rec.status) : "NO ACTION REQUIRED",
    recommendationTone: rec ? "warn" : "ok",
    selectedPath: selected,
    pipeline: sase.pipeline,
    needsHumanDecision: !!rec && rec.status === "PENDING_HUMAN_DECISION",
  };
}

/** The visual DETECT → … → AUDIT flow (existing HSDEEP workflow, visualised). */
export const DECISION_FLOW = [
  "TELEMETRY",
  "SD-WAN PATH",
  "SASE POLICY",
  "CORRELATION",
  "RECOMMENDATION",
  "HUMAN DECISION",
  "ACTION PLANE",
] as const;

/** Operator-facing failure state (never fabricates network intelligence). */
export interface ApiProblem {
  label: string;
  detail: string;
}

export const INVALID_RESPONSE: ApiProblem = {
  label: "INVALID RESPONSE",
  detail:
    "The Network Intelligence service returned an unexpected payload, so no decision can be shown.",
};

/**
 * Shape guard for the evaluation payload. A malformed body must never reach the
 * render path — without this the card components would throw while reading
 * `sdwan.paths` and blank the whole page.
 */
export function isValidEvaluation(value: unknown): value is NetworkEvaluateResult {
  if (!value || typeof value !== "object") return false;
  const r = value as Partial<NetworkEvaluateResult>;
  return (
    typeof r.flow_id === "string" &&
    !!r.sdwan &&
    typeof r.sdwan.decision === "string" &&
    Array.isArray(r.sdwan.paths) &&
    !!r.sase &&
    typeof r.sase.decision === "string" &&
    Array.isArray(r.sase.pipeline) &&
    Array.isArray(r.audit)
  );
}

/** Shape guard for the scenario payload (flow + candidate paths). */
export function isValidScenario(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const s = value as { flow?: unknown; paths?: unknown };
  return !!s.flow && typeof s.flow === "object" && Array.isArray(s.paths);
}

/** Map a thrown API error to the operator-facing semantic state. */
export function describeApiProblem(err: unknown): ApiProblem {
  const status = typeof (err as { status?: unknown })?.status === "number"
    ? (err as { status: number }).status
    : 0;
  const raw = err instanceof Error ? err.message : String(err ?? "");

  if (status === 401) {
    return {
      label: "ACCESS DENIED",
      detail: "Your session is not authorised for Network Intelligence. Sign in again and retry.",
    };
  }
  if (status === 403) {
    return {
      label: "ACCESS DENIED",
      detail: "Your role does not grant access to Network Intelligence.",
    };
  }
  if (status === 404) {
    return {
      label: "API ENDPOINT UNAVAILABLE",
      detail: "The Network Intelligence endpoint is not available on this backend.",
    };
  }
  if (status === 502 || status === 503 || status === 504) {
    return {
      label: "SERVICE ERROR",
      detail: "The Network Intelligence service is temporarily unavailable.",
    };
  }
  if (status === 0) {
    return {
      label: "API UNAVAILABLE",
      detail: "The Network Intelligence service could not be reached.",
    };
  }
  if (status >= 500) {
    return {
      label: "SERVICE ERROR",
      detail: "The Network Intelligence service reported an internal error.",
    };
  }
  return {
    label: "SERVICE ERROR",
    detail: raw || "The Network Intelligence request failed.",
  };
}

