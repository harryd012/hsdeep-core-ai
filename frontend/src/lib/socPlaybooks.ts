/**
 * SOC playbooks: the SOPs tagged `soc` (SOP-017 firewall diagnostic, SOP-018..021
 * security playbooks, SOP-022 FortiGate scenarios). Pure helpers that turn the
 * SOP API's loose step JSON into a typed shape the page can render, and filter
 * a step's commands to the platform the analyst is working on.
 */

export interface PlaybookCommand {
  platform: string;
  command: string;
  disruptive: boolean;
}

export interface PlaybookStep {
  order: number;
  action: string;
  expectedResult: string | null;
  warnings: string[];
  matrix: { condition: string; meaning: string }[];
  commands: PlaybookCommand[];
}

interface VersionLike {
  id: string;
  version_number: number;
  steps: Array<Record<string, unknown>>;
}

/** Display names for the command platforms used in the SOP library. */
export const PLATFORM_LABELS: Record<string, string> = {
  fortios: "FortiGate (FortiOS)",
  panos: "Palo Alto (PAN-OS)",
  cisco_asa: "Cisco ASA",
  cisco_ftd: "Cisco FTD",
  checkpoint: "Check Point",
  junos_srx: "Juniper SRX",
  sonicwall: "SonicWall",
  sophos: "Sophos",
  cisco_ios: "Cisco IOS",
  windows: "Windows",
  linux: "Linux",
  meraki: "Meraki",
  siem: "SIEM",
  edr: "EDR",
  any: "Any",
};

export function platformLabel(p: string): string {
  return PLATFORM_LABELS[p] ?? p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function isSocPlaybook(sop: { tags: string[]; is_active?: boolean }): boolean {
  return sop.is_active !== false && sop.tags.some((t) => t.toLowerCase() === "soc");
}

/** The SOP's current version, else the highest version number. */
export function currentVersion<V extends VersionLike>(currentId: string | null, versions: V[] | undefined): V | undefined {
  if (!versions?.length) return undefined;
  return (
    (currentId ? versions.find((v) => v.id === currentId) : undefined) ??
    versions.slice().sort((a, b) => b.version_number - a.version_number)[0]
  );
}

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

/** Normalise raw step JSON; anything malformed is dropped, never invented. */
export function normalizeSteps(raw: Array<Record<string, unknown>> | undefined): PlaybookStep[] {
  return (raw ?? [])
    .map((s, i): PlaybookStep | null => {
      const action = str(s.action);
      if (!action) return null;
      const matrixRaw = s.troubleshooting_matrix;
      const matrix =
        matrixRaw && typeof matrixRaw === "object" && !Array.isArray(matrixRaw)
          ? Object.entries(matrixRaw as Record<string, unknown>)
              .filter(([, v]) => str(v))
              .map(([k, v]) => ({ condition: k.replace(/_/g, " "), meaning: v as string }))
          : [];
      const commands = Array.isArray(s.commands)
        ? (s.commands as Array<Record<string, unknown>>)
            .filter((c) => c && str(c.command))
            .map((c) => ({ platform: str(c.platform) ?? "any", command: c.command as string, disruptive: c.disruptive === true }))
        : [];
      return {
        order: typeof s.order === "number" ? s.order : i + 1,
        action,
        expectedResult: str(s.expected_result),
        warnings: Array.isArray(s.warnings) ? (s.warnings as unknown[]).filter((w): w is string => !!str(w)) : [],
        matrix,
        commands,
      };
    })
    .filter((s): s is PlaybookStep => s !== null)
    .sort((a, b) => a.order - b.order);
}

/** Platforms that appear in any step's commands, most-used first. */
export function platformsOf(steps: PlaybookStep[]): string[] {
  const counts = new Map<string, number>();
  for (const s of steps) for (const c of s.commands) counts.set(c.platform, (counts.get(c.platform) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([p]) => p);
}

/** Commands for one platform ("all" keeps every command). Vendor-neutral "any" commands always show. */
export function commandsFor(step: PlaybookStep, platform: string): PlaybookCommand[] {
  if (platform === "all") return step.commands;
  return step.commands.filter((c) => c.platform === platform || c.platform === "any");
}

/** Search across number, name, description and tags. */
export function matchesPlaybook(
  sop: { sop_number: string; name: string; description: string | null; tags: string[]; vendor: string | null },
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [sop.sop_number, sop.name, sop.description ?? "", sop.vendor ?? "", ...sop.tags].some((f) => f.toLowerCase().includes(q));
}
