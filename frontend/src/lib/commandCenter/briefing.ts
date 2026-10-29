/**
 * Situation Briefing — an explainable reading of the estate's state.
 *
 * Deterministic rules over signals the platform already collects (command-
 * center brief, site health, alert summary). Each finding states what is
 * true, the evidence behind it, why it matters and where to act. Nothing is
 * generated text: if a rule's inputs are missing, the rule stays silent.
 *
 * The readiness score starts at 100 and subtracts each finding's weight, so
 * every point lost is attributable to a named finding.
 *
 * Pure and dependency-free (tests: frontend/tests/briefing.test.mjs).
 */

export type FindingSeverity = "critical" | "warning" | "info";

export interface Finding {
  id: string;
  severity: FindingSeverity;
  title: string;
  /** Measured facts, shown as chips. */
  evidence: string[];
  /** Why an operator should care. */
  impact: string;
  action: { label: string; href: string };
  /** Points this finding takes off the readiness score. */
  weight: number;
}

export interface BriefingInput {
  devices?: { total: number; healthy: number; warning: number; critical: number; down: number; unknown: number } | null;
  collectors?: { total: number; healthy: number; degraded: number; failed: number; stale: number; unknown: number } | null;
  /** Latest collection error per source, if any. */
  sourceErrors?: { name: string; error: string | null }[] | null;
  incidents?: { open: number; critical: number; opened24h: number; resolved24h: number } | null;
  sites?: { total: number; unassignedDevices: number } | null;
  alerts?: { open: number; critical: number } | null;
}

export interface Briefing {
  score: number | null;
  grade: "Healthy" | "At risk" | "Degraded" | "Critical" | "No data";
  findings: Finding[];
  /** Rules that could run (had their inputs). */
  rulesEvaluated: number;
}

const fmt = (n: number) => n.toLocaleString("en-US");
const pctOf = (part: number, total: number) => (total ? Math.round((part / total) * 100) : 0);
const SEVERITY_RANK: Record<FindingSeverity, number> = { critical: 0, warning: 1, info: 2 };

