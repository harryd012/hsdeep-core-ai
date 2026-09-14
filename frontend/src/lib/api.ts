/**
 * Typed fetch client for the HSDEEP CORE AI backend.
 *
 * This is the first real backend wiring point in the frontend - every panel
 * that talks to the API should go through here rather than calling fetch()
 * directly, so the base URL, error handling, and tenant-id convention stay
 * in one place.
 *
 * AUTHENTICATION MODEL:
 * - Routes that use `Depends(get_tenant_id)` resolve tenant from the JWT token
 *   automatically. The tenant_id query parameter is NOT used for these routes.
 * - Routes still using NOTE(auth) legacy pattern accept tenant_id as a query param.
 *   These are being migrated; see backend audit docs.
 *
 * TOKEN MANAGEMENT:
 * - Call `setAuthToken()` after login to enable authenticated requests.
 * - The token is persisted in localStorage.
 * - Call `getAuthToken()` to check if a token exists.
 *
 * TENANT ID:
 * - Do NOT use DEFAULT_TENANT_ID for authenticated API calls.
 * - Use useAuth().tenantId from authcontext.tsx for the current tenant.
 * - DEFAULT_TENANT_ID is kept only for transitional unauthenticated routes.
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * DEFAULT_TENANT_ID is deprecated for authenticated API calls.
 *
 * For routes that use Depends(get_tenant_id), the tenant is resolved from
 * the JWT token, NOT from this parameter. New code should derive tenant
 * from the auth context (getCurrentTenantId from auth-context.tsx).
 *
 * This constant remains for:
 * - Non-authenticated routes (if any exist)
 * - Server-side rendering contexts
 * - Transitional migration period
 */
export const DEFAULT_TENANT_ID = process.env.NEXT_PUBLIC_DEFAULT_TENANT_ID ?? "00000000-0000-0000-0000-000000000000";

const AUTH_TOKEN_KEY = "hsdeep_auth_token";

/**
 * Dev-mode auto-login bootstrap.
 *
 * A real /login page + AuthProvider now exist (see lib/auth-context.tsx and
 * app/login/page.tsx) and this product now targets real customer logins
 * (MSP/Enterprise IT tenants), so this bootstrap is OPT-IN ONLY - it must
 * be explicitly enabled with NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP=true and is OFF
 * by default in every environment, including `next dev`.
 *
 * HISTORY / WHY THIS CHANGED: the previous default ("on unless explicitly
 * disabled", then "on in dev, off in production") caused a real, observed
 * bug: apiFetch() calls ensureAuthenticated() on every request, and
 * AuthProvider's first getMe() call fires the instant /login mounts. With
 * bootstrap defaulting to true, that call silently minted a session for the
 * seeded admin BEFORE a real user's typed credentials were ever submitted -
 * so /login would auto-redirect to /dashboard logged in as
 * admin@hsdeep.local no matter what the visitor actually typed. A real
 * login page must never race a silent bootstrap login. If you want the old
 * convenience for pure local API scripting, set
 * NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP=true in your own .env.local — it will
 * never turn on by itself again.
 */
export const AUTH_BOOTSTRAP = process.env.NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP === "true";

export const AUTH_EMAIL = process.env.NEXT_PUBLIC_AUTH_EMAIL ?? "admin@hsdeep.local";
export const AUTH_PASSWORD = process.env.NEXT_PUBLIC_AUTH_PASSWORD ?? "ChooseAStrongPasswordHere123!";
export const AUTH_TENANT_ID = process.env.NEXT_PUBLIC_AUTH_TENANT_ID ?? DEFAULT_TENANT_ID;

// ---------------------------------------------------------------------------
// Auth token management
// ---------------------------------------------------------------------------

export function setAuthToken(token: string): void {
  if (typeof window !== "undefined") {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
  }
}

export function getAuthToken(): string | null {
  if (typeof window !== "undefined") {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  }
  return null;
}

export function clearAuthToken(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginCredentials {
  tenantId?: string;
  email?: string;
  password?: string;
}

/**
 * Authenticate against POST /api/auth/login and persist the access token.
 *
 * IMPORTANT: tenant_id is only sent when the caller explicitly provides one
 * (e.g. the dev bootstrap, or a superuser switching tenants). A real end
 * user typing their own email/password NEVER has a tenant_id to supply -
 * the backend resolves it server-side from a global email lookup (see
 * AuthService.login). Previously this always defaulted to a hardcoded
 * tenant UUID, which silently broke login for every real user outside that
 * one default tenant. Do not reintroduce that default.
 */
export async function loginWithCredentials(creds: LoginCredentials = {}): Promise<LoginResponse> {
  const body: Record<string, string> = {
    email: creds.email ?? AUTH_EMAIL,
    password: creds.password ?? AUTH_PASSWORD,
  };
  if (creds.tenantId) {
    body.tenant_id = creds.tenantId;
  }
  const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError("/api/auth/login", res.status, await readErrorDetail(res));
  }
  const data = (await res.json()) as LoginResponse;
  setAuthToken(data.access_token);
  return data;
}

// ---------------------------------------------------------------------------
// Self-service signup (POST /api/billing/signup)
//
// The backend endpoint already existed (tenant + Owner user + trial
// subscription + token issuance, rate-limited 5/IP/hour) - this was simply
// never wired up from the frontend, so there was no way for a real customer
// to create an account at all (POST /api/auth/users requires an existing
// authenticated tenant admin - a chicken-and-egg problem for a brand new
// company). See app/api/routes/billing.py::signup for the backend contract.
// ---------------------------------------------------------------------------

export interface SignupRequest {
  org_name: string;
  email: string;
  password: string;
  full_name?: string;
  plan_tier?: string;
}

