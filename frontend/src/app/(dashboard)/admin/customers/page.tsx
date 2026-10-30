"use client";

// Platform-admin customer administration (internal ops view).
//
// Authentication: this page previously used raw `fetch(..., {credentials:
// "include"})` (cookie auth), but the platform authenticates with a
// localStorage Bearer token via `apiFetch`. Without the token /api/auth/me
// returned 401, so the page showed "Platform-admin access required" for EVERY
// user - including real superusers - and could never load. It now goes through
// the app's authenticated client.
//
// Authorization stays authoritative in the BACKEND: GET
// /api/billing/admin/customers is gated by require_admin (superuser). Hiding
// this page in the UI is convenience, never security.
//
// Every value comes from the canonical billing service, so plan, subscription
// status and limits agree with the Billing and Usage screens by construction.
//
// KNOWN GAP (deliberate): the endpoint does not return per-tenant device/user/
// API-usage counts, so those columns render "No data" instead of invented
// numbers.

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  AdminCustomer,
  AdminTenantDetail,
  AdminTenantPermission,
  AdminTenantRenameResult,
  AdminTenantStatusResult,
  ApiError,
  CustomPropertyDataType,
  TenantCustomProperty,
  deleteTenantCustomProperty,
  getAdminTenantDetail,
  getMe,
  getTenantCustomProperties,
  listAdminCustomers,
  createTenantCustomProperty,
  renameAdminTenant,
  setAdminTenantStatus,
  updateTenantCustomProperty,
} from "@/lib/api";

/** Explicit load states so the panel can never show another tenant's data. */
type DetailState = "idle" | "loading" | "ready" | "not_found" | "forbidden" | "error";

/** Same explicit-state discipline for the custom-property list. */
type PropState = "idle" | "loading" | "ready" | "forbidden" | "error";

