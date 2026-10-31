/** Recovery evidence for an incident (GET /api/incidents/{id}/recovery-check). */

export type RecoveryVerdict = "recovered" | "not_recovered" | "inconclusive";

export interface RecoveryCheckItem {
  kind: "alert" | "sensor" | "device";
  id: string;
  name: string;
  status: string;
  observed_at: string | null;
  result: "pass" | "fail" | "inconclusive";
  detail: string;
}

export interface RecoveryCheck {
  incident_id: string;
  since: string;
  evaluated_at: string;
  verdict: RecoveryVerdict;
  checks: RecoveryCheckItem[];
}

/** Lines naming every check that does not support recovery, failures first. */
export function unmetChecks(check: RecoveryCheck): string[] {
  const order = { fail: 0, inconclusive: 1, pass: 2 } as const;
  return check.checks
    .filter((c) => c.result !== "pass")
    .sort((a, b) => order[a.result] - order[b.result])
    .map((c) => `${c.result === "fail" ? "Failing" : "No fresh evidence"}: ${c.kind} ${c.name} (${c.detail})`);
}

/**
 * Confirmation text before marking an incident recovered without verified
 * evidence, or null when the telemetry supports recovery.
 */
export function recoveryConfirmText(check: RecoveryCheck): string | null {
  if (check.verdict === "recovered") return null;
  const lines = unmetChecks(check);
  const head =
    check.verdict === "not_recovered"
      ? "Telemetry does not show recovery."
      : "Recovery is not verified: there is no fresh telemetry for part of this incident.";
  const body = lines.length ? lines.slice(0, 8).join("\n") : "No alerts, sensors or devices are linked to this incident.";
  const more = lines.length > 8 ? `\n…and ${lines.length - 8} more` : "";
  return `${head}\n\n${body}${more}\n\nMark it recovered anyway?`;
}
