/**
 * Typed client for the Palo Alto Diagnostic API.
 *
 * Follows lib/api.ts conventions: base URL + token from shared helpers,
 * tenant resolved from the JWT, predictable error responses.
 */
import { apiFetch, API_BASE_URL } from "@/lib/api";

export interface PaRunbookStep {
  id: string;
  instruction: string;
  expected_outcome: string;
  abnormal_outcome?: string | null;
  evidence_requirement?: string | null;
  risk: string;
}

export interface PaRunbook {
  id: string;
  title: string;
  topic: string;
  version: string;
  applicable_symptoms: string[];
  prerequisites: string[];
  steps: PaRunbookStep[];
  safe_remediation: string[];
  approval_required: boolean;
  rollback_instructions?: string | null;
  post_fix_verification: string[];
  escalation_criteria: string[];
}

export interface PaRunbookMatch {
  runbook: PaRunbook;
  confidence_score: number;
  matched_symptoms: string[];
}

export interface PaWorkspace {
  device_id?: string | null;
  hostname?: string | null;
  vendor?: string | null;
  model?: string | null;
  panos_version?: string | null;
  health?: string | null;
  data_quality: Record<string, string>;
}

const BASE = `${API_BASE_URL}/api/paloalto`;

export async function fetchPaRunbooks(q?: string): Promise<PaRunbook[]> {
  const url = q ? `${BASE}/runbooks?q=${encodeURIComponent(q)}` : `${BASE}/runbooks`;
  return apiFetch<PaRunbook[]>(url);
}

export async function matchPaRunbooks(symptoms: string[]): Promise<PaRunbookMatch[]> {
  return apiFetch<PaRunbookMatch[]>(`${BASE}/runbooks/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symptoms }),
  });
}

export async function fetchPaWorkspace(): Promise<PaWorkspace> {
  return apiFetch<PaWorkspace>(`${BASE}/workspace`);
}

export async function createPaExecution(body: Record<string, unknown>): Promise<unknown> {
  return apiFetch<unknown>(`${BASE}/executions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