export default function AdminCustomersPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [customers, setCustomers] = useState<AdminCustomer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<AdminCustomer | null>(null);

  const [detail, setDetail] = useState<AdminTenantDetail | null>(null);
  const [detailState, setDetailState] = useState<DetailState>("idle");
  const [detailError, setDetailError] = useState<string | null>(null);

  // --- Custom properties (Phase 6.3E) -------------------------------------
  const [properties, setProperties] = useState<TenantCustomProperty[] | null>(null);
  const [propState, setPropState] = useState<PropState>("idle");
  const [propError, setPropError] = useState<string | null>(null);
  /** Bumping this re-runs the loader without changing the selected tenant. */
  const [propReloadKey, setPropReloadKey] = useState(0);
  const [propNotice, setPropNotice] = useState<string | null>(null);
  /** null = modal closed; `property` null inside = create mode. */
  const [propForm, setPropForm] = useState<{ property: TenantCustomProperty | null } | null>(
    null,
  );

  // --- Tenant rename (Phase 6.3F) -----------------------------------------
  const [renameOpen, setRenameOpen] = useState(false);
  /** Server-confirmed before/after, shown after a successful rename. */
  const [renameResult, setRenameResult] = useState<AdminTenantRenameResult | null>(null);

  // --- Tenant lifecycle: suspend / reactivate (Phase 6.4A) ----------------
  const [lifecycleOpen, setLifecycleOpen] = useState(false);
  const [lifecycleSaving, setLifecycleSaving] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  const [lifecycleNotice, setLifecycleNotice] = useState<string | null>(null);

  /**
   * Apply a server-confirmed lifecycle change.
   *
   * ONLY `profile.is_active` is rewritten - the rest of `detail` (custom
   * properties, users, roles, scopes) is left exactly as loaded, which is why a
   * suspend never blanks the tenant's configuration and no page reload is needed.
   */
  function applyStatus(result: AdminTenantStatusResult) {
    const tenantId = result.tenant_id;
    setDetail((cur) =>
      cur && cur.profile.tenant_id === tenantId
        ? { ...cur, profile: { ...cur.profile, is_active: result.after.is_active } }
        : cur,
    );
    setLifecycleNotice(
      result.changed
        ? result.after.is_active
          ? "Tenant reactivated."
          : "Tenant suspended."
        : "No change - the tenant was already in that state.",
    );
  }

  async function submitStatus(next: boolean) {
    const tenantId = selected?.tenant_id;
    if (!tenantId || lifecycleSaving) return; // duplicate-submission guard
    setLifecycleError(null);
    setLifecycleSaving(true);
    try {
      const result = await setAdminTenantStatus(tenantId, next);
      setLifecycleSaving(false);
      setLifecycleOpen(false);
      applyStatus(result);
    } catch (err) {
      setLifecycleSaving(false);
      setLifecycleError(err instanceof Error ? err.message : String(err));
    }
  }

  /**
   * Apply a server-confirmed rename everywhere the old name is displayed, so the
   * table row, the panel heading and the profile agree immediately. Every value
   * comes from the RESPONSE - never from the form - so the UI cannot show a name
   * the backend did not accept.
   */
  function applyRename(result: AdminTenantRenameResult) {
    setRenameResult(result);
    const { tenant_id, after } = result;
    setCustomers((rows) =>
      rows
        ? rows.map((c) =>
            c.tenant_id === tenant_id ? { ...c, name: after.name, slug: after.slug } : c,
          )
        : rows,
    );
    setSelected((cur) =>
      cur && cur.tenant_id === tenant_id
        ? { ...cur, name: after.name, slug: after.slug }
        : cur,
    );
    setDetail((cur) =>
      cur && cur.profile.tenant_id === tenant_id
        ? { ...cur, profile: { ...cur.profile, name: after.name, slug: after.slug } }
        : cur,
    );
  }

  useEffect(() => {
    // Loader declared INSIDE the effect (house pattern); calling an outer
    // useCallback that sets state in the effect body trips
    // react-hooks/set-state-in-effect.
    let cancelled = false;
    async function refresh() {
      try {
        const me = await getMe();
        if (cancelled) return;
        if (!me?.is_superuser) {
          setAllowed(false);
          return;
        }
        setAllowed(true);
        const data = await listAdminCustomers();
        if (cancelled) return;
        setCustomers(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        // A 403 means "not a platform admin"; anything else is an operational
        // error and must not be disguised as a permissions problem.
        const message = err instanceof Error ? err.message : String(err);
        if (/403|forbidden/i.test(message)) {
          setAllowed(false);
        } else {
          setError(`Failed to load customers: ${message}`);
        }
      }
    }
    refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  // Detail is loaded ON SELECTION (never for every tenant up front), and the
  // previous tenant's data is cleared immediately so a switch can never render
  // stale values under the new tenant's name.
  useEffect(() => {
    const tenantId = selected?.tenant_id;
    let cancelled = false;

    (async () => {
      // All state updates happen inside the async task (not the effect body) -
      // setState called directly in an effect trips
      // react-hooks/set-state-in-effect. Clearing BEFORE loading guarantees the
      // panel can never show the previously selected tenant's values.
      if (!tenantId) {
        if (!cancelled) setDetailState("idle");
        return;
      }
      setDetail(null);
      setDetailError(null);
      setDetailState("loading");
      // A switch must never carry the previous tenant's lifecycle feedback with
      // it - the notice/error and any open confirm dialog are tenant-specific.
      setLifecycleNotice(null);
      setLifecycleError(null);
      setLifecycleOpen(false);
      setRenameOpen(false);
      setRenameResult(null);
      try {
        const data = await getAdminTenantDetail(tenantId);
        if (cancelled) return;
        setDetail(data);
        setDetailState("ready");
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        if (/404/.test(message)) {
          setDetailState("not_found");
        } else if (/403|forbidden/i.test(message)) {
          // The backend rejected this admin - never assume UI access = API access.
          setDetailState("forbidden");
        } else {
          setDetailState("error");
          setDetailError(message);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selected]);

  // Custom properties load ON SELECTION and clear immediately, exactly like the
  // detail loader above, so switching tenants can never render stale rows under
  // the new tenant's heading. `propReloadKey` re-runs it after a mutation
  // WITHOUT disturbing `selected`, which is what preserves the open tenant.
  useEffect(() => {
    const tenantId = selected?.tenant_id;
    let cancelled = false;

    (async () => {
      if (!tenantId) {
        if (!cancelled) {
          setPropState("idle");
          setProperties(null);
        }
        return;
      }
      setProperties(null);
      setPropError(null);
      setPropState("loading");
      try {
        const data = await getTenantCustomProperties(tenantId);
        if (cancelled) return;
        setProperties(data);
        setPropState("ready");
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        if (err instanceof ApiError && err.status === 403) {
          setPropState("forbidden");
        } else {
          setPropState("error");
          setPropError(message);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selected, propReloadKey]);

  const visible = (customers ?? []).filter((c) => {
    if (!filter.trim()) return true;
    const needle = filter.trim().toLowerCase();
    return (
      c.name.toLowerCase().includes(needle) ||
      c.slug.toLowerCase().includes(needle) ||
      c.tenant_id.toLowerCase().includes(needle) ||
      (c.plan_name ?? "").toLowerCase().includes(needle)
    );
  });

  /** Re-fetch for the CURRENT tenant, deliberately leaving it selected. */
  function refreshProperties() {
    setPropReloadKey((k) => k + 1);
  }

  async function handleDeactivate(property: TenantCustomProperty) {
    const tenantId = selected?.tenant_id;
    if (!tenantId) return;
    if (!window.confirm(`Deactivate custom property \u201c${property.display_name}\u201d?`)) {
      return;
    }
    setPropNotice(null);
    setPropError(null);
    try {
      // 204 No Content: apiFetch resolves with undefined and never parses a body.
      await deleteTenantCustomProperty(tenantId, property.id);
      setPropNotice(`Custom property \u201c${property.display_name}\u201d deactivated.`);
      refreshProperties();
    } catch (err) {
      // A 404 means "not this tenant's (or already gone)". The backend refuses to
      // confirm which, so the UI does not speculate either.
      setPropError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>PLATFORM ADMIN · CUSTOMERS</h2>
          {allowed === false && (
            <p style={{ color: "#a7bec7" }}>
              Platform-admin access required. <Link href="/dashboard">Back to dashboard</Link>
            </p>
          )}
          {error && <p style={{ color: "#ff6b6b" }}>{error}</p>}
          {allowed && customers && (
            <>
              <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
                {visible.length} of {customers.length} tenants
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter by name, slug, id or plan…"
                  style={filterInput}
                />
              </p>

              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                    <th style={th}>Tenant</th>
                    <th style={th}>Tenant ID</th>
                    <th style={th}>Plan</th>
                    <th style={th}>Subscription</th>
                    <th style={th}>Devices</th>
                    <th style={th}>Users</th>
                    <th style={th}>API usage</th>
                    <th style={th}>Created</th>
                    <th style={th}>Last activity</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((c) => (
                    <tr
                      key={c.tenant_id}
                      style={{ borderTop: "1px solid var(--line)", cursor: "pointer" }}
                      onClick={() => setSelected(c)}
                    >
                      <td style={td}>
                        {c.name}
                        <span style={{ color: "var(--muted)" }}> ({c.slug})</span>
                      </td>
                      <td style={{ ...td, fontFamily: "monospace", fontSize: 11 }}>
                        {c.tenant_id}
                      </td>
                      <td style={td}>{c.plan_name ?? "No plan"}</td>
                      <td style={td}>
                        {c.subscription_status}
                        {c.over_limit_blocked && (
                          <span style={{ color: "var(--amber)" }}> · over-limit</span>
                        )}
                      </td>
                      {/* Not returned by the endpoint - never invented. */}
                      <td style={noData}>No data</td>
                      <td style={noData}>No data</td>
                      <td style={noData}>No data</td>
                      <td style={td}>{fmt(c.created_at)}</td>
                      <td style={td}>{fmt(c.last_activity_at)}</td>
                    </tr>
                  ))}
                  {visible.length === 0 && (
                    <tr>
                      <td style={{ ...td, color: "var(--muted)" }} colSpan={9}>
                        No tenants match this filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </section>
        {selected && (
          <section className="hud-panel">
            <h2>TENANT · {selected.name}</h2>

            {detailState === "loading" && (
              <p style={{ color: "var(--muted)" }}>Loading tenant…</p>
            )}
            {detailState === "not_found" && (
              <p style={{ color: "var(--amber)" }}>Tenant not found.</p>
            )}
            {detailState === "forbidden" && (
              <p style={{ color: "#ff6b6b" }}>Access denied.</p>
            )}
            {detailState === "error" && (
              <p style={{ color: "#ff6b6b" }}>
                Unable to load tenant details{detailError ? `: ${detailError}` : "."}
              </p>
            )}

            {detailState === "ready" && detail && (
              <>
                <h3 style={h3}>TENANT PROFILE</h3>
                <dl style={dl}>
                  <Field label="Tenant name" value={detail.profile.name} />
                  <Field label="Tenant ID" value={detail.profile.tenant_id} mono />
                  <Field label="Slug" value={detail.profile.slug} />
                  <Field
                    label="Status"
                    value={detail.profile.is_active ? "Active" : "Inactive"}
                  />
                  <Field label="Created" value={maybe(detail.profile.created_at)} />
                  <Field label="Updated" value={maybe(detail.profile.updated_at)} />
                  {/* Null from the backend renders as unavailable, never invented. */}
                  <Field label="Display name" value={maybe(detail.profile.display_name)} />
                  <Field label="Legal name" value={maybe(detail.profile.legal_name)} />
                  <Field label="Industry" value={maybe(detail.profile.industry)} />
                  <Field label="Country" value={maybe(detail.profile.country)} />
                  <Field label="Timezone" value={maybe(detail.profile.timezone)} />
                  <Field label="Locale" value={maybe(detail.profile.locale)} />
                  <Field
                    label="Contacts"
                    value={detail.profile.contacts ? "Configured" : "No data"}
                    muted={!detail.profile.contacts}
                  />
                  <Field
                    label="Branding"
                    value={detail.profile.branding ? "Configured" : "No data"}
                    muted={!detail.profile.branding}
                  />
                  <Field
                    label="Custom properties"
                    value={detail.profile.custom_properties ? "Configured" : "No data"}
                    muted={!detail.profile.custom_properties}
                  />
                </dl>

                <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                  <span
                    style={{
                      fontSize: 11,
                      letterSpacing: 1,
                      padding: "2px 8px",
                      borderRadius: 6,
                      border: "1px solid",
                      color: detail.profile.is_active ? "var(--cyan)" : "var(--amber)",
                      borderColor: detail.profile.is_active ? "var(--cyan)" : "var(--amber)",
                    }}
                  >
                    {detail.profile.is_active ? "ACTIVE" : "SUSPENDED"}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setLifecycleError(null);
                      setLifecycleNotice(null);
                      setLifecycleOpen(true);
                    }}
                    style={{
                      ...btn,
                      marginTop: 0,
                      color: detail.profile.is_active ? "var(--amber)" : "var(--cyan)",
                      borderColor: detail.profile.is_active ? "var(--amber)" : "var(--cyan)",
                    }}
                  >
                    {detail.profile.is_active ? "Suspend tenant" : "Reactivate tenant"}
                  </button>
                  {lifecycleNotice && (
                    <span style={{ fontSize: 12, color: "var(--cyan)" }}>{lifecycleNotice}</span>
                  )}
                </div>
                {lifecycleError && (
                  <p style={{ color: "#ff6b6b", fontSize: 13 }} role="alert">
                    {lifecycleError}
                  </p>
                )}

                <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
                  <button
                    type="button"
                    onClick={() => {
                      setRenameResult(null);
                      setRenameOpen(true);
                    }}
                    style={{ ...btn, marginTop: 0 }}
                  >
                    Rename tenant
                  </button>
                  {renameResult && (
                    <span style={{ fontSize: 12, color: "var(--cyan)" }}>
                      {renameResult.changed
                        ? "Tenant renamed."
                        : "No change - the submitted values already matched."}
                    </span>
                  )}
                </div>
                {/* Canonical before/after as returned by the backend. */}
                {renameResult?.changed && (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
                    Before: {renameResult.before.name} / {renameResult.before.slug}
                    <br />
                    After: {renameResult.after.name} / {renameResult.after.slug}
                  </p>
                )}

                <h3 style={h3}>SUBSCRIPTION</h3>
                <dl style={dl}>
                  <Field label="Plan" value={maybe(detail.subscription.plan_name)} />
                  <Field label="Plan tier" value={maybe(detail.subscription.plan_tier)} />
                  <Field
                    label="Subscription status"
                    value={maybe(detail.subscription.subscription_status)}
                  />
                  <Field
                    label="Billing cycle"
                    value={maybe(detail.subscription.billing_interval)}
                  />
                  <Field
                    label="Current period start"
                    value={maybe(detail.subscription.current_period_start)}
                  />
                  <Field
                    label="Renewal (period end)"
                    value={maybe(detail.subscription.current_period_end)}
                  />
                  <Field label="Trial ends" value={maybe(detail.subscription.trial_ends_at)} />
                  <Field label="Over plan limits" value={tri(detail.subscription.over_limit)} />
                  <Field
                    label="New-resource blocked"
                    value={tri(detail.subscription.over_limit_blocked)}
                  />
                </dl>

                <h3 style={h3}>USAGE</h3>
                {Object.keys(detail.usage).length === 0 ? (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>No data</p>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                        <th style={th}>Resource</th>
                        <th style={th}>Used / limit</th>
                        <th style={th}>%</th>
                        <th style={th}>State</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(detail.usage).map(([key, u]) => (
                        <tr key={key} style={{ borderTop: "1px solid var(--line)" }}>
                          <td style={td}>{u.label}</td>
                          <td style={td}>
                            {u.used} / {u.limit}
                          </td>
                          <td style={td}>{u.pct}%</td>
                          <td style={{ ...td, color: u.over_limit ? "var(--amber)" : "inherit" }}>
                            {u.over_limit ? "over limit" : "within limit"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <h3 style={h3}>COUNTS</h3>
                <dl style={dl}>
                  <Field label="Devices" value={String(detail.counts.devices_total)} />
                  <Field
                    label="Users"
                    value={`${detail.counts.users_total} (${detail.counts.users_active} active, ${detail.counts.users_inactive} inactive)`}
                  />
                  <Field label="Roles" value={String(detail.counts.roles_total)} />
                  <Field
                    label="Discovery scopes"
                    value={String(detail.counts.discovery_scopes_total)}
                  />
                  <Field label="Last activity" value={maybe(detail.last_activity_at)} />
                  {/* Not returned by the admin API - stated, not faked. */}
                  <Field
                    label="Interfaces / Alerts / Relationships / Sites"
                    value="No data"
                    muted
                  />
                </dl>

                <h3 style={h3}>DISCOVERY SCOPES</h3>
                {detail.discovery_scopes.length === 0 ? (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>
                    No discovery scopes configured
                  </p>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                        <th style={th}>Scope</th>
                        <th style={th}>Type</th>
                        <th style={th}>Target</th>
                        <th style={th}>Protocols</th>
                        <th style={th}>Credential</th>
                        <th style={th}>Status</th>
                        <th style={th}>Last run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.discovery_scopes.map((s) => (
                        <tr key={s.id} style={{ borderTop: "1px solid var(--line)" }}>
                          <td style={td}>
                            {s.name}
                            {!s.enabled && (
                              <span style={{ color: "var(--muted)" }}> · disabled</span>
                            )}
                          </td>
                          <td style={td}>{s.scope_type}</td>
                          <td style={{ ...td, fontFamily: "monospace", fontSize: 12 }}>
                            {s.target}
                          </td>
                          <td style={td}>
                            {s.allowed_protocols.length
                              ? s.allowed_protocols.map((p) => p.toUpperCase()).join(" · ")
                              : "No data"}
                          </td>
                          {/* Configured/not only - the secret itself is never fetched. */}
                          <td style={td}>
                            {s.credential_configured ? "Configured" : "None"}
                          </td>
                          <td style={td}>{s.status}</td>
                          <td style={td}>{maybe(s.last_run_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <h3 style={h3}>CUSTOM PROPERTIES</h3>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
                  <button
                    type="button"
                    onClick={() => {
                      setPropNotice(null);
                      setPropError(null);
                      setPropForm({ property: null });
                    }}
                    disabled={propState !== "ready"}
                    style={{
                      ...btn,
                      marginTop: 0,
                      opacity: propState === "ready" ? 1 : 0.5,
                      cursor: propState === "ready" ? "pointer" : "not-allowed",
                    }}
                  >
                    Add Custom Property
                  </button>
                  {propNotice && (
                    <span style={{ color: "var(--cyan)", fontSize: 12 }}>{propNotice}</span>
                  )}
                </div>

                {propState === "loading" && (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>
                    Loading custom properties...
                  </p>
                )}
                {propState === "forbidden" && (
                  <p style={{ color: "#ff6b6b", fontSize: 13 }}>Access denied.</p>
                )}
                {propState === "error" && (
                  <p style={{ color: "#ff6b6b", fontSize: 13 }}>
                    Unable to load custom properties{propError ? `: ${propError}` : "."}
                  </p>
                )}
                {propState === "ready" && properties?.length === 0 && (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>
                    No custom properties configured
                  </p>
                )}
                {propState === "ready" && properties && properties.length > 0 && (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                        <th style={th}>Display Name</th>
                        <th style={th}>Key</th>
                        <th style={th}>Type</th>
                        <th style={th}>Required</th>
                        <th style={th}>Default</th>
                        <th style={th}>Status</th>
                        <th style={th}>Allowed Values</th>
                        <th style={th}>Updated</th>
                        <th style={th}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {properties.map((p) => (
                        <tr key={p.id} style={{ borderTop: "1px solid var(--line)" }}>
                          <td style={td}>
                            {p.display_name}
                            {p.description ? (
                              <span style={{ color: "var(--muted)" }}> - {p.description}</span>
                            ) : null}
                          </td>
                          <td style={{ ...td, fontFamily: "monospace", fontSize: 12 }}>
                            {p.key}
                          </td>
                          <td style={td}>{p.data_type}</td>
                          {/* Required / optional is stated, never inferred. */}
                          <td style={td}>{p.required ? "Required" : "Optional"}</td>
                          <td style={p.default_value ? td : noData}>
                            {p.default_value ? p.default_value : "No data"}
                          </td>
                          <td style={td}>
                            <span
                              style={{ color: p.is_active ? "var(--cyan)" : "var(--muted)" }}
                            >
                              {p.is_active ? "Active" : "Inactive"}
                            </span>
                          </td>
                          {/* Only meaningful for enum definitions. */}
                          <td style={td}>
                            {p.data_type === "enum" && p.allowed_values.length
                              ? p.allowed_values.join(", ")
                              : "No data"}
                          </td>
                          <td style={td}>{fmt(p.updated_at)}</td>
                          <td style={td}>
                            <button
                              type="button"
                              onClick={() => {
                                setPropNotice(null);
                                setPropError(null);
                                setPropForm({ property: p });
                              }}
                              style={rowBtn}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeactivate(p)}
                              style={{ ...rowBtn, borderColor: "var(--amber)", color: "var(--amber)" }}
                            >
                              Deactivate
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
                  Usage and limits are the canonical entitlements values, identical to
                  the Usage screen. <Link href="/usage">Open usage &amp; billing</Link>.
                </p>

                <h3 style={h3}>USERS</h3>
                <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
                  Read-only. Role names only — credentials are never returned by the
                  API.
                </p>
                {detail.users.total === 0 ? (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>No users configured</p>
                ) : (
                  <>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                      <thead>
                        <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                          <th style={th}>Name</th>
                          <th style={th}>Email</th>
                          <th style={th}>Status</th>
                          <th style={th}>Roles</th>
                          <th style={th}>Last login</th>
                          <th style={th}>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.users.items.map((u) => (
                          <tr key={u.id} style={{ borderTop: "1px solid var(--line)" }}>
                            <td style={td}>{maybe(u.full_name)}</td>
                            <td style={td}>{u.email}</td>
                            <td style={{ ...td, color: u.is_active ? "inherit" : "var(--muted)" }}>
                              {u.is_active ? "Active" : "Disabled"}
                              {u.is_superuser && (
                                <span style={{ color: "var(--muted)" }}> · platform admin</span>
                              )}
                            </td>
                            <td style={td}>
                              {u.roles.length ? u.roles.join(" · ") : "No roles"}
                            </td>
                            <td style={td}>{maybe(u.last_login_at)}</td>
                            <td style={td}>{maybe(u.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {detail.users.truncated && (
                      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
                        Showing {detail.users.returned} of {detail.users.total} users.
                      </p>
                    )}
                  </>
                )}

                <h3 style={h3}>ROLES &amp; PERMISSIONS</h3>
                <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
                  Read-only view of this tenant&apos;s roles. Roles are tenant-scoped;
                  it grants no platform-admin rights.
                </p>
                {detail.roles.length === 0 ? (
                  <p style={{ color: "var(--muted)", fontSize: 13 }}>No roles configured</p>
                ) : (
                  detail.roles.map((role) => {
                    // Group by the REAL permission-key prefix (e.g. devices.read ->
                    // "devices"). Nothing is invented: an empty prefix falls under
                    // "(ungrouped)".
                    const groups = new Map<string, AdminTenantPermission[]>();
                    for (const perm of role.permissions) {
                      const prefix = perm.key.includes(".")
                        ? perm.key.split(".")[0]
                        : "(ungrouped)";
                      const bucket = groups.get(prefix) ?? [];
                      bucket.push(perm);
                      groups.set(prefix, bucket);
                    }
                    return (
                      <div key={role.id} style={roleCard}>
                        <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
                          <strong style={{ color: "var(--cyan)" }}>{role.name}</strong>
                          <span style={{ fontSize: 11, color: "var(--muted)" }}>
                            scope: {role.scope} · {role.user_count}{" "}
                            {role.user_count === 1 ? "user" : "users"}
                          </span>
                        </div>
                        {role.description && (
                          <p style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0" }}>
                            {role.description}
                          </p>
                        )}
                        {role.permissions.length === 0 ? (
                          <p style={{ fontSize: 12, color: "var(--muted)" }}>
                            No permissions configured
                          </p>
                        ) : (
                          Array.from(groups.entries()).map(([prefix, perms]) => (
                            <div key={prefix} style={{ marginTop: 6 }}>
                              <span
                                style={{
                                  fontSize: 11,
                                  color: "var(--cyan)",
                                  letterSpacing: 1,
                                }}
                              >
                                {prefix.toUpperCase()}
                              </span>
                              <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>
                                {perms.map((perm) => (
                                  <li
                                    key={perm.id}
                                    style={{ fontSize: 12, color: "#a7bec7" }}
                                  >
                                    {perm.key}
                                    {perm.description ? ` — ${perm.description}` : ""}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ))
                        )}
                      </div>
                    );
                  })
                )}
              </>
            )}

            <button type="button" onClick={() => setSelected(null)} style={btn}>
              Close
            </button>
          </section>
        )}

        {/* Suspension/reactivation confirmation. Requires the OPEN tenant, and the
            decision is passed in from that tenant's own state - it cannot be
            misapplied to another tenant. */}
        {selected && lifecycleOpen && detailState === "ready" && detail && (
          <LifecycleConfirmModal
            tenantName={detail.profile.name}
            suspending={detail.profile.is_active}
            saving={lifecycleSaving}
            error={lifecycleError}
            onCancel={() => {
              setLifecycleOpen(false);
              setLifecycleError(null);
            }}
            onConfirm={() => submitStatus(!detail.profile.is_active)}
          />
        )}

        {/* Rename is only ever offered for the OPEN tenant, and the id comes from
            that selection - the modal cannot target another tenant. */}
        {selected && renameOpen && (
          <RenameTenantModal
            tenantId={selected.tenant_id}
            currentName={detail?.profile.name ?? selected.name}
            currentSlug={detail?.profile.slug ?? selected.slug}
            onClose={() => setRenameOpen(false)}
            onRenamed={(result) => {
              applyRename(result);
              setRenameOpen(false);
            }}
          />
        )}

        {/* Rendered only for a selected tenant, so `tenantId` is always the one
            whose panel is open - the modal cannot target another tenant. */}
        {selected && propForm && (
          <CustomPropertyModal
            tenantId={selected.tenant_id}
            property={propForm.property}
            onClose={() => setPropForm(null)}
            onSaved={(message) => {
              setPropForm(null);
              setPropNotice(message);
              refreshProperties();
            }}
          />
        )}

      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "8px 10px" };
const td: React.CSSProperties = { padding: "8px 10px", color: "#a7bec7" };
const noData: React.CSSProperties = { ...td, color: "var(--muted)" };
const filterInput: React.CSSProperties = {
  marginLeft: 12,
  padding: "4px 8px",
  fontSize: 12,
  background: "transparent",
  color: "inherit",
  border: "1px solid var(--line)",
  borderRadius: 6,
  minWidth: 260,
};
const dl: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "260px 1fr",
  rowGap: 6,
  columnGap: 12,
  margin: 0,
};
const btn: React.CSSProperties = {
  marginTop: 12,
  padding: "4px 10px",
  fontSize: 12,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const h3: React.CSSProperties = {
  color: "var(--cyan)",
  fontSize: 12,
  letterSpacing: 1,
  marginTop: 18,
  marginBottom: 6,
};
const roleCard: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 8,
  padding: "8px 10px",
  marginBottom: 8,
};
/** Compact in-row action (Edit / Deactivate). */
const rowBtn: React.CSSProperties = {
  marginRight: 6,
  padding: "2px 8px",
  fontSize: 11,
  background: "transparent",
  color: "var(--cyan)",
  border: "1px solid var(--cyan)",
  borderRadius: 6,
  cursor: "pointer",
};
const formLabel: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  color: "var(--muted)",
  marginBottom: 3,
};
const formInput: React.CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  fontSize: 13,
  background: "transparent",
  color: "#a7bec7",
  border: "1px solid var(--line)",
  borderRadius: 6,
};
const field: React.CSSProperties = { marginBottom: 10 };

/** Null/empty from the backend is "No data" - never a fabricated value. */
const ISO_LIKE = /^\d{4}-\d{2}-\d{2}T/;

function maybe(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "No data";
  // Only timestamps are reformatted; plain strings (plan name, slug, status)
  // are passed through verbatim so they can never become "Invalid Date".
  return ISO_LIKE.test(value) ? fmt(value) : value;
}

/** Tri-state: null means the canonical service could not answer (unknown). */
function tri(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "No data";
  return value ? "Yes" : "No";
}

function Field({
  label,
  value,
  mono,
  muted,
}: {
  label: string;
  value: string;
  mono?: boolean;
  muted?: boolean;
}) {
  return (
    <>
      <dt style={{ color: "var(--muted)", fontSize: 12 }}>{label}</dt>
      <dd
        style={{
          margin: 0,
          fontSize: 13,
          color: muted ? "var(--muted)" : "#a7bec7",
          fontFamily: mono ? "monospace" : undefined,
        }}
      >
        {value}
      </dd>
    </>
  );
}

function fmt(iso: string | null): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Controlled data types. Mirrors the backend `DATA_TYPES`. */
const DATA_TYPE_OPTIONS: CustomPropertyDataType[] = [
  "string",
  "integer",
  "boolean",
  "date",
  "datetime",
  "enum",
];

/** Declarative rule keys the backend accepts. Data only - never executed. */
const VALIDATION_RULE_KEYS = ["min_length", "max_length", "min", "max", "regex", "pattern"];

/**
 * Create/edit dialog for one tenant-scoped custom-property definition.
 *
 * `tenantId` comes from the OPEN tenant panel, so a submission can only ever
 * target the tenant whose details are displayed.
 *
 * `validation_rules` is edited and checked as a JSON OBJECT. It is
 * configuration DATA: never compiled, never evaluated, never executed.
 */
function CustomPropertyModal({
  tenantId,
  property,
  onClose,
  onSaved,
}: {
  tenantId: string;
  property: TenantCustomProperty | null;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const isEdit = property !== null;
  const [displayName, setDisplayName] = useState(property?.display_name ?? "");
  const [key, setKey] = useState(property?.key ?? "");
  const [description, setDescription] = useState(property?.description ?? "");
  const [dataType, setDataType] = useState<CustomPropertyDataType>(property?.data_type ?? "string");
  const [required, setRequired] = useState(property?.required ?? false);
  const [defaultValue, setDefaultValue] = useState(property?.default_value ?? "");
  const [allowedValuesText, setAllowedValuesText] = useState(
    (property?.allowed_values ?? []).join("\n"),
  );
  const [validationText, setValidationText] = useState(
    JSON.stringify(property?.validation_rules ?? {}, null, 2),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEnum = dataType === "enum";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    // Duplicate-submission guard: re-entrant submits are ignored in flight.
    if (saving) return;
    setError(null);

    // `validation_rules` stays STRUCTURED CONFIGURATION: parsed as JSON and
    // shape-checked only. It is never compiled, evaluated or executed.
    let rules: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(validationText.trim() || "{}");
      if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
        setError("Validation rules must be a JSON object, e.g. {\"max_length\": 40}");
        return;
      }
      const unknownKeys = Object.keys(parsed).filter((k) => !VALIDATION_RULE_KEYS.includes(k));
      if (unknownKeys.length > 0) {
        setError(
          `Unsupported validation rule key(s): ${unknownKeys.join(", ")}. Allowed: ${VALIDATION_RULE_KEYS.join(", ")}`,
        );
        return;
      }
      rules = parsed as Record<string, unknown>;
    } catch {
      setError("Validation rules must be valid JSON, e.g. {\"max_length\": 40}");
      return;
    }

    // Allowed values only travel for enum definitions; anything else is dropped
    // because the backend rejects allowed_values on non-enum types.
    const allowedValues = isEnum
      ? allowedValuesText
          .split("\n")
          .map((v) => v.trim())
          .filter((v) => v.length > 0)
      : [];
    const trimmedDefault = defaultValue.trim();

    setSaving(true);
    try {
      if (isEdit && property) {
        await updateTenantCustomProperty(tenantId, property.id, {
          display_name: displayName.trim(),
          description: description.trim() || null,
          required,
          default_value: trimmedDefault || null,
          allowed_values: allowedValues,
          validation_rules: rules,
        });
        onSaved(`Custom property "${displayName.trim()}" updated.`);
      } else {
        await createTenantCustomProperty(tenantId, {
          key: key.trim(),
          display_name: displayName.trim(),
          description: description.trim() || null,
          data_type: dataType,
          required,
          default_value: trimmedDefault || null,
          allowed_values: allowedValues,
          validation_rules: rules,
        });
        onSaved(`Custom property "${displayName.trim()}" created.`);
      }
    } catch (err) {
      setSaving(false);
      // 409 = duplicate key. Surface the user-facing meaning only; the database
      // constraint name is never shown (nor returned by the backend).
      if (err instanceof ApiError && err.status === 409) {
        setError("A custom property with this key already exists for this tenant.");
        return;
      }
      // Otherwise show the backend's own validation/authorization message.
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isEdit ? "Edit custom property" : "Add custom property"}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "10vh",
        zIndex: 50,
      }}
    >
      <form
        onSubmit={submit}
        className="hud-panel"
        style={{ width: "100%", maxWidth: 560, maxHeight: "80vh", overflowY: "auto" }}
      >
        <h2>{isEdit ? "EDIT CUSTOM PROPERTY" : "ADD CUSTOM PROPERTY"}</h2>
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          Applies to tenant <span style={{ fontFamily: "monospace" }}>{tenantId}</span>
        </p>

        {error && (
          <p style={{ color: "#ff6b6b", fontSize: 13 }} role="alert">
            {error}
          </p>
        )}

        <div style={field}>
          <label style={formLabel} htmlFor="cp-display-name">
            Display Name
          </label>
          <input
            id="cp-display-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            maxLength={255}
            style={formInput}
          />
        </div>

        <div style={field}>
          <label style={formLabel} htmlFor="cp-key">
            Key
          </label>
          <input
            id="cp-key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            required
            maxLength={64}
            pattern="[a-z][a-z0-9_]*"
            title="Start with a lowercase letter; lowercase letters, digits and underscores only."
            readOnly={isEdit}
            disabled={isEdit}
            style={{ ...formInput, opacity: isEdit ? 0.6 : 1 }}
          />
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Lowercase letters, digits and underscores. {isEdit ? "Immutable once created." : ""}
          </span>
        </div>

        <div style={field}>
          <label style={formLabel} htmlFor="cp-description">
            Description
          </label>
          <input
            id="cp-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={500}
            style={formInput}
          />
        </div>


        <div style={field}>
          <label style={formLabel} htmlFor="cp-data-type">
            Data Type
          </label>
          <select
            id="cp-data-type"
            value={dataType}
            onChange={(e) => {
              const next = e.target.value as CustomPropertyDataType;
              setDataType(next);
              // Leaving enum discards allowed values so an invalid combination
              // can never be submitted.
              if (next !== "enum") setAllowedValuesText("");
            }}
            disabled={isEdit}
            style={{ ...formInput, opacity: isEdit ? 0.6 : 1 }}
          >
            {DATA_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t} style={{ color: "#0b1418" }}>
                {t}
              </option>
            ))}
          </select>
          {isEdit && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>Immutable once created.</span>
          )}
        </div>

        <div style={field}>
          <label style={{ ...formLabel, display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={required}
              onChange={(e) => setRequired(e.target.checked)}
            />
            Required
          </label>
        </div>

        <div style={field}>
          <label style={formLabel} htmlFor="cp-default">
            Default Value
          </label>
          <input
            id="cp-default"
            value={defaultValue}
            onChange={(e) => setDefaultValue(e.target.value)}
            maxLength={500}
            style={formInput}
          />
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Required when the property is marked Required.
          </span>
        </div>

        {/* Only shown for enum: the backend rejects allowed_values elsewhere. */}
        {isEnum && (
          <div style={field}>
            <label style={formLabel} htmlFor="cp-allowed">
              Allowed Values (one per line)
            </label>
            <textarea
              id="cp-allowed"
              value={allowedValuesText}
              onChange={(e) => setAllowedValuesText(e.target.value)}
              rows={4}
              style={{ ...formInput, fontFamily: "monospace", fontSize: 12 }}
            />
          </div>
        )}

        <div style={field}>
          <label style={formLabel} htmlFor="cp-validation">
            Validation Rules (JSON object - configuration, never executed)
          </label>
          <textarea
            id="cp-validation"
            value={validationText}
            onChange={(e) => setValidationText(e.target.value)}
            rows={4}
            style={{ ...formInput, fontFamily: "monospace", fontSize: 12 }}
          />
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Allowed keys: {VALIDATION_RULE_KEYS.join(", ")}
          </span>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="submit"
            disabled={saving}
            style={{ ...btn, marginTop: 0, opacity: saving ? 0.6 : 1 }}
          >
            {saving ? "Saving..." : isEdit ? "Save Changes" : "Create Property"}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            style={{ ...btn, marginTop: 0, color: "var(--muted)", borderColor: "var(--line)" }}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}



/** Tenant slug shape the backend enforces. Mirrored here for immediate feedback. */
const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * Rename ONE tenant (Phase 6.3F).
 *
 * Admin-protected server-side by `require_admin`; this component only supplies the
 * tenant id it was given, so it cannot target another tenant.
 *
 * `name` is always sent (the primary rename). `slug` is sent ONLY when the admin
 * actually edits it - sending an unchanged slug would be a needless write, and
 * the backend reports `changed: false` when nothing differs.
 */
function RenameTenantModal({
  tenantId,
  currentName,
  currentSlug,
  onClose,
  onRenamed,
}: {
  tenantId: string;
  currentName: string;
  currentSlug: string;
  onClose: () => void;
  onRenamed: (result: AdminTenantRenameResult) => void;
}) {
  const [name, setName] = useState(currentName);
  const [slug, setSlug] = useState(currentSlug);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedName = name.trim();
  const trimmedSlug = slug.trim();
  const nameChanged = trimmedName !== currentName;
  const slugChanged = trimmedSlug !== currentSlug;
  const slugValid = !slugChanged || SLUG_PATTERN.test(trimmedSlug);
  const canSubmit = !saving && trimmedName.length > 0 && slugValid;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    // Duplicate-submission guard: re-entrant submits are ignored in flight.
    if (!canSubmit) return;
    setError(null);

    if (!nameChanged && !slugChanged) {
      setError("Nothing to rename - the name and slug are unchanged.");
      return;
    }

    const payload: { name?: string; slug?: string } = {};
    if (nameChanged) payload.name = trimmedName;
    if (slugChanged) payload.slug = trimmedSlug;

    setSaving(true);
    try {
      const result = await renameAdminTenant(tenantId, payload);
      onRenamed(result);
    } catch (err) {
      setSaving(false);
      if (err instanceof ApiError && err.status === 409) {
        setError("That slug is already in use by another tenant.");
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setError("Platform-admin access required.");
        return;
      }
      // Otherwise the backend's own validation message.
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Rename tenant"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "10vh",
        zIndex: 50,
      }}
    >
      <form
        onSubmit={submit}
        className="hud-panel"
        style={{ width: "100%", maxWidth: 520, maxHeight: "80vh", overflowY: "auto" }}
      >
        <h2>RENAME TENANT</h2>
        <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
          Applies to tenant <span style={{ fontFamily: "monospace" }}>{tenantId}</span>
        </p>

        {error && (
          <p style={{ color: "#ff6b6b", fontSize: 13 }} role="alert">
            {error}
          </p>
        )}

        <div style={field}>
          <label style={formLabel} htmlFor="rn-name">
            Tenant name
          </label>
          <input
            id="rn-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={255}
            style={formInput}
          />
        </div>

        <div style={field}>
          <label style={formLabel} htmlFor="rn-slug">
            Slug
          </label>
          <input
            id="rn-slug"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            maxLength={100}
            style={{ ...formInput, fontFamily: "monospace" }}
          />
          <span style={{ fontSize: 11, color: slugValid ? "var(--muted)" : "#ff6b6b" }}>
            {slugValid
              ? "Lowercase letters, digits and single dashes."
              : "Invalid slug: lowercase letters, digits and single dashes only."}
          </span>
        </div>

        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          Before: {currentName} / {currentSlug}
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="submit"
            disabled={!canSubmit}
            style={{ ...btn, marginTop: 0, opacity: canSubmit ? 1 : 0.6 }}
          >
            {saving ? "Saving..." : "Rename tenant"}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            style={{ ...btn, marginTop: 0, color: "var(--muted)", borderColor: "var(--line)" }}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}



/**
 * Confirmation for a tenant lifecycle change (Phase 6.4A).
 *
 * Suspension is destructive-feeling and wide-reaching, so it always requires an
 * explicit, clearly-worded confirmation. Reactivation uses the same dialog for
 * consistency - it is a state change either way.
 */
function LifecycleConfirmModal({
  tenantName,
  suspending,
  saving,
  error,
  onCancel,
  onConfirm,
}: {
  tenantName: string;
  suspending: boolean;
  saving: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={suspending ? "Suspend tenant" : "Reactivate tenant"}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "10vh",
        zIndex: 50,
      }}
    >
      <div className="hud-panel" style={{ width: "100%", maxWidth: 520 }}>
        <h2>{suspending ? "SUSPEND TENANT?" : "REACTIVATE TENANT?"}</h2>

        {error && (
          <p style={{ color: "#ff6b6b", fontSize: 13 }} role="alert">
            {error}
          </p>
        )}

        <p style={{ fontSize: 13, color: "#a7bec7" }}>
          {suspending ? (
            <>
              Suspending <strong>{tenantName}</strong> will prevent normal tenant activity
              while retaining its configuration and historical data.
            </>
          ) : (
            <>
              Reactivating <strong>{tenantName}</strong> will allow normal tenant activity
              again. Its existing configuration is unchanged.
            </>
          )}
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            style={{ ...btn, marginTop: 0, color: "var(--muted)", borderColor: "var(--line)" }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={saving}
            style={{
              ...btn,
              marginTop: 0,
              color: suspending ? "var(--amber)" : "var(--cyan)",
              borderColor: suspending ? "var(--amber)" : "var(--cyan)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "Working..." : suspending ? "Suspend Tenant" : "Reactivate Tenant"}
          </button>
        </div>
      </div>
    </div>
  );
}