export function buildBriefing(input: BriefingInput): Briefing {
  const findings: Finding[] = [];
  let evaluated = 0;
  const { devices, collectors, incidents, sites, alerts } = input;

  // 1. Collection blackout — the platform cannot see the estate.
  const collectionBlind = !!collectors && collectors.total > 0 && collectors.healthy === 0;
  if (collectors && collectors.total > 0) {
    evaluated += 1;
    const failing = collectors.failed + collectors.stale + collectors.degraded;
    if (collectionBlind || failing > 0) {
      const topError = input.sourceErrors?.find((s) => s.error)?.error ?? null;
      findings.push({
        id: "collection",
        severity: collectionBlind ? "critical" : "warning",
        title: collectionBlind
          ? `Monitoring is blind: none of ${collectors.total} collectors is healthy`
          : `${failing} of ${collectors.total} collectors are not collecting cleanly`,
        evidence: [
          `${collectors.failed} failed`,
          ...(collectors.stale ? [`${collectors.stale} stale`] : []),
          ...(collectors.degraded ? [`${collectors.degraded} degraded`] : []),
          ...(topError ? [`Latest error: ${topError.length > 80 ? `${topError.slice(0, 77)}…` : topError}`] : []),
        ],
        impact: collectionBlind
          ? "Every health number on this page is frozen at the last successful collection. New failures will not raise alerts."
          : "Devices behind these collectors may show stale or missing state.",
        action: { label: "Fix collectors", href: "/monitoring" },
        weight: collectionBlind ? 35 : Math.min(20, 5 * failing),
      });
    }
  }

  // 2. Correlation: a mostly-"down" estate while collection is failing is far
  //    more likely a data problem than a real outage.
  if (devices && devices.total > 0) {
    evaluated += 1;
    const down = devices.down + devices.critical;
    const share = pctOf(down, devices.total);
    if (share >= 50) {
      findings.push({
        id: "estate-down",
        severity: collectionBlind ? "warning" : "critical",
        title: collectionBlind
          ? `${share}% of devices read as down — likely a collection artefact, not an outage`
          : `${share}% of the estate is down or critical`,
        evidence: [`${fmt(down)} of ${fmt(devices.total)} devices`, `${fmt(devices.healthy)} healthy`],
        impact: collectionBlind
          ? "With every collector failing, device state cannot refresh. Restore collection before treating this as a mass outage."
          : "A majority of devices are unavailable; expect broad service impact.",
        action: collectionBlind ? { label: "Check collection first", href: "/monitoring" } : { label: "Open critical hosts", href: "/infrastructure/hosts?health=critical" },
        weight: collectionBlind ? 10 : 30,
      });
    }
  }

  // 3. Stagnant incident backlog — lots open, nothing moving.
  if (incidents) {
    evaluated += 1;
    if (incidents.open > 0 && incidents.opened24h === 0 && incidents.resolved24h === 0) {
      findings.push({
        id: "backlog",
        severity: incidents.open > 1000 ? "warning" : "info",
        title: `${fmt(incidents.open)} incidents are open but nothing changed in 24 hours`,
        evidence: [`${fmt(incidents.critical)} critical`, "0 opened in 24h", "0 resolved in 24h"],
        impact: "A frozen backlog hides real new problems and inflates every incident KPI. Old incidents likely need an auto-close or archive policy.",
        action: { label: "Review incidents", href: "/incidents" },
        weight: incidents.open > 1000 ? 15 : 5,
      });
    } else if (incidents.critical > 0) {
      findings.push({
        id: "critical-incidents",
        severity: "critical",
        title: `${fmt(incidents.critical)} critical incidents need an owner`,
        evidence: [`${fmt(incidents.open)} active`, `${fmt(incidents.opened24h)} opened in 24h`],
        impact: "Critical incidents are the most likely source of customer-facing impact.",
        action: { label: "Open critical incidents", href: "/incidents?severity=critical" },
        weight: Math.min(25, 5 + incidents.critical),
      });
    }
  }

  // 4. Site model missing — impact analysis and site views have nothing to work with.
  if (sites && devices && devices.total > 0) {
    evaluated += 1;
    if (sites.unassignedDevices > 0) {
      const share = pctOf(sites.unassignedDevices, devices.total);
      findings.push({
        id: "sites",
        severity: share >= 50 ? "warning" : "info",
        title: sites.total === 0 ? "No sites are defined — every device is unplaced" : `${fmt(sites.unassignedDevices)} devices have no site`,
        evidence: [`${share}% of devices unassigned`, `${sites.total} sites`],
        impact: "Without sites, the honeycomb, top-affected-sites and business impact views stay empty, and incidents cannot be routed by location.",
        action: { label: "Organize the estate", href: "/infrastructure/hosts" },
        weight: share >= 50 ? 8 : 3,
      });
    }
  }

  // 5. Unacknowledged alerts.
  if (alerts) {
    evaluated += 1;
    if (alerts.open > 0) {
      findings.push({
        id: "alerts",
        severity: alerts.critical > 0 ? "warning" : "info",
        title: `${fmt(alerts.open)} alerts are waiting to be acknowledged`,
        evidence: [`${fmt(alerts.critical)} critical (all statuses)`],
        impact: "Unacknowledged alerts mean nobody has confirmed they are being handled.",
        action: { label: "Triage alerts", href: "/alerts" },
        weight: Math.min(10, 2 + Math.floor(alerts.open / 20)),
      });
    }
  }

  findings.sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.weight - a.weight);
  if (evaluated === 0) return { score: null, grade: "No data", findings, rulesEvaluated: 0 };
  const score = Math.max(0, 100 - findings.reduce((sum, f) => sum + f.weight, 0));
  const grade = score >= 85 ? "Healthy" : score >= 65 ? "At risk" : score >= 40 ? "Degraded" : "Critical";
  return { score, grade, findings, rulesEvaluated: evaluated };
}
