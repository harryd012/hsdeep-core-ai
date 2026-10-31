/**
 * Display rules for the Firewalls page. Pure, so they are unit-tested.
 * Anything the firewall did not report reads "No data", never a healthy value.
 */

type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export interface FirewallFacts {
  evidence: "fresh" | "stale" | "none";
  ha_mode: string | null;
  ha_sync: string | null;
  ha_role: string | null;
  session_count: number | null;
  session_limit: number | null;
  licences: { status: string }[] | null;
  tunnels_up: number | null;
  tunnels_total: number | null;
}

export interface Label {
  tone: Tone;
  text: string;
}

const NO_DATA: Label = { tone: "neutral", text: "No data" };

export function evidenceLabel(f: Pick<FirewallFacts, "evidence">): Label {
  if (f.evidence === "fresh") return { tone: "good", text: "Reporting" };
  if (f.evidence === "stale") return { tone: "warn", text: "Stale" };
  return NO_DATA;
}

export function haLabel(f: FirewallFacts): Label {
  if (f.evidence === "none" || f.ha_mode === null) return NO_DATA;
  if (f.ha_mode === "standalone") return { tone: "neutral", text: "Standalone" };
  const role = f.ha_role ? ` · ${f.ha_role}` : "";
  if (f.ha_sync === "in_sync") return { tone: "good", text: `In sync${role}` };
  if (f.ha_sync === "out_of_sync") return { tone: "bad", text: `Out of sync${role}` };
  return { tone: "neutral", text: `Sync not reported${role}` };
}

export function sessionsText(f: FirewallFacts): string {
  if (f.session_count === null) return "No data";
  if (!f.session_limit) return f.session_count.toLocaleString("en-US");
  const pct = Math.round((f.session_count / f.session_limit) * 1000) / 10;
  return `${f.session_count.toLocaleString("en-US")} / ${f.session_limit.toLocaleString("en-US")} (${pct}%)`;
}

export function licenceLabel(f: FirewallFacts): Label {
  if (!f.licences || f.licences.length === 0) return NO_DATA;
  const s = new Set(f.licences.map((l) => l.status));
  if (s.has("expired")) return { tone: "bad", text: `${count(f, "expired")} expired` };
  if (s.has("expiring")) return { tone: "warn", text: `${count(f, "expiring")} expiring` };
  if (s.has("valid")) return { tone: "good", text: "Valid" };
  return NO_DATA;
}

export function tunnelsText(f: FirewallFacts): string {
  if (f.tunnels_total === null) return "No data";
  if (f.tunnels_total === 0) return "None configured";
  return `${f.tunnels_up ?? 0} of ${f.tunnels_total} up`;
}

function count(f: FirewallFacts, status: string): number {
  return (f.licences ?? []).filter((l) => l.status === status).length;
}
