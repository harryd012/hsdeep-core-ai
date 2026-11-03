/**
 * Enterprise security programme S1: sessions and the tenant audit log.
 * Mirrors backend/app/schemas/auth.py (AuthSessionOut) and
 * backend/app/schemas/audit_log.py. The tenant always comes from the signed-in
 * user on the server; nothing here sends a tenant id.
 */
import {
  API_BASE_URL,
  ApiError,
  apiFetch,
  ensureAuthenticated,
  getActiveTenantOverride,
  readErrorDetail,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export interface AuthSession {
  session_id: string;
  started_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  /** The session making the request (this browser). */
  current: boolean;
}

export function listMySessions(): Promise<AuthSession[]> {
  return apiFetch<AuthSession[]>("/api/auth/sessions");
}

export function endMySession(sessionId: string): Promise<void> {
  return apiFetch<void>(`/api/auth/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export function endMyOtherSessions(): Promise<{ sessions_ended: number }> {
  return apiFetch<{ sessions_ended: number }>("/api/auth/sessions/end-others", { method: "POST" });
}

export function signOutEverywhere(): Promise<void> {
  return apiFetch<void>("/api/auth/logout-all", { method: "POST" });
}

/** "Chrome on Windows" from a user-agent string; null when it says nothing useful. */
export function describeDevice(ua: string | null): string | null {
  if (!ua) return null;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : null;
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad|iOS/.test(ua)
        ? "iOS"
        : /Mac OS X|Macintosh/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : null;
  if (browser && os) return `${browser} on ${os}`;
  return browser ?? os ?? (ua.length > 60 ? `${ua.slice(0, 57)}…` : ua);
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

export interface AuditEvent {
  id: string;
  created_at: string;
  action: string;
  actor_id: string | null;
  actor_email: string | null;
  actor_type: string;
  entity_type: string | null;
  entity_id: string | null;
  description: string | null;
  old_values: Record<string, unknown>;
  new_values: Record<string, unknown>;
  ip_address: string | null;
}

export interface AuditPage {
  total: number;
  limit: number;
  offset: number;
  rows: AuditEvent[];
}

export interface AuditFilters {
  /** Exact action, or a prefix ending in "." ("user."). */
  action?: string;
  actor?: string;
  entity_type?: string;
  since?: string;
  q?: string;
}

function auditQuery(filters: AuditFilters, extra: Record<string, string | number> = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...filters, ...extra })) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export function listAuditEvents(filters: AuditFilters, limit: number, offset: number): Promise<AuditPage> {
  return apiFetch<AuditPage>(`/api/audit/events${auditQuery(filters, { limit, offset })}`);
}

export function getAuditFacets(): Promise<{ actions: string[]; entity_types: string[] }> {
  return apiFetch("/api/audit/events/facets");
}

/** Download the filtered audit log as CSV (the export is itself audited). */
export async function downloadAuditCsv(filters: AuditFilters): Promise<void> {
  const token = await ensureAuthenticated();
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const tenant = getActiveTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  const path = `/api/audit/events/export${auditQuery(filters)}`;
  const res = await fetch(`${API_BASE_URL}${path}`, { headers, cache: "no-store" });
  if (!res.ok) throw new ApiError(path, res.status, await readErrorDetail(res));
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "audit-log.csv";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Multi-factor authentication (security programme S2)
// ---------------------------------------------------------------------------

export interface MfaStatus {
  enabled: boolean;
  confirmed_at: string | null;
  recovery_codes_remaining: number;
  setup_pending: boolean;
  /** The organization requires MFA for everyone. */
  required_by_policy: boolean;
}

export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
  /** Server-rendered QR code (SVG markup) for the otpauth URI. */
  qr_svg: string;
}

export function getMfaStatus(): Promise<MfaStatus> {
  return apiFetch<MfaStatus>("/api/auth/mfa");
}

export function startMfaSetup(): Promise<MfaSetup> {
  return apiFetch<MfaSetup>("/api/auth/mfa/setup", { method: "POST" });
}

/** Turns MFA on; returns the recovery codes (shown once). */
export function confirmMfa(code: string): Promise<{ codes: string[] }> {
  return apiFetch<{ codes: string[] }>("/api/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code }) });
}

export function regenerateRecoveryCodes(code: string): Promise<{ codes: string[] }> {
  return apiFetch<{ codes: string[] }>("/api/auth/mfa/recovery-codes", { method: "POST", body: JSON.stringify({ code }) });
}

export function disableMfa(code: string): Promise<void> {
  return apiFetch<void>("/api/auth/mfa/disable", { method: "POST", body: JSON.stringify({ code }) });
}

/** Admin (security.manage): remove a user's MFA and end their sessions. */
export function resetUserMfa(userId: string): Promise<{ mfa_removed: boolean; sessions_ended: number }> {
  return apiFetch(`/api/auth/users/${encodeURIComponent(userId)}/mfa/reset`, { method: "POST" });
}

/** "ABCD EFGH ..." for typing a secret into an app by hand. */
export function groupSecret(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}