export interface SignupResponse {
  tenant_id: string;
  user_id: string;
  email: string;
  plan_tier: string;
  subscription_status: string;
  trial_ends_at: string | null;
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export async function signupTenant(payload: SignupRequest): Promise<SignupResponse> {
  const res = await fetch(`${API_BASE_URL}/api/billing/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_tier: "starter", ...payload }),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError("/api/billing/signup", res.status, await readErrorDetail(res));
  }
  const data = (await res.json()) as SignupResponse;
  setAuthToken(data.access_token);
  return data;
}

/**
 * Minimal JWT payload decoder (no signature verification — we only read
 * the `exp` claim client-side to decide whether to proactively drop an
 * expired token before it triggers a needless 401 round-trip.
 *
 * See BUG 1 (auth-401-repro.js): without this, a stale/expired JWT left
 * in localStorage from a previous session causes getMe() to fire an HTTP
 * request that the backend rejects with 401, which then cascades into an
 * AuthGuard redirect to /login?next=….
 */
function decodeJwtPayload(token: string): { exp?: number; [k: string]: unknown } | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // JWT payloads are base64url-encoded; atob expects standard base64.
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(base64);
    return JSON.parse(json) as { exp?: number; [k: string]: unknown };
  } catch {
    return null;
  }
}

let authPromise: Promise<string> | null = null;

/**
 * Returns a valid Bearer token, performing the dev-bootstrap login once when
 * no token is cached yet. Concurrent callers share a single in-flight login.
 */
export async function ensureAuthenticated(): Promise<string> {
  const existing = getAuthToken();
  if (existing) {
    // Proactively check token expiry so we never fire a doomed request
    // that the backend will reject with 401 (BUG 1 — auth-401-repro.js).
    // If the token is expired, clear it locally and fall through to either
    // the dev-bootstrap login or a thrown 401 — whichever applies.
    const decoded = decodeJwtPayload(existing);
    const now = Math.floor(Date.now() / 1000);
    if (decoded && typeof decoded.exp === "number" && decoded.exp <= now) {
      clearAuthToken();
    } else {
      return existing;
    }
  }

  if (!AUTH_BOOTSTRAP) {
    throw new ApiError("/api/auth/login", 401, "Session expired - please log in again.");
  }

  if (!authPromise) {
    authPromise = loginWithCredentials()
      .then((r) => r.access_token)
      .finally(() => {
        authPromise = null;
      });
  }
  return authPromise;
}

export type AlertSeverity = "critical" | "warning" | "info";
export type AlertStatus = "open" | "acknowledged" | "resolved";

export interface Alert {
  id: string;
  tenant_id: string;
  site_id: string | null;
  device_id: string | null;
  sensor_id: string | null;
  monitoring_source_id: string | null;
  title: string;
  description: string | null;
  severity: AlertSeverity;
  status: AlertStatus;
  metric_type: string | null;
  source: string;
  triggered_value: number | null;
  threshold_value: number | null;
  triggered_at: string;
  last_occurred_at: string;
  occurrence_count: number;
  acknowledged_at: string | null;
  resolved_at: string | null;
  auto_resolved: boolean;
  device_name: string | null;
  site_name: string | null;
  sensor_name: string | null;
}

export interface AlertSummary {
  total: number;
  by_severity: { critical: number; warning: number; info: number };
  by_status: { open: number; acknowledged: number; resolved: number };
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
export interface DashboardSummary {
  sites: number;
  devices: number;
  monitoring_sources: number;
  sensors: number;
  cloud_resources: number;
}

export interface CloudVendorStatus {
  vendor: string;
  resource_count: number;
  status: "operational" | "degraded" | "error";
}

export interface CloudStatus {
  vendors: CloudVendorStatus[];
  total_resources: number;
}

export interface NetworkStatus {
  sites_total: number;
  sites_online: number;
  devices_total: number;
  devices_up: number;
  devices_down: number;
}

export interface SourceRunHealth {
  source_id: string;
  source_name: string;
  status: string;
  last_run_at: string | null;
  last_run_status: string | null;
  next_run_at: string | null;
}

export interface AutomationStatus {
  sources_total: number;
  sources_connected: number;
  sources_error: number;
  runs_last_24h: number;
  runs_succeeded_last_24h: number;
  success_rate_pct: number;
  sources: SourceRunHealth[];
}

export interface AIInsight {
  kind: "sustained_critical" | "flapping_sensor" | "collector_failing" | "no_data";
  severity: "critical" | "warning" | "info";
  message: string;
  device_id: string | null;
  monitoring_source_id: string | null;
}

export interface AIInsights {
  generated_at: string;
  insights: AIInsight[];
}

// ---------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------
export interface MonitoringSourceOut {
  id: string;
  tenant_id: string;
  site_id: string | null;
  name: string;
  source_type: string;
  config: Record<string, unknown>;
  status: string;
  last_sync_at: string | null;
  last_error: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  vendor: string | null;
  hostname: string | null;
  ip_address: string | null;
  port: number | null;
  protocol: string | null;
  verify_ssl: boolean | null;
  snmp_version: string | null;
  username: string | null;
  poll_interval_seconds: number;
}

export interface SensorOut {
  id: string;
  tenant_id: string;
  monitoring_source_id: string;
  device_id: string | null;
  external_id: string;
  name: string;
  metric_type: string;
  unit: string | null;
  current_value: number | null;
  warning_threshold: number | null;
  critical_threshold: number | null;
  status: string;
  last_value_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrtgSensorOut {
  name: string;
  status: string;
  status_raw: string | null;
  lastvalue: string | null;
  message: string | null;
  objid: string | null;
  baselink: string | null;
  probe_group_device: string | null;
  source: string;
}

export function listMonitoringSources(tenantId: string = DEFAULT_TENANT_ID): Promise<MonitoringSourceOut[]> {
  return apiFetch<MonitoringSourceOut[]>(`/api/monitoring/sources?${withTenant(tenantId).toString()}`);
}

export function listSensors(tenantId: string = DEFAULT_TENANT_ID): Promise<SensorOut[]> {
  return apiFetch<SensorOut[]>(`/api/monitoring/sensors?${withTenant(tenantId).toString()}`);
}

export function listPrtgRootSensors(): Promise<PrtgSensorOut[]> {
  return apiFetch<PrtgSensorOut[]>('/api/monitoring/prtg/root-sensors');
}

// ---------------------------------------------------------------------------
// Topology
// ---------------------------------------------------------------------------
export interface SensorNode {
  id: string;
  name: string;
  metric_type?: string | null;
  status?: string | null;
  last_value_at?: string | null;
}

export interface MonitoringSourceNode {
  id: string;
  name: string;
  source_type: string;
  status?: string | null;
  sensors: SensorNode[];
}

export interface DeviceNode {
  id: string;
  name: string;
  device_type?: string | null;
  hostname?: string | null;
  ip_address?: string | null;
  vendor?: string | null;
  status?: string | null;
}

export interface SiteNode {
  id: string;
  name: string;
  code?: string | null;
  address?: string | null;
  region?: string | null;
  devices: DeviceNode[];
  monitoring_sources: MonitoringSourceNode[];
}

export interface TopologyOut {
  tenant_id: string;
  generated_at: string;
  sites: SiteNode[];
}

/**
 * Fetch tenant-scoped infrastructure topology from the backend.
 * The backend currently returns sites -> devices + monitoring_sources -> sensors.
 * If levels are missing for a tenant, the response will honestly reflect empty arrays.
 */
export function getTopology(tenantId: string = DEFAULT_TENANT_ID): Promise<TopologyOut> {
  return apiFetch<TopologyOut>(`/api/infrastructure/topology?${withTenant(tenantId).toString()}`);
}

// ---------------------------------------------------------------------------
// Devices / Events / Collector Runs
// ---------------------------------------------------------------------------
export interface Device {
  id: string;
  tenant_id: string;
  site_id: string | null;
  monitoring_source_id: string | null;
  external_id: string | null;
  name: string;
  device_type: string;
  vendor: string | null;
  model: string | null;
  os_version: string | null;
  hostname: string | null;
  ip_address: string | null;
  mac_address: string | null;
  status: string;
  serial_number: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Event {
  id: string;
  tenant_id: string;
  device_id: string | null;
  monitoring_source_id: string | null;
  external_id: string | null;
  event_type: string;
  severity: string;
  message: string;
  occurred_at: string;
  raw_payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CollectorRun {
  id: string;
  tenant_id: string;
  monitoring_source_id: string;
  trigger: "scheduler" | "manual";
  status: "running" | "success" | "partial" | "error" | "skipped";
  error: string | null;
  devices_upserted: number;
  interfaces_upserted: number;
  sensors_upserted: number;
  metrics_recorded: number;
  events_recorded: number;
  alerts_upserted: number;
  started_at: string;
  finished_at: string | null;
}

export function getTenantId(): string {
  // Prefer the active-tenant override (superuser tenant switching) when set.
  // Falls back to the dev DEFAULT_TENANT_ID only when no override exists.
  // In production with auth, the mounted routes use Depends(get_tenant_id) so
  // the query-param tenant_id is redundant — but the frontend keeps sending it
  // for backward compatibility with any route that still expects it.
  const override = getActiveTenantOverride();
  if (override) return override;

  // Note: AuthContext is not accessed here because api.ts is a plain utility
  // module — React context can only be consumed inside React components/hooks.
  // Use useTenantId() from within components to get the authenticated tenant.

  return DEFAULT_TENANT_ID;
}

/**
 * React hook that returns the current authenticated tenant ID.
 *
 * This is the preferred way to get the tenant ID in components.
 * It will:
 * - Return the user's tenant_id if logged in
 * - Return the active tenant override if set (superuser switching)
 * - Throw if called outside AuthProvider
 *
 * @example
 * ```tsx
 * const { tenantId } = useAuth(); // or
 * const tenantId = useTenantId();
 * ```
 */
export function useTenantId(): string {
  // Lazy import to avoid circular dependency between api.ts and auth-context.tsx
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { useAuth } = require("./auth-context");
  const { tenantId } = useAuth();
  return tenantId;
}

// ---------------------------------------------------------------------------
// Current user / RBAC
// ---------------------------------------------------------------------------

export interface AccessibleTenant {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  role: string | null;
}

export interface MeResponse {
  user_id: string;
  tenant_id: string;
  email: string;
  full_name: string | null;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
  // -- P0 tenant context (authoritative backend data) --
  tenant_name: string | null;
  accessible_tenants: AccessibleTenant[];
}

export function getMe(): Promise<MeResponse> {
  return apiFetch<MeResponse>("/api/auth/me");
}

// ---------------------------------------------------------------------------
// Active-tenant override (P0 tenant switching)
//
// The backend's existing interim MSP model lets a superuser act on any tenant
// via the X-Tenant-Id header (validated + audited server-side). The frontend
// persists the user's chosen override here and attaches it to every request.
// For normal users this stays null (they're pinned to their own tenant).
// ---------------------------------------------------------------------------

const ACTIVE_TENANT_KEY = "hsdeep_active_tenant";

export function getActiveTenantOverride(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_TENANT_KEY);
}

export function setActiveTenantOverride(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  if (tenantId) {
    localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
  } else {
    localStorage.removeItem(ACTIVE_TENANT_KEY);
  }
}

export function clearActiveTenantOverride(): void {
  setActiveTenantOverride(null);
}

export interface UserOut {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  last_login_at: string | null;
  created_at: string;
  roles: string[];
}

export interface RoleOut {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  permission_codes: string[];
}

export function listUsers(tenantId: string = DEFAULT_TENANT_ID): Promise<UserOut[]> {
  return apiFetch<UserOut[]>(`/api/auth/users?${withTenant(tenantId).toString()}`);
}

export function listRoles(tenantId: string = DEFAULT_TENANT_ID): Promise<RoleOut[]> {
  return apiFetch<RoleOut[]>(`/api/auth/roles?${withTenant(tenantId).toString()}`);
}

export interface GeneralSettings {
  id: string;
  tenant_id: string;
  platform_name: string;
  timezone: string;
  branding_logo_url: string | null;
  primary_color: string | null;
  locale: string;
  created_at: string;
  updated_at: string;
}

export interface GeneralSettingsUpdate {
  platform_name?: string;
  timezone?: string;
  branding_logo_url?: string | null;
  primary_color?: string | null;
  locale?: string;
}

export interface SecuritySettings {
  id: string;
  tenant_id: string;
  min_password_length: number;
  require_uppercase: boolean;
  require_numbers: boolean;
  require_special_chars: boolean;
  mfa_enabled: boolean;
  session_timeout_minutes: number;
  max_login_attempts: number;
  created_at: string;
  updated_at: string;
}

export interface SecuritySettingsUpdate {
  min_password_length?: number;
  require_uppercase?: boolean;
  require_numbers?: boolean;
  require_special_chars?: boolean;
  mfa_enabled?: boolean;
  session_timeout_minutes?: number;
  max_login_attempts?: number;
}

export interface NotificationSettings {
  id: string;
  tenant_id: string;
  email_enabled: boolean;
  email_recipients: string | null;
  slack_enabled: boolean;
  slack_webhook_url: string | null;
  slack_channel: string | null;
  pagerduty_enabled: boolean;
  pagerduty_integration_key: string | null;
  pagerduty_severity: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationSettingsUpdate {
  email_enabled?: boolean;
  email_recipients?: string | null;
  slack_enabled?: boolean;
  slack_webhook_url?: string | null;
  slack_channel?: string | null;
  pagerduty_enabled?: boolean;
  pagerduty_integration_key?: string | null;
  pagerduty_severity?: string | null;
}

export interface ApiKey {
  id: string;
  tenant_id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  is_active: boolean;
}

export interface ApiKeyCreateRequest {
  name: string;
  scopes?: string[];
  expires_at?: string | null;
}

export interface ApiKeyCreateResponse {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  api_key: string;
  created_at: string;
  expires_at: string | null;
}

export interface PermissionOut {
  id: string;
  code: string;
  description: string | null;
}

export function listPermissions(tenantId: string = DEFAULT_TENANT_ID): Promise<PermissionOut[]> {
  return apiFetch<PermissionOut[]>(`/api/auth/permissions?${withTenant(tenantId).toString()}`);
}

export interface UserUpdateRequest {
  full_name?: string | null;
  is_active?: boolean;
  is_superuser?: boolean;
}

export interface SetPasswordRequest {
  password: string;
}

export interface AssignRolesRequest {
  role_ids: string[];
}

export function updateUser(tenantId: string, userId: string, payload: UserUpdateRequest): Promise<UserOut> {
  return apiFetch<UserOut>(`/api/auth/users/${encodeURIComponent(userId)}?${withTenant(tenantId).toString()}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function setUserPassword(tenantId: string, userId: string, password: string): Promise<UserOut> {
  return apiFetch<UserOut>(`/api/auth/users/${encodeURIComponent(userId)}/password?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function assignUserRoles(tenantId: string, userId: string, roleIds: string[]): Promise<UserOut> {
  return apiFetch<UserOut>(`/api/auth/users/${encodeURIComponent(userId)}/roles?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({ role_ids: roleIds }),
  });
}

export function deleteUser(tenantId: string, userId: string): Promise<void> {
  return apiFetch<void>(`/api/auth/users/${encodeURIComponent(userId)}?${withTenant(tenantId).toString()}`, {
    method: "DELETE",
  });
}

export class ApiError extends Error {
  constructor(
    public path: string,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the request was rejected because the session is missing/expired. */
  get isAuthError(): boolean {
    return this.status === 401;
  }
}

/**
 * Extract a human-readable error message from a non-OK response body.
 * FastAPI/Starlette returns `{"detail": "..."}`, which previously leaked raw
 * JSON into the UI. We prefer the detail string; fall back to a readable
 * pairing of status + statusText.
 */
export async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.message === "string") return body.message;
    if (Array.isArray(body?.detail)) {
      // Pydantic validation errors: [{loc,msg,type}, ...]
      return body.detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          d.msg ? `${(d.loc ?? []).join(".")}: ${d.msg}` : JSON.stringify(d)
        )
        .join("; ");
    }
  } catch {
    // Not JSON ΓÇö fall through to text/status.
  }
  const text = await res.text().catch(() => "");
  return text || `${res.status} ${res.statusText}`;
}

