/**
 * Privacy & compliance (enterprise security programme S5): AI data use,
 * retention, data export and offboarding. Mirrors backend/app/api/routes/privacy.py.
 */
import { API_BASE_URL, ApiError, apiFetch, ensureAuthenticated, getActiveTenantOverride, readErrorDetail } from "@/lib/api";

export interface PrivacySettings {
  ai_mode: "on" | "off";
  ai_redact_emails: boolean;
  ai_redact_ips: boolean;
  metrics_retention_days: number | null;
  events_retention_days: number | null;
  /** Platform default used when the tenant sets none (null = kept). */
  platform_metrics_retention_days: number | null;
  deletion_requested_at: string | null;
  deletion_scheduled_for: string | null;
  deletion_requested_by: string | null;
  is_default: boolean;
}

export type PrivacyUpdate = Partial<Pick<PrivacySettings,
  "ai_mode" | "ai_redact_emails" | "ai_redact_ips" | "metrics_retention_days" | "events_retention_days">>;

export interface TenantExport {
  id: string;
  status: "pending" | "running" | "ready" | "failed" | "expired";
  requested_by: string | null;
  size_bytes: number | null;
  summary: Record<string, number>;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
}

const BASE = "/api/settings/privacy";

export function getPrivacy(): Promise<PrivacySettings> {
  return apiFetch<PrivacySettings>(BASE);
}

export function updatePrivacy(body: PrivacyUpdate): Promise<PrivacySettings> {
  return apiFetch<PrivacySettings>(BASE, { method: "PUT", body: JSON.stringify(body) });
}

export function listExports(): Promise<TenantExport[]> {
  return apiFetch<TenantExport[]>(`${BASE}/exports`);
}

export function requestExport(): Promise<TenantExport> {
  return apiFetch<TenantExport>(`${BASE}/exports`, { method: "POST" });
}

export function requestDeletion(confirmName: string): Promise<PrivacySettings> {
  return apiFetch<PrivacySettings>(`${BASE}/deletion`, { method: "POST", body: JSON.stringify({ confirm_name: confirmName }) });
}

export function cancelDeletion(): Promise<PrivacySettings> {
  return apiFetch<PrivacySettings>(`${BASE}/deletion`, { method: "DELETE" });
}

/** Download a ready export (authenticated; the download is audited). */
export async function downloadExport(id: string): Promise<void> {
  const token = await ensureAuthenticated();
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const tenant = getActiveTenantOverride();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  const path = `${BASE}/exports/${encodeURIComponent(id)}/download`;
  const res = await fetch(`${API_BASE_URL}${path}`, { headers, cache: "no-store" });
  if (!res.ok) throw new ApiError(path, res.status, await readErrorDetail(res));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = /filename="?([^";]+)"?/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "hsdeep-export.zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
