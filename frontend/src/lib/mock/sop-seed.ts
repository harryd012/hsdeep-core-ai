/**
 * SOP seed library — representative fallback dataset for the SOP Engine grid.
 *
 * The SOP Engine page always tries GET /api/sops first; this module is the
 * local fallback used when the backend is unreachable or the tenant has no
 * SOPs seeded yet, so the search/filter/category-count logic stays fully
 * demonstrable end-to-end. Titles mirror the runbooks under /sop/*.yaml.
 */

import type { SOP } from "@/lib/api";

const TENANT_ID = "00000000-0000-0000-0000-000000000000";

interface SeedInput {
  number: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  vendor?: string | null;
  approval?: boolean;
  minutes: number;
  stepCount: number;
}

function buildSop(input: SeedInput): SOP {
  const now = "2026-01-01T00:00:00Z";
  return {
    id: input.number.toLowerCase(),
    tenant_id: TENANT_ID,
    sop_number: input.number,
    name: input.name,
    description: input.description,
    category: input.category,
    tags: input.tags,
    vendor: input.vendor ?? null,
    platform: input.vendor ?? null,
    device_type: null,
    site_id: null,
    business_service_id: null,
    region: null,
    min_priority: input.approval ? "P2" : "P3",
    required_skills: [],
    requires_approval: input.approval ?? false,
    estimated_resolution_minutes: input.minutes,
    rollback_procedure: null,
    is_active: true,
    current_version_id: `${input.number.toLowerCase()}-v1`,
    created_at: now,
    updated_at: now,
    versions: [
      {
        id: `${input.number.toLowerCase()}-v1`,
        sop_id: input.number.toLowerCase(),
        version_number: 1,
        steps: Array.from({ length: input.stepCount }, (_, i) => ({
          order: i + 1,
          action: "Refer to the published runbook document.",
        })),
        possible_causes: [],
        change_summary: null,
        status: "approved",
        approved_by: null,
        approved_at: now,
        created_at: now,
      },
    ],
  };
}

export const SOP_SEED: SOP[] = [
  { number: "SOP-001", name: "Device/Service Outage Response", description: "Triage and restore any monitored device or service reporting down.", category: "availability", tags: ["outage", "triage"], minutes: 30, stepCount: 8 },
  { number: "SOP-002", name: "Internet Outage / Missing Static IP", description: "Detect ISP outage vs. lost static IP assignment and fail over WAN links.", category: "connectivity", tags: ["wan", "isp", "starlink"], approval: true, minutes: 45, stepCount: 10 },
  { number: "SOP-003", name: "Meraki Site Outage", description: "Validate Meraki organization health and restore tunnel/site reachability.", category: "availability", tags: ["meraki", "site"], vendor: "Cisco Meraki", minutes: 40, stepCount: 9 },
  { number: "SOP-004", name: "Firewall Ping Down / Starlink IP Change", description: "Handle FortiGate losing ping after a Starlink dynamic IP rotation.", category: "connectivity", tags: ["fortigate", "firewall", "ip-change"], vendor: "Fortinet", approval: true, minutes: 35, stepCount: 11 },
  { number: "SOP-005", name: "IIS App Pool Recovery", description: "Identify crashed IIS application pools and recycle them safely.", category: "performance", tags: ["iis", "windows"], vendor: "Microsoft", minutes: 20, stepCount: 6 },
  { number: "SOP-006", name: "DNS Sensor Verification (PRTG)", description: "Verify PRTG DNS sensors match expected resolution after zone changes.", category: "configuration", tags: ["dns", "prtg"], vendor: "Paessler", minutes: 25, stepCount: 7 },
  { number: "SOP-007", name: "Inactive Property DNS Alert", description: "Resolve stale DNS records triggering inactive-property alerts.", category: "configuration", tags: ["dns", "records"], minutes: 20, stepCount: 6 },
  { number: "SOP-008", name: "PRTG Device & Sensor Onboarding", description: "Onboard new devices into PRTG with the standard sensor set.", category: "configuration", tags: ["prtg", "onboarding"], vendor: "Paessler", minutes: 60, stepCount: 12 },
  { number: "SOP-009", name: "Qualys Missing Critical Patch", description: "Remediate servers missing critical patches per Qualys scan results.", category: "security", tags: ["qualys", "patching"], vendor: "Qualys", approval: true, minutes: 90, stepCount: 9 },
  { number: "SOP-010", name: "Server/Device Lifecycle Management", description: "Track capacity headroom and decommission or resize aging devices.", category: "capacity", tags: ["lifecycle", "capacity"], minutes: 50, stepCount: 8 },
  { number: "SOP-011", name: "UPS Self-Test Failure", description: "Investigate failed UPS self-tests and replace at-risk battery units.", category: "availability", tags: ["ups", "power"], minutes: 40, stepCount: 7 },
  { number: "SOP-012", name: "Disk Capacity Threshold Breach", description: "Free space or extend volumes when disk usage crosses plan limits.", category: "capacity", tags: ["disk", "threshold"], approval: true, minutes: 35, stepCount: 8 },
  { number: "SOP-013", name: "Brute-Force Login Investigation", description: "Correlate repeated auth failures and block offending sources.", category: "security", tags: ["auth", "bruteforce"], minutes: 30, stepCount: 9 },
  { number: "SOP-014", name: "WAN Latency Degradation", description: "Diagnose rising WAN latency between sites and shift traffic paths.", category: "performance", tags: ["latency", "wan"], minutes: 45, stepCount: 10 },
].map(buildSop);

/** Category accent colors used by the SOP card badges (spec color coding). */
export const SOP_CATEGORY_COLORS: Record<string, string> = {
  availability: "#ff4d4f",
  capacity: "#00d38d",
  performance: "#ffb200",
  connectivity: "#3aa9ff",
  security: "#9b6bff",
  configuration: "#22d3ee",
  other: "#82a0ac",
};