/**
 * Shared fetch wrapper. Guarantees:
 *  - Every request carries `Authorization: Bearer <token>` (auto-login once
 *    via the dev bootstrap when no token is cached).
 *  - A 401 triggers a single token refresh/re-auth retry before surfacing.
 *  - Errors are exposed as readable `ApiError`s, never raw `{"detail":...}`.
 *  - Every request is bounded by REQUEST_TIMEOUT_MS via AbortController, so
 *    a hung server can never wedge a caller's loading state permanently.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetchWithRetry<T>(path, init, 0);
}

/** Hard ceiling for any single HTTP round-trip made through this client. */
const REQUEST_TIMEOUT_MS = 10_000;


async function apiFetchWithRetry<T>(path: string, init: RequestInit | undefined, attempt: number): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  // Attach a valid token. If none exists yet, perform the (shared, single
  // in-flight) dev-bootstrap login before firing the request. If bootstrap
  // is disabled and no token is present, this throws a clear 401 error.
  const token = await ensureAuthenticated();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  // Active-tenant override (P0): a superuser may act on any authorized tenant.
  // The backend validates the target against the principal's memberships and
  // audits every override — the frontend never trusts this value on its own.
  const activeTenant = getActiveTenantOverride();
  if (activeTenant) {
    headers["X-Tenant-Id"] = activeTenant;
  }

  // Client-side hard timeout: a hung backend/proxy (e.g. a Stripe test-mode
  // call that never returns) must NEVER leave a caller's loading state
  // spinning forever. Timed-out requests reject with a readable ApiError so
  // every view transitions to its error/retry state instead.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { ...headers, ...init?.headers },
      cache: "no-store",
      signal: init?.signal ?? controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (controller.signal.aborted && !init?.signal?.aborted) {
      throw new ApiError(path, 0, "Request timed out — the server did not respond in time.");
    }
    throw err instanceof Error ? err : new ApiError(path, 0, String(err));
  }
  clearTimeout(timer);

  if (!res.ok) {
    const message = await readErrorDetail(res);

    // Session expired mid-flight: clear the stale token and retry exactly
    // once with a freshly-minted credential (avoids infinite retry loops
    // on genuinely invalid credentials).
    if (res.status === 401 && attempt === 0) {
      clearAuthToken();
      return apiFetchWithRetry<T>(path, init, attempt + 1);
    }

    throw new ApiError(path, res.status, res.status === 401 ? "Session expired - please log in again." : message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function withTenant(tenantId: string, extra?: Record<string, string>): URLSearchParams {
  return new URLSearchParams({ tenant_id: tenantId, ...extra });
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
export function getGeneralSettings(tenantId: string = DEFAULT_TENANT_ID): Promise<GeneralSettings> {
  return apiFetch<GeneralSettings>(`/api/settings/general?${withTenant(tenantId).toString()}`);
}

export function updateGeneralSettings(tenantId: string = DEFAULT_TENANT_ID, payload: GeneralSettingsUpdate): Promise<GeneralSettings> {
  return apiFetch<GeneralSettings>(`/api/settings/general?${withTenant(tenantId).toString()}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getSecuritySettings(tenantId: string = DEFAULT_TENANT_ID): Promise<SecuritySettings> {
  return apiFetch<SecuritySettings>(`/api/settings/security?${withTenant(tenantId).toString()}`);
}

export function updateSecuritySettings(tenantId: string = DEFAULT_TENANT_ID, payload: SecuritySettingsUpdate): Promise<SecuritySettings> {
  return apiFetch<SecuritySettings>(`/api/settings/security?${withTenant(tenantId).toString()}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getNotificationSettings(tenantId: string = DEFAULT_TENANT_ID): Promise<NotificationSettings> {
  return apiFetch<NotificationSettings>(`/api/settings/notifications?${withTenant(tenantId).toString()}`);
}

export function updateNotificationSettings(tenantId: string = DEFAULT_TENANT_ID, payload: NotificationSettingsUpdate): Promise<NotificationSettings> {
  return apiFetch<NotificationSettings>(`/api/settings/notifications?${withTenant(tenantId).toString()}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listApiKeys(tenantId: string = DEFAULT_TENANT_ID): Promise<ApiKey[]> {
  return apiFetch<ApiKey[]>(`/api/settings/api-keys?${withTenant(tenantId).toString()}`);
}

export function createApiKey(tenantId: string = DEFAULT_TENANT_ID, payload: ApiKeyCreateRequest): Promise<ApiKeyCreateResponse> {
  return apiFetch<ApiKeyCreateResponse>(`/api/settings/api-keys?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteApiKey(tenantId: string = DEFAULT_TENANT_ID, keyId: string): Promise<void> {
  return apiFetch<void>(`/api/settings/api-keys/${encodeURIComponent(keyId)}?${withTenant(tenantId).toString()}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Usage metering
// ---------------------------------------------------------------------------
export interface UsageSummaryResponse {
  tenant_id: string;
  total_api_calls: number;
  total_alerts_ingested: number;
  total_metrics_ingested: number;
  total_collector_runs: number;
  total_sop_executions: number;
  total_workflow_executions: number;
  total_auth_logins: number;
  grand_total: number;
  period_start: string;
  period_end: string;
}

export interface UsageEventBucket {
  event_type: string;
  bucket_start: string;
  count: number;
}

export interface UsageEventsResponse {
  tenant_id: string;
  events: UsageEventBucket[];
  total: number;
}

export function getTenantUsageSummary(tenantId: string, days: number = 30): Promise<UsageSummaryResponse> {
  return apiFetch<UsageSummaryResponse>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/usage/summary?days=${days}`);
}

export function getTenantUsageEvents(tenantId: string, eventType?: string, days: number = 7): Promise<UsageEventsResponse> {
  const qs = new URLSearchParams({ days: String(days) });
  if (eventType) qs.set("event_type", eventType);
  return apiFetch<UsageEventsResponse>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/usage/events?${qs.toString()}`);
}


// ---------------------------------------------------------------------------
// Alert Engine
// ---------------------------------------------------------------------------
export function listAlerts(
  params: {
    tenantId?: string;
    status?: AlertStatus;
    severity?: AlertSeverity;
    limit?: number;
  } = {},
): Promise<Alert[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const search = withTenant(tenantId, { limit: String(params.limit ?? 10) });
  if (params.status) search.set("status", params.status);
  if (params.severity) search.set("severity", params.severity);
  return apiFetch<Alert[]>(`/api/alerts?${search.toString()}`);
}

export function getAlertSummary(tenantId: string = DEFAULT_TENANT_ID): Promise<AlertSummary> {
  return apiFetch<AlertSummary>(`/api/alerts/summary?${withTenant(tenantId).toString()}`);
}

export function getAlertStatistics(tenantId: string = DEFAULT_TENANT_ID): Promise<AlertSummary> {
  return apiFetch<AlertSummary>(`/api/alerts/statistics?${withTenant(tenantId).toString()}`);
}

export function acknowledgeAlert(
  alertId: string,
  acknowledgedBy: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${alertId}/acknowledge?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({ acknowledged_by: acknowledgedBy }),
  });
}

export function resolveAlert(alertId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Alert> {
  return apiFetch<Alert>(`/api/alerts/${alertId}/resolve?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
export function getDashboardSummary(tenantId: string = DEFAULT_TENANT_ID): Promise<DashboardSummary> {
  return apiFetch<DashboardSummary>(`/api/dashboard/summary?${withTenant(tenantId).toString()}`);
}


export function getCloudStatus(tenantId: string = DEFAULT_TENANT_ID): Promise<CloudStatus> {
  return apiFetch<CloudStatus>(`/api/cloud/status?${withTenant(tenantId).toString()}`);
}

export function getNetworkStatus(tenantId: string = DEFAULT_TENANT_ID): Promise<NetworkStatus> {
  return apiFetch<NetworkStatus>(`/api/network/status?${withTenant(tenantId).toString()}`);
}

export function getAutomationStatus(tenantId: string = DEFAULT_TENANT_ID): Promise<AutomationStatus> {
  return apiFetch<AutomationStatus>(`/api/automation/status?${withTenant(tenantId).toString()}`);
}

export function getAIInsights(tenantId: string = DEFAULT_TENANT_ID): Promise<AIInsights> {
  return apiFetch<AIInsights>(`/api/ai/insights?${withTenant(tenantId).toString()}`);
}

/**
 * Real liveness probe against the backend's unauthenticated /health endpoint.
 * Returns true only when the backend actually answered 200 — never assume.
 */
export async function getBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

export interface AiCtoStatus {
  ai_cto: {
    status: 'healthy' | 'degraded';
    reason: string | null;
  };
  services: Record<string, 'up' | 'down' | 'unavailable'>;
  ai: {
    status: 'ready' | 'unavailable';
    message: string;
  };
  timestamp: string;
}

export function getAiCtoStatus(): Promise<AiCtoStatus> {
  return apiFetch<AiCtoStatus>(`/api/health/ai-cto`);
}

// ---------------------------------------------------------------------------
// Devices / Events / Collector Runs
// ---------------------------------------------------------------------------
export function getDevices(
  params: { tenantId?: string; siteId?: string; deviceType?: string; vendor?: string; status?: string } = {},
): Promise<Device[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const search = withTenant(tenantId);
  if (params.siteId) search.set("site_id", params.siteId);
  if (params.deviceType) search.set("device_type", params.deviceType);
  if (params.vendor) search.set("vendor", params.vendor);
  if (params.status) search.set("status", params.status);
  return apiFetch<Device[]>(`/api/devices?${search.toString()}`);
}

export function getEvents(
  params: { tenantId?: string; deviceId?: string; severity?: string; eventType?: string; since?: string; limit?: number } = {},
): Promise<Event[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const search = withTenant(tenantId, { limit: String(params.limit ?? 20) });
  if (params.deviceId) search.set("device_id", params.deviceId);
  if (params.severity) search.set("severity", params.severity);
  if (params.eventType) search.set("event_type", params.eventType);
  if (params.since) search.set("since", params.since);
  return apiFetch<Event[]>(`/api/events?${search.toString()}`);
}

export function getCollectorRuns(
  params: { tenantId?: string; monitoringSourceId?: string; status?: string; since?: string; limit?: number } = {},
): Promise<CollectorRun[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const search = withTenant(tenantId, { limit: String(params.limit ?? 20) });
  if (params.monitoringSourceId) search.set("monitoring_source_id", params.monitoringSourceId);
  if (params.status) search.set("status", params.status);
  if (params.since) search.set("since", params.since);
  return apiFetch<CollectorRun[]>(`/api/collector-runs?${search.toString()}`);
}

export function getSensors(tenantId: string = DEFAULT_TENANT_ID): Promise<SensorOut[]> {
  return apiFetch<SensorOut[]>(`/api/monitoring/sensors?${withTenant(tenantId).toString()}`);
}

// ---------------------------------------------------------------------------
// CMDB / Asset Intelligence
// ---------------------------------------------------------------------------

export interface AssetDevice {
  id: string;
  tenant_id: string;
  site_id: string | null;
  monitoring_source_id: string | null;
  external_id: string | null;
  name: string;
  device_type: string;
  serial_number: string | null;
  status: string;
  hostname: string | null;
  ip_address: string | null;
  mac_address: string | null;
  vendor: string | null;
  model: string | null;
  os_version: string | null;
  last_seen_at: string | null;
  criticality: string | null;
  business_service_id: string | null;
  environment: string | null;
  business_owner: string | null;
  support_team: string | null;
  maintenance_window: string | null;
  warranty_start: string | null;
  warranty_end: string | null;
  tags: string[];
  custom_metadata: Record<string, unknown>;
}

export interface AssetSite {
  id: string;
  tenant_id: string;
  name: string;
  code: string;
  address: string | null;
  region: string | null;
  criticality: string | null;
  building: string | null;
  floor: string | null;
  rack: string | null;
  latitude: number | null;
  longitude: number | null;
  site_owner: string | null;
  support_contact: string | null;
  timezone: string | null;
}

export interface AssetRelationship {
  id: string;
  tenant_id: string;
  asset_id: string;
  related_asset_id: string;
  relationship_type: string;
  label: string | null;
  weight: number | null;
  created_at: string;
  updated_at: string;
}

export interface AssetHealthSummary {
  scope: string;
  scope_id: string | null;
  health_score: number;
  status: string;
  details: Record<string, unknown> | null;
}

export interface TenantHealthSummary {
  tenant_id: string;
  health_score: number;
  sites_count: number;
  devices_count: number;
  sensors_count: number;
  alerts_active: number;
}

export function listAssetDevices(tenantId: string = DEFAULT_TENANT_ID, params: {
  siteId?: string;
  deviceType?: string;
  status?: string;
  environment?: string;
  criticality?: string;
  tag?: string;
  search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<AssetDevice[]> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.siteId) qs.set("site_id", params.siteId);
  if (params.deviceType) qs.set("device_type", params.deviceType);
  if (params.status) qs.set("status", params.status);
  if (params.environment) qs.set("environment", params.environment);
  if (params.criticality) qs.set("criticality", params.criticality);
  if (params.tag) qs.set("tag", params.tag);
  if (params.search) qs.set("search", params.search);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<AssetDevice[]>(`/api/assets/devices?${qs.toString()}`);
}

/**
 * Legacy device list (monitoring inventory devices, not asset-registry
 * devices). Kept as an alias for pages that import listDevices().
 */
export function listDevices(tenantId: string = DEFAULT_TENANT_ID, params: {
  siteId?: string;
  status?: string;
  vendor?: string;
  limit?: number;
} = {}): Promise<Device[]> {
  const search = new URLSearchParams({ tenant_id: tenantId });
  if (params.siteId) search.set("site_id", params.siteId);
  if (params.status) search.set("status", params.status);
  if (params.vendor) search.set("vendor", params.vendor);
  if (params.limit) search.set("limit", String(params.limit));
  return apiFetch<Device[]>(`/api/devices?${search.toString()}`);
}

export function getAssetDevice(deviceId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<AssetDevice> {
  return apiFetch<AssetDevice>(`/api/assets/devices/${deviceId}?${withTenant(tenantId).toString()}`);
}

export function listAssetSites(tenantId: string = DEFAULT_TENANT_ID, params: {
  region?: string;
  building?: string;
  criticality?: string;
  search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<AssetSite[]> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.region) qs.set("region", params.region);
  if (params.building) qs.set("building", params.building);
  if (params.criticality) qs.set("criticality", params.criticality);
  if (params.search) qs.set("search", params.search);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<AssetSite[]>(`/api/assets/sites?${qs.toString()}`);
}

export function getAssetDeviceRelationships(deviceId: string, tenantId: string = DEFAULT_TENANT_ID, relationshipType?: string): Promise<AssetRelationship[]> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (relationshipType) qs.set("relationship_type", relationshipType);
  return apiFetch<AssetRelationship[]>(`/api/assets/devices/${deviceId}/relationships?${qs.toString()}`);
}

export function getTenantHealth(tenantId: string = DEFAULT_TENANT_ID): Promise<TenantHealthSummary> {
  return apiFetch<TenantHealthSummary>(`/api/assets/tenants/${tenantId}/health?${withTenant(tenantId).toString()}`);
}

// ---------------------------------------------------------------------------
// Zones (infrastructure hierarchy)
// ---------------------------------------------------------------------------

export interface ZoneItem {
  id: string;
  tenant_id: string;
  site_id: string;
  name: string;
  code: string;
  description: string | null;
  zone_type: string;
}

export function listZones(tenantId: string = DEFAULT_TENANT_ID, params: {
  siteId?: string;
  zoneType?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ZoneItem[]> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.siteId) qs.set("site_id", params.siteId);
  if (params.zoneType) qs.set("zone_type", params.zoneType);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<ZoneItem[]>(`/api/zones?${qs.toString()}`);
}

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  depth: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  relationship_type: string;
  label: string | null;
  weight: number | null;
}

export interface GraphResponse {
  root_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export function getDeviceDependencyChain(deviceId: string, tenantId: string = DEFAULT_TENANT_ID, maxDepth: number = 5): Promise<GraphResponse> {
  const qs = new URLSearchParams({ tenant_id: tenantId, max_depth: String(maxDepth) });
  return apiFetch<GraphResponse>(`/api/assets/devices/${encodeURIComponent(deviceId)}/dependency-chain?${qs.toString()}`);
}

export function getDeviceImpactAnalysis(deviceId: string, tenantId: string = DEFAULT_TENANT_ID, maxDepth: number = 5): Promise<GraphResponse> {
  const qs = new URLSearchParams({ tenant_id: tenantId, max_depth: String(maxDepth) });
  return apiFetch<GraphResponse>(`/api/assets/devices/${encodeURIComponent(deviceId)}/impact-analysis?${qs.toString()}`);
}

export function getDeviceFullGraph(deviceId: string, tenantId: string = DEFAULT_TENANT_ID, maxDepth: number = 5): Promise<GraphResponse> {
  const qs = new URLSearchParams({ tenant_id: tenantId, max_depth: String(maxDepth) });
  return apiFetch<GraphResponse>(`/api/assets/devices/${encodeURIComponent(deviceId)}/full-graph?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// Enterprise Ticket Management
// ---------------------------------------------------------------------------

export type TicketStatus =
  | "open"
  | "assigned"
  | "in_progress"
  | "pending_approval"
  | "resolved"
  | "closed"
  | "cancelled"
  | "reopened";

export type TicketPriority = "P1" | "P2" | "P3" | "P4" | null;

export interface Ticket {
  id: string;
  tenant_id: string;
  incident_id: string | null;
  ticket_number: string;
  title: string;
  description: string | null;
  source: string;
  status: TicketStatus;
  priority: TicketPriority;
  category: string | null;
  subcategory: string | null;
  assigned_to: string | null;
  assigned_team: string | null;
  assignment_group: string | null;
  sla_due_at: string | null;
  sla_breached: boolean;
  opened_at: string;
  assigned_at: string | null;
  in_progress_at: string | null;
  pending_approval_at: string | null;
  resolved_at: string | null;
  closed_at: string | null;
  reopened_at: string | null;
  escalation_level: number;
  escalated_at: string | null;
  escalated_by: string | null;
  ai_ticket_summary: string | null;
  ai_executive_summary: string | null;
  ai_engineer_recommendation: string | null;
  ai_customer_update: string | null;
  ai_playbook_selection: string | null;
  ai_execution_confidence: number | null;
  ai_automation_recommendation: string | null;
  knowledge_article_id: string | null;
  watchers: Array<Record<string, unknown>>;
  tags: string[];
  custom_fields: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  is_deleted: boolean;
}

export interface TicketDashboardStats {
  open_count: number;
  assigned_count: number;
  in_progress_count: number;
  pending_approval_count: number;
  pending_customer_count: number;
  pending_vendor_count: number;
  resolved_today_count: number;
  closed_today_count: number;
  escalated_count: number;
  overdue_count: number;
  automation_pending_count: number;
  sla_breach_count: number;
  priority_distribution: Record<string, number>;
  recent_activity: TicketHistoryOut[];
}

export interface TicketHistoryOut {
  id: string;
  tenant_id: string;
  ticket_id: string;
  actor_id: string | null;
  actor_name: string | null;
  action_type: string;
  field_name: string | null;
  old_value: string | null;
  new_value: string | null;
  context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TicketListResponse {
  items: Ticket[];
  total: number;
  limit: number;
  offset: number;
}

export interface TicketTransitionRequest {
  to_state: string;
  reason?: string | null;
  context?: Record<string, unknown>;
}

export interface TicketTransitionResponse {
  ticket: Ticket;
  available_transitions: string[];
}

export function listTickets(params: {
  tenantId?: string;
  status?: string;
  priority?: string;
  assigned_to?: string;
  category?: string;
  incident_id?: string;
  search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<TicketListResponse> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.status) qs.set("status", params.status);
  if (params.priority) qs.set("priority", params.priority);
  if (params.assigned_to) qs.set("assigned_to", params.assigned_to);
  if (params.category) qs.set("category", params.category);
  if (params.incident_id) qs.set("incident_id", params.incident_id);
  if (params.search) qs.set("search", params.search);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<TicketListResponse>(`/api/tickets?${qs.toString()}`);
}

export function getTicket(ticketId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  return apiFetch<Ticket>(`/api/tickets/${encodeURIComponent(ticketId)}?${withTenant(tenantId).toString()}`);
}

export function getTicketDashboardStats(tenantId: string = DEFAULT_TENANT_ID): Promise<TicketDashboardStats> {
  return apiFetch<TicketDashboardStats>(`/api/tickets/dashboard?${withTenant(tenantId).toString()}`);
}

export function transitionTicket(ticketId: string, payload: TicketTransitionRequest, tenantId: string = DEFAULT_TENANT_ID): Promise<TicketTransitionResponse> {
  return apiFetch<TicketTransitionResponse>(`/api/tickets/${encodeURIComponent(ticketId)}/transition?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function resolveTicket(ticketId: string, resolutionNote?: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  const qs = withTenant(tenantId);
  if (resolutionNote) qs.set("resolution_note", resolutionNote);
  return apiFetch<Ticket>(`/api/tickets/${encodeURIComponent(ticketId)}/resolve?${qs.toString()}`, {
    method: "POST",
  });
}

export function closeTicket(ticketId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  return apiFetch<Ticket>(`/api/tickets/${encodeURIComponent(ticketId)}/close?${withTenant(tenantId).toString()}`, {
    method: "POST",
  });
}

export function reopenTicket(ticketId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  return apiFetch<Ticket>(`/api/tickets/${encodeURIComponent(ticketId)}/reopen?${withTenant(tenantId).toString()}`, {
    method: "POST",
  });
}

export function assignTicket(ticketId: string, assignedTo: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  return apiFetch<Ticket>(`/api/tickets/${encodeURIComponent(ticketId)}/assign?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({ assigned_to: assignedTo }),
  });
}

// ---------------------------------------------------------------------------
// Change Management (CAB — Change Advisory Board)
// Backs the /changes module. Connects to the existing backend router mounted
// at /api/change-requests (full CRUD + status lifecycle + summary).
// Lifecycle: draft -> submitted -> approved -> scheduled -> implementing ->
//            verifying -> closed | rejected/cancelled.
// ---------------------------------------------------------------------------

export type ChangeType =
  | "config"
  | "software"
  | "hardware"
  | "security"
  | "network"
  | "cloud"
  | "database"
  | "application"
  | "other";

export type ChangeRiskLevel = "low" | "medium" | "high" | "critical";

export type ChangePriority = "low" | "normal" | "high" | "critical" | "emergency";

export type ChangeStatus =
  | "draft"
  | "submitted"
  | "approved"
  | "rejected"
  | "scheduled"
  | "implementing"
  | "verifying"
  | "closed"
  | "cancelled";

export interface ChangeRequest {
  id: string;
  tenant_id: string;
  change_number: string;
  title: string;
  description: string;
  change_type: ChangeType;
  risk_level: ChangeRiskLevel;
  priority: ChangePriority;
  status: ChangeStatus;
  business_justification: string | null;
  implementation_plan: string | null;
  backout_plan: string | null;
  test_plan: string | null;
  validation_plan: string | null;
  security_impact: string | null;
  affected_devices: unknown[];
  affected_services: unknown[];
  downtime_expected: boolean;
  downtime_duration_minutes: number;
  maintenance_window_start: string | null;
  maintenance_window_end: string | null;
  requested_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  implemented_by: string | null;
  implemented_at: string | null;
  verified_by: string | null;
  verified_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChangeRequestSummary {
  total_changes: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  by_risk_level: Record<string, number>;
}

export interface ChangeRequestCreateInput {
  title: string;
  description: string;
  change_type: ChangeType;
  risk_level?: ChangeRiskLevel;
  priority?: ChangePriority;
  business_justification?: string | null;
  implementation_plan?: string | null;
  backout_plan?: string | null;
  test_plan?: string | null;
  validation_plan?: string | null;
  security_impact?: string | null;
  affected_devices?: unknown[];
  affected_services?: unknown[];
  downtime_expected?: boolean;
  downtime_duration_minutes?: number;
  maintenance_window_start?: string | null;
  maintenance_window_end?: string | null;
}

export function listChangeRequests(params: {
  status?: string;
  change_type?: string;
  risk_level?: string;
  requested_by?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ChangeRequest[]> {
  const qs = new URLSearchParams({ tenant_id: DEFAULT_TENANT_ID });
  if (params.status) qs.set("status", params.status);
  if (params.change_type) qs.set("change_type", params.change_type);
  if (params.risk_level) qs.set("risk_level", params.risk_level);
  if (params.requested_by) qs.set("requested_by", params.requested_by);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<ChangeRequest[]>(`/api/change-requests?${qs.toString()}`);
}

export function getChangeRequest(changeId: string): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/api/change-requests/${encodeURIComponent(changeId)}?${withTenant(DEFAULT_TENANT_ID).toString()}`);
}

export function getChangeRequestSummary(): Promise<ChangeRequestSummary> {
  return apiFetch<ChangeRequestSummary>(`/api/change-requests/summary?${withTenant(DEFAULT_TENANT_ID).toString()}`);
}

export function createChangeRequest(payload: ChangeRequestCreateInput): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/api/change-requests?${withTenant(DEFAULT_TENANT_ID).toString()}`, {
    method: "POST",
    body: JSON.stringify({ tenant_id: DEFAULT_TENANT_ID, ...payload }),
  });
}

export function updateChangeStatus(
  changeId: string,
  newStatus: ChangeStatus,
  fields: {
    approved_by?: string;
    approval_notes?: string;
    implemented_by?: string;
    implementation_result?: string;
    verified_by?: string;
    verification_result?: string;
    closure_notes?: string;
  } = {},
): Promise<ChangeRequest> {
  const qs = new URLSearchParams({ tenant_id: DEFAULT_TENANT_ID, new_status: newStatus });
  if (fields.approved_by) qs.set("approved_by", fields.approved_by);
  if (fields.approval_notes) qs.set("approval_notes", fields.approval_notes);
  if (fields.implemented_by) qs.set("implemented_by", fields.implemented_by);
  if (fields.implementation_result) qs.set("implementation_result", fields.implementation_result);
  if (fields.verified_by) qs.set("verified_by", fields.verified_by);
  if (fields.verification_result) qs.set("verification_result", fields.verification_result);
  if (fields.closure_notes) qs.set("closure_notes", fields.closure_notes);
  return apiFetch<ChangeRequest>(`/api/change-requests/${encodeURIComponent(changeId)}/status?${qs.toString()}`, {
    method: "POST",
  });
}

export function createTicket(payload: {
  title: string;
  description?: string;
  incident_id?: string;
  source?: string;
  priority?: string;
  category?: string;
  subcategory?: string;
  assigned_to?: string;
  assigned_team?: string;
  assignment_group?: string;
  sla_due_at?: string;
  custom_fields?: Record<string, unknown>;
  tags?: string[];
  created_by?: string;
}, tenantId: string = DEFAULT_TENANT_ID): Promise<Ticket> {
  return apiFetch<Ticket>(`/api/tickets?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Sprint 15: Enterprise Alert Intelligence & Correlation Engine
// ---------------------------------------------------------------------------

export type IncidentSeverity = "critical" | "warning" | "info";
export type IncidentStatus =
  | "open"
  | "acknowledged"
  | "investigating"
  | "monitoring"
  | "recovered"
  | "resolved"
  | "closed"
  | "archived";

export interface Incident {
  id: string;
  tenant_id: string;
  site_id: string | null;
  device_id: string | null;
  business_service_id: string | null;
  title: string;
  description: string | null;
  severity: IncidentSeverity;
  status: IncidentStatus;
  root_cause_alert_id: string | null;
  alert_count: number;
  total_occurrences: number;
  opened_at: string;
  last_alert_at: string;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  investigating_at: string | null;
  monitoring_at: string | null;
  recovered_at: string | null;
  closed_at: string | null;
  archived_at: string | null;
  assigned_to: string | null;
  assigned_team: string | null;
  correlation_type: string | null;
  root_cause_summary: string | null;
  supporting_evidence: Record<string, unknown>;
  automation_available: boolean;
  affected_assets: Record<string, unknown>[];
  sla_due_at: string | null;
  stale_after_minutes: number;
  device_name: string | null;
  site_name: string | null;
  business_service_name: string | null;
}

export interface IncidentDetail extends Incident {
  alerts: Alert[];
}

export interface AlertIntelligenceMetrics {
  active_incidents: number;
  correlated_incidents: number;
  suppressed_alerts: number;
  duplicate_alerts_removed: number;
  noise_reduction_pct: number;
  total_raw_alerts: number;
  total_displayed_alerts: number;
}

export interface CollectorHealth {
  source_id: string;
  source_name: string;
  current_state: "healthy" | "degraded" | "failing" | "unknown";
  success_rate_pct: number;
  failure_rate_pct: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  retry_count: number;
  last_error: string | null;
}

export interface CollectorHealthList {
  collectors: CollectorHealth[];
  healthy_count: number;
  degraded_count: number;
  failing_count: number;
  unknown_count: number;
}

export function listIncidents(
  params: {
    tenantId?: string;
    status?: IncidentStatus;
    severity?: IncidentSeverity;
    limit?: number;
    offset?: number;
  } = {},
): Promise<Incident[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.status) qs.set("status", params.status);
  if (params.severity) qs.set("severity", params.severity);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<Incident[]>(`/api/incidents?${qs.toString()}`);
}

export function getIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<IncidentDetail> {
  return apiFetch<IncidentDetail>(`/api/incidents/${incidentId}?${withTenant(tenantId).toString()}`);
}

export function getAlertIntelligenceMetrics(tenantId: string = DEFAULT_TENANT_ID): Promise<AlertIntelligenceMetrics> {
  return apiFetch<AlertIntelligenceMetrics>(`/api/incidents/intelligence?${withTenant(tenantId).toString()}`);
}

export function getCollectorHealth(tenantId: string = DEFAULT_TENANT_ID, windowHours: number = 24): Promise<CollectorHealthList> {
  const qs = withTenant(tenantId, { window_hours: String(windowHours) });
  return apiFetch<CollectorHealthList>(`/api/incidents/collector-health?${qs.toString()}`);
}

export function acknowledgeIncident(incidentId: string, acknowledgedBy: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/acknowledge?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({ acknowledged_by: acknowledgedBy }),
  });
}

export function investigateIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/investigate?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function monitorIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/monitor?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function recoverIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/recover?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function closeIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/close?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function archiveIncident(incidentId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/archive?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function assignIncident(
  incidentId: string,
  payload: { assigned_to?: string; assigned_team?: string },
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<Incident> {
  return apiFetch<Incident>(`/api/incidents/${incidentId}/assign?${withTenant(tenantId).toString()}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// SOP Engine (SOP Repository / Knowledge Base)
// ---------------------------------------------------------------------------

export type SOPCategory =
  | "availability"
  | "performance"
  | "capacity"
  | "configuration"
  | "security"
  | "connectivity"
  | "other";

export type SOPVersionStatus = "draft" | "approved" | "deprecated";

export interface SOPVersion {
  id: string;
  sop_id: string;
  version_number: number;
  steps: Array<{ order?: number; action?: string; [key: string]: unknown }>;
  possible_causes: string[];
  change_summary: string | null;
  status: SOPVersionStatus;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface SOP {
  id: string;
  tenant_id: string;
  sop_number: string;
  name: string;
  description: string | null;
  category: string;
  tags: string[];
  vendor: string | null;
  platform: string | null;
  device_type: string | null;
  site_id: string | null;
  business_service_id: string | null;
  region: string | null;
  min_priority: string | null;
  required_skills: string[];
  requires_approval: boolean;
  estimated_resolution_minutes: number | null;
  rollback_procedure: string | null;
  is_active: boolean;
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
  versions: SOPVersion[];
}

export interface SOPDetail extends SOP {
  versions: SOPVersion[];
}

export interface SOPUpdatePayload {
  name?: string;
  description?: string | null;
  category?: SOPCategory;
  tags?: string[];
  vendor?: string | null;
  platform?: string | null;
  device_type?: string | null;
  site_id?: string | null;
  business_service_id?: string | null;
  region?: string | null;
  min_priority?: "P1" | "P2" | "P3" | "P4" | null;
  required_skills?: string[];
  requires_approval?: boolean;
  estimated_resolution_minutes?: number | null;
  rollback_procedure?: string | null;
  is_active?: boolean;
}

export function listSops(
  params: {
    tenantId?: string;
    category?: string;
    vendor?: string;
    platform?: string;
    device_type?: string;
    is_active?: boolean;
    search?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<SOP[]> {
  const tenantId = params.tenantId ?? DEFAULT_TENANT_ID;
  const qs = new URLSearchParams({ tenant_id: tenantId });
  if (params.category) qs.set("category", params.category);
  if (params.vendor) qs.set("vendor", params.vendor);
  if (params.platform) qs.set("platform", params.platform);
  if (params.device_type) qs.set("device_type", params.device_type);
  if (params.is_active !== undefined) qs.set("is_active", String(params.is_active));
  if (params.search) qs.set("search", params.search);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return apiFetch<SOP[]>(`/api/sops?${qs.toString()}`);
}

export function getSop(sopId: string, tenantId: string = DEFAULT_TENANT_ID): Promise<SOPDetail> {
  return apiFetch<SOPDetail>(`/api/sops/${encodeURIComponent(sopId)}?${withTenant(tenantId).toString()}`);
}

export function updateSop(
  sopId: string,
  payload: SOPUpdatePayload,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<SOP> {
  return apiFetch<SOP>(`/api/sops/${encodeURIComponent(sopId)}?${withTenant(tenantId).toString()}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// AI Copilot
// ---------------------------------------------------------------------------

/** Shape returned by POST /api/copilot/chat (Step 3 backend contract). */
export interface CopilotChatResponse {
  answer: string;
  task_created: boolean;
  task_id: string | null;
  task_number: string | null;
  department: string | null;
  agent: string | null;
  intent: "command" | "query";
  context_used: Record<string, unknown>;
}

/**
 * One Copilot turn. Deliberately uses a LONG deadline (3 minutes) via a
 * caller-supplied AbortController: local LLM inference (qwen3.6 via Ollama)
 * legitimately takes on the order of minutes, and the shared client's
 * 10-second REQUEST_TIMEOUT_MS would cut off a perfectly healthy request.
 * Passing our own `signal` makes apiFetch skip its short default timer.
 */
export function copilotChat(message: string, timeoutMs: number = 180_000): Promise<CopilotChatResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return apiFetch<CopilotChatResponse>("/api/copilot/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));
}

// ---------------------------------------------------------------------------
// AI Operations Command Center
// ---------------------------------------------------------------------------

/** Real Command Center payload — mirrors backend
 * app/schemas/command_center.py::CommandCenterOverview exactly. */
export type CommandCenterTaskStatus =
  | "created" | "assigned" | "accepted" | "investigating" | "executing"
  | "verifying" | "verified" | "recorded" | "failed" | "retrying"
  | "escalated" | "reassigned" | "cancelled";

export interface CCAgent {
  id: string;
  agent_id: string;
  name: string;
  role: string;
  department: string;
  level: string;
  manager_id: string | null;
  status: string;
  availability: string;
  is_active: boolean;
  current_workload: number;
  max_workload: number;
  skills: string[];
  supported_task_types: string[];
  success_rate: number;
}

export interface CCTreeNode {
  id: string;
  agent_id: string;
  name: string;
  role: string;
  department: string;
  level: string;
  status: string;
  availability: string;
  current_workload: number;
  max_workload: number;
  is_available: boolean;
  success_rate: number;
  skills: string[];
  children: CCTreeNode[];
}

export interface CCTask {
  id: string;
  task_number: string;
  title: string;
  task_type: string;
  priority: string;
  status: string;
  assigned_agent_id: string | null;
  assigned_agent_name: string | null;
  manager_agent_id: string | null;
  manager_agent_name: string | null;
  department: string | null;
  incident_id: string | null;
  execution_result: Record<string, unknown>;
  evidence: Record<string, unknown>;
  verified: boolean;
  created_at: string | null;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface CCWorkload {
  department: string;
  agent_count: number;
  active_agents: number;
  active_tasks: number;
  queued_tasks: number;
  verifying_tasks: number;
  completed_tasks: number;
  critical_tasks: number;
}

export interface CCActivityEvent {
  task_id: string;
  task_number: string;
  timestamp: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor: string | null;
  message: string | null;
}

export interface CCMetrics {
  total_agents: number;
  active_agents: number;
  assigned_agents: number;
  working_agents: number;
  total_tasks: number;
  queued: number;
  working: number;
  verifying: number;
  completed: number;
  failed: number;
  critical: number;
}

export interface CommandCenterHealth {
  status: string;
  ready: boolean;
  ai_ready: boolean;
  system_online: boolean;
  timestamp: string;
  dependencies: Record<string, string>;
}

export interface CommandCenterOverview {
  generated_at: string;
  metrics: CCMetrics;
  tree: CCTreeNode[];
  agents: CCAgent[];
  tasks: CCTask[];
  workload: CCWorkload[];
  activity: CCActivityEvent[];
  system_online: boolean;
  health: CommandCenterHealth | null;
}

/** Real adapter/source types actually implemented in the Collector
 * Framework (app/collectors/*). Informational only — the Tool Gateway
 * has no live execution tracking yet. */
export const TOOL_GATEWAY_ADAPTERS: { id: string; name: string; implemented: boolean }[] = [
  { id: "prtg", name: "PRTG Network Monitor", implemented: true },
  { id: "meraki", name: "Cisco Meraki Dashboard", implemented: true },
  { id: "fortigate", name: "Fortinet FortiGate", implemented: true },
  { id: "fortimanager", name: "Fortinet FortiManager", implemented: true },
  { id: "linux", name: "Linux (SSH)", implemented: true },
  { id: "azure", name: "Microsoft Azure Monitor", implemented: true },
  { id: "vmware", name: "VMware vSphere / vCenter", implemented: true },
  { id: "windows_winrm", name: "Windows (WinRM)", implemented: true },
  { id: "snmp", name: "SNMP (v1/v2c/v3)", implemented: true },
  { id: "zabbix", name: "Zabbix Enterprise", implemented: true },
];

export function getCommandCenterOverview(tenantId: string = DEFAULT_TENANT_ID): Promise<CommandCenterOverview> {
  return apiFetch<CommandCenterOverview>(`/api/command-center/overview?${withTenant(tenantId).toString()}`);
}

// ---------------------------------------------------------------------------
// Tasks / Worker Execution
// ---------------------------------------------------------------------------
export interface TaskRead {
  id: string;
  tenant_id: string;
  task_number: string;
  title: string;
  task_type: string;
  priority: string;
  status: string;
  failure_reason?: string | null;
  retry_count?: number;
  max_retries?: number;
  assigned_agent_id?: string | null;
  assigned_by_agent_id?: string | null;
  manager_agent_id?: string | null;
  incident_id?: string | null;
  ticket_id?: string | null;
  execution_plan?: Record<string, unknown>;
  execution_result?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  verification_result?: Record<string, unknown>;
  verified?: boolean;
  verified_at?: string | null;
  verified_by_agent_id?: string | null;
  recommended_sop_id?: string | null;
  sop_recommendation_score?: number | null;
  assigned_at?: string | null;
  accepted_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  audit_log?: Array<Record<string, unknown>>;
  created_at?: string;
  updated_at?: string;
}

export interface TaskWorkerExecution {
  tool_name?: string | null;
  execution_input?: Record<string, unknown> | null;
  allowed_tools?: string[] | null;
}

export function workerExecuteTask(
  taskId: string,
  payload: TaskWorkerExecution,
  agentId: string,
  tenantId: string = DEFAULT_TENANT_ID,
): Promise<TaskRead> {
  const qs = withTenant(tenantId);
  qs.set("agent_id", agentId);
  return apiFetch<TaskRead>(`/api/tasks/${encodeURIComponent(taskId)}/worker-execute?${qs.toString()}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

