/**
 * Enterprise identity (security programme S6): verified domains, OIDC single
 * sign-on and SCIM. Mirrors backend/app/api/routes/identity.py.
 */
import { API_BASE_URL, ApiError, apiFetch, readErrorDetail } from "@/lib/api";

export interface IdentityDomain {
  id: string;
  domain: string;
  verified: boolean;
  verified_at: string | null;
  /** DNS TXT record to publish to prove ownership. */
  txt_name: string;
  txt_value: string;
}

export interface SsoConfig {
  protocol: "oidc";
  issuer: string | null;
  client_id: string | null;
  client_secret_set: boolean;
  enabled: boolean;
  require_sso: boolean;
  jit_provisioning: boolean;
  default_role_id: string | null;
  /** Register this redirect URI with your identity provider. */
  redirect_uri: string;
  scim_enabled: boolean;
  scim_token_prefix: string | null;
  scim_base_url: string;
}

export interface Identity {
  domains: IdentityDomain[];
  sso: SsoConfig;
}

export interface SsoUpdate {
  issuer?: string | null;
  client_id?: string | null;
  /** Write-only; omit to keep, "" to remove. */
  client_secret?: string;
  enabled?: boolean;
  require_sso?: boolean;
  jit_provisioning?: boolean;
  default_role_id?: string | null;
}

const BASE = "/api/settings/identity";

export const getIdentity = () => apiFetch<Identity>(BASE);
export const addDomain = (domain: string) => apiFetch<IdentityDomain>(`${BASE}/domains`, { method: "POST", body: JSON.stringify({ domain }) });
export const verifyDomain = (id: string) => apiFetch<IdentityDomain>(`${BASE}/domains/${encodeURIComponent(id)}/verify`, { method: "POST" });
export const removeDomain = (id: string) => apiFetch<void>(`${BASE}/domains/${encodeURIComponent(id)}`, { method: "DELETE" });
export const updateSso = (body: SsoUpdate) => apiFetch<Identity>(`${BASE}/sso`, { method: "PUT", body: JSON.stringify(body) });
export const createScimToken = () => apiFetch<{ token: string; prefix: string }>(`${BASE}/scim-token`, { method: "POST" });
export const revokeScimToken = () => apiFetch<void>(`${BASE}/scim-token`, { method: "DELETE" });

/** Public: the identity provider's sign-in URL for this email's organization. */
export async function startSso(email: string, next?: string): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/auth/sso/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, next: next || null }),
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError("/api/auth/sso/start", res.status, await readErrorDetail(res));
  return ((await res.json()) as { redirect_url: string }).redirect_url;
}
