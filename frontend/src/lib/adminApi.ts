/**
 * Platform-admin API -- tenant/customer administration.
 *
 * Transport comes from ./api; this module owns the admin surface only. These
 * endpoints are superuser-gated server-side (require_admin) and the tenant is
 * named in the path, so a call can only ever touch the tenant it names -- there
 * is no cross-tenant override here. The backend stays authoritative for
 * authorisation and tenant isolation; nothing here re-implements either.
 *
 * Shapes mirror the backend responses exactly: no business calculation is
 * duplicated, so these views cannot drift from the billing/usage endpoints.
 */
import { apiFetch } from "./api";

// ---------------------------------------------------------------------------
// Platform-admin customer administration
// ---------------------------------------------------------------------------

/** One tenant row from GET /api/billing/admin/customers (superuser only). */
export interface AdminCustomer {
  tenant_id: string;
  name: string;
  slug: string;
  plan_name: string | null;
  plan_tier: string | null;
  subscription_status: string;
  trial_ends_at: string | null;
  over_limit_blocked: boolean;
  created_at: string | null;
  last_activity_at: string | null;
}

/**
 * Platform-admin tenant/customer list.
 *
 * Uses the app's authenticated client so the request carries the real
 * `Authorization: Bearer` token. The endpoint is gated server-side by
 * `require_admin` (superuser); the backend remains authoritative - this call
 * simply stops the UI from silently failing to authenticate.
 *
 * Every value is canonical: the tenant, plan and subscription come from the
 * existing billing service, so these figures cannot drift from the billing and
 * usage screens.
 */
export function listAdminCustomers(): Promise<AdminCustomer[]> {
  return apiFetch<AdminCustomer[]>("/api/billing/admin/customers");
}

// ---------------------------------------------------------------------------
// Platform-admin tenant detail (GET /api/admin/tenants/{tenant_id})
//
// Shapes mirror the backend response EXACTLY. No business calculation is
// duplicated here: pct/used/limit/over_limit are rendered as returned by
// BillingService.get_entitlements(), so this view cannot disagree with /usage.
// ---------------------------------------------------------------------------

export interface AdminTenantSummary {
  tenant_id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string | null;
}

export interface AdminTenantProfile {
  tenant_id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
  /** Null when the canonical Tenant model has no such property. */
  display_name: string | null;
  legal_name: string | null;
  industry: string | null;
  country: string | null;
  timezone: string | null;
  locale: string | null;
  contacts: Record<string, unknown> | null;
  branding: Record<string, unknown> | null;
  custom_properties: Record<string, unknown> | null;
}

export interface AdminTenantLimit {
  resource: string;
  label: string;
  limit: number;
  used: number;
  pct: number;
  over_limit: boolean;
}

export interface AdminTenantSubscription {
  plan_name: string | null;
  plan_tier: string | null;
  subscription_status: string | null;
  billing_interval: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  trial_ends_at: string | null;
  /** Null means the canonical service could not answer - not "false". */
  over_limit: boolean | null;
  over_limit_blocked: boolean | null;
  limits: AdminTenantLimit[];
}

export interface AdminTenantUsage {
  label: string;
  used: number;
  limit: number;
  pct: number;
  over_limit: boolean;
}

export interface AdminTenantCounts {
  users_total: number;
  users_active: number;
  users_inactive: number;
  roles_total: number;
  devices_total: number;
  discovery_scopes_total: number;
}

export interface AdminTenantDiscoveryScope {
  id: string;
  name: string;
  scope_type: string;
  target: string;
  enabled: boolean;
  allowed_protocols: string[];
  credential_configured: boolean;
  status: string;
  last_run_at: string | null;
}

export interface AdminTenantPermission {
  id: string;
  key: string;
  name: string;
  description: string | null;
}

export interface AdminTenantRole {
  id: string;
  name: string;
  description: string | null;
  /** "tenant" - these are tenant-scoped roles, never platform-admin grants. */
  scope: string;
  user_count: number;
  permissions: AdminTenantPermission[];
}

export interface AdminTenantUser {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  last_login_at: string | null;
  created_at: string | null;
  roles: string[];
}

export interface AdminTenantUsers {
  total: number;
  returned: number;
  truncated: boolean;
  items: AdminTenantUser[];
}

export interface AdminTenantDetail {
  profile: AdminTenantProfile;
  subscription: AdminTenantSubscription;
  usage: Record<string, AdminTenantUsage>;
  counts: AdminTenantCounts;
  last_activity_at: string | null;
  discovery_scopes: AdminTenantDiscoveryScope[];
  /** Tenant-scoped roles with their permissions. Read-only. */
  roles: AdminTenantRole[];
  /** Tenant-scoped users with role NAMES. Read-only; never credentials. */
  users: AdminTenantUsers;
}

/** Detail for ONE explicitly named tenant. Backend is superuser-gated. */
export function getAdminTenantDetail(tenantId: string): Promise<AdminTenantDetail> {
  return apiFetch<AdminTenantDetail>(`/api/admin/tenants/${tenantId}`);
}

/** Server-confirmed outcome of a tenant rename (Phase 6.3F). */
export interface AdminTenantRenameResult {
  tenant_id: string;
  /** false = the submitted values already matched; nothing was written/audited. */
  changed: boolean;
  before: { name: string; slug: string };
  after: { name: string; slug: string };
}

/**
 * Rename ONE tenant (PATCH /api/admin/tenants/{tenant_id}).
 *
 * Backend-gated by `require_admin`. The tenant is named in the path, so the call
 * can only ever touch that tenant - there is no X-Tenant-Id override here.
 * The response carries the canonical before/after values, which the UI displays
 * verbatim rather than guessing what changed.
 */
export function renameAdminTenant(
  tenantId: string,
  payload: { name?: string; slug?: string },
): Promise<AdminTenantRenameResult> {
  return apiFetch<AdminTenantRenameResult>(`/api/admin/tenants/${tenantId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** Server-confirmed outcome of a tenant lifecycle change (Phase 6.4A). */
export interface AdminTenantStatusResult {
  tenant_id: string;
  /** false = the tenant was ALREADY in the requested state (no write, no audit). */
  changed: boolean;
  before: { is_active: boolean };
  after: { is_active: boolean };
}

/**
 * Suspend (`is_active: false`) or reactivate (`is_active: true`) ONE tenant.
 *
 * Lifecycle state ONLY: the backend flips `tenants.is_active` and touches nothing
 * the tenant owns - users, roles, permissions, custom properties, discovery
 * scopes and history all remain intact. Gated by `require_admin`, and the tenant
 * is named in the path, so the call can only ever affect that tenant.
 */
export function setAdminTenantStatus(
  tenantId: string,
  isActive: boolean,
): Promise<AdminTenantStatusResult> {
  return apiFetch<AdminTenantStatusResult>(`/api/admin/tenants/${tenantId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

