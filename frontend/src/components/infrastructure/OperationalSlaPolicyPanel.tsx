"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createFailoverPolicy,
  deleteFailoverPolicy,
  FAILOVER_POLICY_METRICS,
  failoverMetricDirection,
  getFailoverPolicy,
  listFailoverPolicies,
  listScopeLinks,
  listScopeSites,
  policyErrorHint,
  toApiFailure,
  updateFailoverPolicy,
  type FailoverPolicyInput,
  type FailoverPolicyOut,
  type FailoverPolicyScope,
  type ScopeLinkOption,
  type ScopeSiteOption,
} from "@/lib/infrastructureIntelligence";

/**
 * OperationalSlaPolicyPanel — manage the thresholds the failover detector
 * evaluates live WAN link telemetry against.
 *
 * These are OPERATIONAL SLA policies (`/api/failover/policies`), deliberately
 * distinct from the business SLA contract model at `/api/sla/policies`.
 *
 * Design rules, in the order they matter:
 *
 * - **The backend is authoritative.** Nothing is clamped, coerced or
 *   pre-emptively rejected here beyond basic form usability: an unevaluatable
 *   policy (no level, a critical level on the wrong side of warning, an
 *   unreachable clear level, an out-of-domain value, a duplicate) is refused by
 *   the API and its own reason is displayed verbatim. A wrong-side critical is
 *   never "fixed" for the operator.
 * - **Guidance, not validation.** The metric direction is shown as a hint
 *   ("higher is worse" / "lower is worse") because it decides which way round
 *   warning and critical go. The ordering rule itself stays on the server.
 * - **Disabled is not deleted.** `is_active: false` is a toggle that keeps the
 *   policy visible; DELETE removes it from the list and from detection but is a
 *   SOFT delete - the row is retained. The two are labelled differently and the
 *   confirmation says exactly what happens.
 * - **Absent is not zero.** A threshold that is not set renders as "-", never 0.
 */

const cardStyle: React.CSSProperties = {
  background: "rgba(9, 16, 28, 0.72)",
  border: "1px solid rgba(90, 140, 200, 0.25)",
  borderRadius: 10,
  padding: "14px 16px",
  marginBottom: 14,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid rgba(90, 140, 200, 0.3)",
  opacity: 0.75,
  fontWeight: 600,
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid rgba(90, 140, 200, 0.12)",
};

const chipStyle: React.CSSProperties = {
  display: "inline-block",
  padding: "1px 7px",
  borderRadius: 6,
  border: "1px solid rgba(90, 140, 200, 0.35)",
  fontSize: 11,
  marginRight: 4,
};

const inputStyle: React.CSSProperties = {
  background: "rgba(2, 6, 16, 0.6)",
  border: "1px solid rgba(90, 140, 200, 0.3)",
  borderRadius: 6,
  padding: "5px 8px",
  color: "inherit",
  fontSize: 12,
  width: "100%",
};

const buttonStyle: React.CSSProperties = {
  background: "rgba(37, 99, 235, 0.18)",
  border: "1px solid rgba(90, 140, 200, 0.45)",
  borderRadius: 6,
  padding: "4px 10px",
  color: "inherit",
  fontSize: 12,
  cursor: "pointer",
};

const dangerButtonStyle: React.CSSProperties = {
  ...buttonStyle,
  background: "rgba(201, 83, 79, 0.16)",
  borderColor: "rgba(201, 83, 79, 0.5)",
};

const ACTIVE_COLOR = "#4fae7a";
const DISABLED_COLOR = "#d9a441";
const ERROR_COLOR = "#c9534f";

function when(value: string | null | undefined): string {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

/** A threshold renders as "-" when unset - never as 0. */
function level(value: number | null | undefined, unit = ""): string {
  return value === null || value === undefined ? "-" : `${value}${unit}`;
}

function StateBadge({ active }: { active: boolean }) {
  const color = active ? ACTIVE_COLOR : DISABLED_COLOR;
  return (
    <span
      style={{ ...chipStyle, borderColor: color, color }}
      title={
        active
          ? "Evaluated by the failover detector on every collection cycle"
          : "Retained and visible, but NOT evaluated until re-enabled"
      }
    >
      {active ? "active" : "inactive"}
    </span>
  );
}

function ScopeBadge({
  scope,
  scopeId,
  targetName,
}: {
  scope: FailoverPolicyScope;
  scopeId: string | null;
  targetName?: string | null;
}) {
  const label = scope === "all_wan" ? "global (all WAN)" : scope;
  return (
    <span style={chipStyle} title={scopeId ?? "applies to every WAN link"}>
      {label}
      {scope !== "all_wan" && scopeId
        ? `: ${targetName ?? `${scopeId.slice(0, 8)}...`}`
        : ""}
    </span>
  );
}

function ErrorBox({ status, message }: { status: number; message: string }) {
  return (
    <div
      style={{
        border: `1px solid ${ERROR_COLOR}`,
        borderRadius: 8,
        padding: "8px 10px",
        marginTop: 8,
        marginBottom: 8,
        fontSize: 12,
        color: ERROR_COLOR,
      }}
    >
      <div style={{ fontWeight: 600 }}>
        {status ? `Request failed (${status})` : "Request failed"}
      </div>
      {/* The backend's own words always come first and are never replaced. */}
      <div style={{ marginTop: 2 }}>{message}</div>
      <div style={{ marginTop: 4, opacity: 0.85 }}>
        {policyErrorHint(status, message)}
      </div>
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        border: `1px solid ${ACTIVE_COLOR}`,
        borderRadius: 8,
        padding: "6px 10px",
        marginBottom: 8,
        fontSize: 12,
        color: ACTIVE_COLOR,
      }}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------------ *
 * Form
 *
 * Thresholds are held as STRINGS so an empty field means "not set" (sent as
 * JSON null) and is never silently turned into 0. Only basic input sanity is
 * checked here; every domain rule stays on the server.
 * ------------------------------------------------------------------------ */

interface Draft {
  name: string;
  metric: string;
  scope: FailoverPolicyScope;
  scopeId: string;
  description: string;
  warning: string;
  critical: string;
  clear: string;
  samples: string;
  isActive: boolean;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  metric: "latency_ms",
  scope: "all_wan",
  scopeId: "",
  description: "",
  warning: "",
  critical: "",
  clear: "",
  samples: "2",
  isActive: true,
};

function draftFrom(policy: FailoverPolicyOut): Draft {
  return {
    name: policy.name,
    metric: policy.metric,
    scope: policy.scope,
    scopeId: policy.scope_id ?? "",
    description: policy.description ?? "",
    warning: policy.warning_threshold === null ? "" : String(policy.warning_threshold),
    critical:
      policy.critical_threshold === null ? "" : String(policy.critical_threshold),
    clear: policy.clear_threshold === null ? "" : String(policy.clear_threshold),
    samples: String(policy.min_consecutive_samples),
    isActive: policy.is_active,
  };
}

/** Empty means "not set" (null); a number means the number. */
function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : Number(trimmed);
}

/** Basic usability check on a threshold field, not a domain rule. */
function thresholdProblem(label: string, value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  return Number.isFinite(Number(trimmed)) ? null : `${label} must be a number.`;
}

function metricHint(metric: string): string {
  return failoverMetricDirection(metric) === "lower_is_worse"
    ? "Lower is worse for this metric (availability): critical must be BELOW warning, and clear at or above warning."
    : "Higher is worse for this metric: critical must be ABOVE warning, and clear at or below warning.";
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "block", fontSize: 12, marginBottom: 8 }}>
      <div style={{ opacity: 0.8, marginBottom: 3 }}>{label}</div>
      {children}
      {hint ? (
        <div style={{ opacity: 0.6, marginTop: 3, fontSize: 11 }}>{hint}</div>
      ) : null}
    </label>
  );
}


function ScopePicker({
  draft,
  sites,
  links,
  targetsError,
  onChange,
}: {
  draft: Draft;
  sites: ScopeSiteOption[];
  links: ScopeLinkOption[];
  targetsError: string | null;
  onChange: (scope: FailoverPolicyScope, scopeId: string) => void;
}) {
  return (
    <>
      <Field
        label="Scope"
        hint="global applies to every WAN link; site and link need a specific target."
      >
        <select
          aria-label="Policy scope"
          value={draft.scope}
          onChange={(event) =>
            onChange(event.target.value as FailoverPolicyScope, "")
          }
          style={inputStyle}
        >
          <option value="all_wan">global (all WAN links)</option>
          <option value="site">site</option>
          <option value="link">topology link</option>
        </select>
      </Field>

      {draft.scope !== "all_wan" ? (
        <Field
          label={`Target ${draft.scope === "site" ? "site" : "link"} (required)`}
          hint={
            targetsError ??
            (draft.scope === "site"
              ? `${sites.length} site(s) available`
              : `${links.length} link(s) available`)
          }
        >
          <select
            aria-label="Policy scope target"
            value={draft.scopeId}
            onChange={(event) => onChange(draft.scope, event.target.value)}
            style={inputStyle}
          >
            <option value="">select a target...</option>
            {draft.scope === "site"
              ? sites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name}
                    {site.code ? ` (${site.code})` : ""}
                  </option>
                ))
              : links.map((link) => (
                  <option key={link.id} value={link.id}>
                    {link.name} - {link.link_type} - {link.oper_status}
                  </option>
                ))}
          </select>
        </Field>
      ) : null}
    </>
  );
}


function PolicyForm({
  mode,
  draft,
  setDraft,
  sites,
  links,
  targetsError,
  localError,
  remoteError,
  busy,
  onSubmit,
  onCancel,
}: {
  mode: "create" | "edit";
  draft: Draft;
  setDraft: (next: Draft) => void;
  sites: ScopeSiteOption[];
  links: ScopeLinkOption[];
  targetsError: string | null;
  localError: string | null;
  remoteError: { status: number; message: string } | null;
  busy: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      style={{
        border: "1px solid rgba(90, 140, 200, 0.35)",
        borderRadius: 8,
        padding: "12px 14px",
        marginTop: 10,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        {mode === "create" ? "NEW OPERATIONAL SLA POLICY" : "EDIT POLICY"}
        <span style={{ fontWeight: 400, opacity: 0.7 }}>
          {" "}
          - these thresholds decide what the detector calls a breach
        </span>
      </div>

      <Field label="Name (required)">
        <input
          value={draft.name}
          maxLength={150}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          style={inputStyle}
        />
      </Field>

      <Field label="Metric" hint={metricHint(draft.metric)}>
        <select
          value={draft.metric}
          onChange={(event) => setDraft({ ...draft, metric: event.target.value })}
          style={inputStyle}
        >
          {FAILOVER_POLICY_METRICS.map((metric) => (
            <option key={metric} value={metric}>
              {metric}
            </option>
          ))}
        </select>
      </Field>

      <ScopePicker
        draft={draft}
        sites={sites}
        links={links}
        targetsError={targetsError}
        onChange={(scope, scopeId) => setDraft({ ...draft, scope, scopeId })}
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 10,
        }}
      >
        <Field label="Warning level" hint="leave empty to leave unset (-), never 0">
          <input
            value={draft.warning}
            onChange={(event) => setDraft({ ...draft, warning: event.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field
          label="Critical level"
          hint="at least one of warning/critical is required"
        >
          <input
            value={draft.critical}
            onChange={(event) => setDraft({ ...draft, critical: event.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field label="Clear / recovery level (optional)">
          <input
            value={draft.clear}
            onChange={(event) => setDraft({ ...draft, clear: event.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field
          label="Consecutive samples"
          hint="breached collection cycles required before it counts as an event (allowed 1-100)"
        >
          <input
            value={draft.samples}
            onChange={(event) => setDraft({ ...draft, samples: event.target.value })}
            style={inputStyle}
          />
        </Field>
      </div>

      <Field label="Description (optional)">
        <input
          value={draft.description}
          onChange={(event) =>
            setDraft({ ...draft, description: event.target.value })
          }
          style={inputStyle}
        />
      </Field>

      <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12 }}>
        <input
          type="checkbox"
          checked={draft.isActive}
          onChange={(event) =>
            setDraft({ ...draft, isActive: event.target.checked })
          }
        />
        Active (evaluated by the detector on every collection cycle)
      </label>

      {localError ? <ErrorBox status={0} message={localError} /> : null}
      {remoteError ? (
        <ErrorBox status={remoteError.status} message={remoteError.message} />
      ) : null}

      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button type="button" style={buttonStyle} disabled={busy} onClick={onSubmit}>
          {busy ? "Saving..." : mode === "create" ? "Create policy" : "Save changes"}
        </button>
        <button type="button" style={buttonStyle} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}


/** Resolve a scope target's display name from the loaded picker lists. */
function scopeTargetName(
  policy: FailoverPolicyOut,
  sites: ScopeSiteOption[],
  links: ScopeLinkOption[],
): string | null {
  if (!policy.scope_id) return null;
  if (policy.scope === "site") {
    const site = sites.find((candidate) => candidate.id === policy.scope_id);
    return site ? site.name : null;
  }
  if (policy.scope === "link") {
    const link = links.find((candidate) => candidate.id === policy.scope_id);
    return link ? link.name : null;
  }
  return null;
}

function PolicyDetail({
  policy,
  sites,
  links,
  onEdit,
  onClose,
}: {
  policy: FailoverPolicyOut;
  sites: ScopeSiteOption[];
  links: ScopeLinkOption[];
  onEdit: () => void;
  onClose: () => void;
}) {
  const target = scopeTargetName(policy, sites, links);
  return (
    <div
      style={{
        border: "1px solid rgba(90, 140, 200, 0.35)",
        borderRadius: 8,
        padding: "12px 14px",
        marginTop: 10,
        fontSize: 12,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6 }}>
        {policy.name}{" "}
        <span style={{ fontWeight: 400, opacity: 0.7 }}>({policy.metric})</span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          gap: 8,
        }}
      >
        <div>
          <div style={{ opacity: 0.7 }}>Scope</div>
          <div>
            <ScopeBadge
              scope={policy.scope}
              scopeId={policy.scope_id}
              targetName={target}
            />
          </div>
          {policy.scope_id ? (
            <div style={{ opacity: 0.6, wordBreak: "break-all" }}>
              {policy.scope_id}
            </div>
          ) : null}
        </div>
        <div>
          <div style={{ opacity: 0.7 }}>Levels (warning / critical / clear)</div>
          <div>
            {level(policy.warning_threshold)} / {level(policy.critical_threshold)} /{" "}
            {level(policy.clear_threshold)}
          </div>
          <div style={{ opacity: 0.6 }}>{metricHint(policy.metric)}</div>
        </div>
        <div>
          <div style={{ opacity: 0.7 }}>Consecutive samples</div>
          <div>{policy.min_consecutive_samples}</div>
        </div>
        <div>
          <div style={{ opacity: 0.7 }}>State</div>
          <div>
            <StateBadge active={policy.is_active} />
          </div>
        </div>
        <div>
          <div style={{ opacity: 0.7 }}>Created / updated</div>
          <div>{when(policy.created_at)}</div>
          <div style={{ opacity: 0.6 }}>{when(policy.updated_at)}</div>
        </div>
        <div>
          <div style={{ opacity: 0.7 }}>Description</div>
          <div>{policy.description ?? "-"}</div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button type="button" style={buttonStyle} onClick={onEdit}>
          Edit
        </button>
        <button type="button" style={buttonStyle} onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

function ConfirmRemove({
  policy,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  policy: FailoverPolicyOut;
  busy: boolean;
  error: { status: number; message: string } | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      style={{
        border: "1px solid rgba(201, 83, 79, 0.5)",
        borderRadius: 8,
        padding: "12px 14px",
        marginTop: 10,
        fontSize: 12,
      }}
    >
      <div style={{ fontWeight: 600, color: ERROR_COLOR }}>
        REMOVE POLICY &quot;{policy.name}&quot; FROM DETECTION?
      </div>
      <p style={{ margin: "6px 0" }}>
        The policy stops being evaluated immediately and disappears from this
        list. This is a <strong>soft delete</strong>: the record is retained so
        the audit trail keeps working - it is not erased from the database. If
        you only want to stop evaluating it while keeping it visible and easy to
        re-enable, cancel and set it to <strong>inactive</strong> instead.
      </p>
      {error ? <ErrorBox status={error.status} message={error.message} /> : null}
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          style={dangerButtonStyle}
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "Removing..." : "Remove from detection"}
        </button>
        <button type="button" style={buttonStyle} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}


/* ------------------------------------------------------------------------ *
 * Panel
 * ------------------------------------------------------------------------ */

type Failure = { status: number; message: string };

export default function OperationalSlaPolicyPanel({
  siteId = null,
}: {
  siteId?: string | null;
}) {
  const [policies, setPolicies] = useState<FailoverPolicyOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [formMode, setFormMode] = useState<"closed" | "create" | "edit">("closed");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [localError, setLocalError] = useState<string | null>(null);
  const [formError, setFormError] = useState<Failure | null>(null);

  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<Failure | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<Failure | null>(null);

  const [sites, setSites] = useState<ScopeSiteOption[]>([]);
  const [links, setLinks] = useState<ScopeLinkOption[]>([]);
  const [targetsError, setTargetsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const page = await listFailoverPolicies();
      setPolicies(page.policies ?? []);
    } catch (error) {
      setLoadError(toApiFailure(error));
      setPolicies([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Mount load. Every state write happens AFTER an await (same convention as
  // FailoverTimelinePanel / UniversalResourcePanel): no setState synchronously
  // inside an effect body, so this cannot cascade renders.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const page = await listFailoverPolicies();
        if (cancelled) return;
        setPolicies(page.policies ?? []);
        setLoadError(null);
      } catch (err) {
        if (cancelled) return;
        setLoadError(toApiFailure(err));
        setPolicies([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Scope targets for the pickers. Best effort: a failure is shown as a hint on
   * the target field, never as a blocked form - the backend validates scope_id
   * regardless, and the operator is still told exactly why it refused.
   */
  const loadTargets = useCallback(async () => {
    setTargetsError(null);
    const [siteResult, linkResult] = await Promise.allSettled([
      listScopeSites(),
      listScopeLinks(),
    ]);
    if (siteResult.status === "fulfilled") {
      setSites(siteResult.value);
    }
    if (linkResult.status === "fulfilled") {
      setLinks(linkResult.value);
    }
    if (siteResult.status === "rejected" || linkResult.status === "rejected") {
      setTargetsError(
        "Could not load scope targets. The backend rejects an unknown target id, and will say so.",
      );
    }
  }, []);

  // Scope targets load once on mount, same after-await convention.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [siteResult, linkResult] = await Promise.allSettled([
        listScopeSites(),
        listScopeLinks(),
      ]);
      if (cancelled) return;
      if (siteResult.status === "fulfilled") setSites(siteResult.value);
      if (linkResult.status === "fulfilled") setLinks(linkResult.value);
      setTargetsError(
        siteResult.status === "rejected" || linkResult.status === "rejected"
          ? "Could not load scope targets. The backend rejects an unknown target id, and will say so."
          : null,
      );
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openCreate = useCallback(() => {
    setNotice(null);
    setFormError(null);
    setLocalError(null);
    setConfirmId(null);
    setEditingId(null);
    setDetailId(null);
    setFormMode("create");
    // With a site context, default to a site-scoped policy for that site - the
    // most specific choice an operator normally wants.
    setDraft(
      siteId
        ? { ...EMPTY_DRAFT, scope: "site", scopeId: siteId }
        : { ...EMPTY_DRAFT },
    );
    if (!sites.length && !links.length) void loadTargets();
  }, [siteId, sites.length, links.length, loadTargets]);

  const openEdit = useCallback((policy: FailoverPolicyOut) => {
    setNotice(null);
    setFormError(null);
    setLocalError(null);
    setConfirmId(null);
    setEditingId(policy.id);
    setFormMode("edit");
    setDraft(draftFrom(policy));
  }, []);

  const closeForm = useCallback(() => {
    setFormMode("closed");
    setEditingId(null);
    setLocalError(null);
    setFormError(null);
  }, []);


  const submit = useCallback(async () => {
    // Basic usability only. Domain rules (ordering, bounds, duplicates) belong
    // to the backend, and its answer is displayed unchanged.
    const problems = [
      draft.name.trim() ? null : "Name is required.",
      draft.scope !== "all_wan" && !draft.scopeId
        ? `Select the ${draft.scope === "site" ? "site" : "link"} this policy applies to.`
        : null,
      thresholdProblem("Warning level", draft.warning),
      thresholdProblem("Critical level", draft.critical),
      thresholdProblem("Clear level", draft.clear),
      draft.samples.trim()
        ? null
        : "Consecutive samples is required (allowed 1-100).",
      draft.samples.trim() && !Number.isInteger(Number(draft.samples))
        ? "Consecutive samples must be a whole number (allowed 1-100)."
        : null,
    ].filter((problem): problem is string => Boolean(problem));

    if (problems.length) {
      setLocalError(problems.join(" "));
      setFormError(null);
      return;
    }

    const payload: FailoverPolicyInput = {
      name: draft.name.trim(),
      metric: draft.metric,
      scope: draft.scope,
      scope_id: draft.scope === "all_wan" ? null : draft.scopeId,
      description: draft.description.trim() === "" ? null : draft.description.trim(),
      warning_threshold: numberOrNull(draft.warning),
      critical_threshold: numberOrNull(draft.critical),
      clear_threshold: numberOrNull(draft.clear),
      min_consecutive_samples: Number(draft.samples),
      is_active: draft.isActive,
    };

    setBusy(true);
    setLocalError(null);
    setFormError(null);
    try {
      if (formMode === "create") {
        const created = await createFailoverPolicy(payload);
        setNotice(
          `Policy "${created.name}" created and is ${
            created.is_active ? "active" : "inactive"
          } (scope ${created.scope}).`,
        );
      } else if (editingId) {
        const updated = await updateFailoverPolicy(editingId, payload);
        setNotice(
          `Policy "${updated.name}" saved and is ${
            updated.is_active ? "active" : "inactive"
          }.`,
        );
      }
      closeForm();
      await load();
    } catch (error) {
      setFormError(toApiFailure(error));
    } finally {
      setBusy(false);
    }
  }, [draft, formMode, editingId, closeForm, load]);

  const openDetail = useCallback(async (policy: FailoverPolicyOut) => {
    setNotice(null);
    setConfirmId(null);
    setDetailError(null);
    setDetailId(policy.id);
    try {
      // Re-read so the detail view is never a stale list projection.
      const fresh = await getFailoverPolicy(policy.id);
      setPolicies((rows) =>
        rows.map((row) => (row.id === fresh.id ? fresh : row)),
      );
    } catch (error) {
      setDetailError(toApiFailure(error));
    }
  }, []);

  /** Enable/disable through PATCH - the policy stays listed either way. */
  const setActive = useCallback(
    async (policy: FailoverPolicyOut, isActive: boolean) => {
      setNotice(null);
      setBusy(true);
      try {
        const updated = await updateFailoverPolicy(policy.id, {
          is_active: isActive,
        });
        setNotice(
          `Policy "${updated.name}" is now ${
            updated.is_active ? "active (evaluated)" : "inactive (not evaluated)"
          }. It is still listed.`,
        );
        await load();
      } catch (error) {
        setFormError(toApiFailure(error));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const confirmRemove = useCallback(async () => {
    const policy = policies.find((row) => row.id === confirmId);
    if (!policy) {
      setConfirmId(null);
      return;
    }
    setBusy(true);
    setDeleteError(null);
    try {
      await deleteFailoverPolicy(policy.id);
      setNotice(
        `Policy "${policy.name}" removed from detection and from this list. It was NOT deleted from the database (soft delete) - the record is retained.`,
      );
      if (detailId === policy.id) setDetailId(null);
      setConfirmId(null);
      await load();
    } catch (error) {
      setDeleteError(toApiFailure(error));
    } finally {
      setBusy(false);
    }
  }, [confirmId, policies, detailId, load]);

  const detailPolicy = useMemo(
    () => policies.find((policy) => policy.id === detailId) ?? null,
    [policies, detailId],
  );
  const confirmPolicy = useMemo(
    () => policies.find((policy) => policy.id === confirmId) ?? null,
    [policies, confirmId],
  );



  return (
    <div style={cardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <div style={{ fontWeight: 600 }}>
          OPERATIONAL SLA POLICIES
          <span style={{ fontWeight: 400, opacity: 0.7 }}>
            {" "}
            - thresholds the detector evaluates ({policies.length})
          </span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            style={buttonStyle}
            onClick={() => void load()}
            disabled={loading}
          >
            Refresh
          </button>
          <button type="button" style={buttonStyle} onClick={openCreate}>
            New policy
          </button>
        </div>
      </div>
      <p style={{ fontSize: 12, opacity: 0.7, margin: "6px 0 10px" }}>
        Operational thresholds for WAN links - separate from the business SLA
        contracts. The backend validates every change, so a refused policy is
        never saved.
      </p>

      {notice ? <Notice>{notice}</Notice> : null}
      {loadError ? (
        <div>
          <ErrorBox status={loadError.status} message={loadError.message} />
          <button type="button" style={buttonStyle} onClick={() => void load()}>
            Retry
          </button>
        </div>
      ) : null}
      {detailError ? (
        <ErrorBox status={detailError.status} message={detailError.message} />
      ) : null}

      {loading ? (
        <div style={{ fontSize: 12, opacity: 0.7 }}>
          Loading operational SLA policies...
        </div>
      ) : null}

      {!loading && !loadError && !policies.length ? (
        <div style={{ fontSize: 12, opacity: 0.8 }}>
          No operational SLA policies yet. WAN links are still observed, but with
          no policy nothing is classified as breaching.{" "}
          <button type="button" style={buttonStyle} onClick={openCreate}>
            Create the first policy
          </button>
        </div>
      ) : null}

      {policies.length ? (
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Scope</th>
                <th style={thStyle}>Metric</th>
                <th style={thStyle}>Warn</th>
                <th style={thStyle}>Crit</th>
                <th style={thStyle}>Clear</th>
                <th style={thStyle}>Samples</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>Updated</th>
                <th style={thStyle}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((policy) => (
                <tr key={policy.id}>
                  <td style={tdStyle}>
                    {policy.name}
                    {policy.description ? (
                      <div style={{ opacity: 0.6, fontSize: 11 }}>
                        {policy.description}
                      </div>
                    ) : null}
                  </td>
                  <td style={tdStyle}>
                    <ScopeBadge
                      scope={policy.scope}
                      scopeId={policy.scope_id}
                      targetName={scopeTargetName(policy, sites, links)}
                    />
                  </td>
                  <td style={tdStyle}>{policy.metric}</td>
                  <td style={tdStyle}>{level(policy.warning_threshold)}</td>
                  <td style={tdStyle}>{level(policy.critical_threshold)}</td>
                  <td style={tdStyle}>{level(policy.clear_threshold)}</td>
                  <td style={tdStyle}>{policy.min_consecutive_samples}</td>
                  <td style={tdStyle}>
                    <StateBadge active={policy.is_active} />
                  </td>
                  <td style={tdStyle}>{when(policy.updated_at)}</td>
                  <td style={tdStyle}>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        style={buttonStyle}
                        onClick={() => void openDetail(policy)}
                      >
                        View
                      </button>
                      <button
                        type="button"
                        style={buttonStyle}
                        onClick={() => openEdit(policy)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        style={buttonStyle}
                        disabled={busy}
                        onClick={() => void setActive(policy, !policy.is_active)}
                      >
                        {policy.is_active ? "Disable" : "Enable"}
                      </button>
                      <button
                        type="button"
                        style={dangerButtonStyle}
                        disabled={busy}
                        onClick={() => {
                          setDeleteError(null);
                          setConfirmId(policy.id);
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {detailPolicy && formMode === "closed" ? (
        <PolicyDetail
          policy={detailPolicy}
          sites={sites}
          links={links}
          onEdit={() => openEdit(detailPolicy)}
          onClose={() => setDetailId(null)}
        />
      ) : null}

      {confirmPolicy ? (
        <ConfirmRemove
          policy={confirmPolicy}
          busy={busy}
          error={deleteError}
          onConfirm={() => void confirmRemove()}
          onCancel={() => {
            setDeleteError(null);
            setConfirmId(null);
          }}
        />
      ) : null}

      {formMode !== "closed" ? (
        <PolicyForm
          mode={formMode}
          draft={draft}
          setDraft={setDraft}
          sites={sites}
          links={links}
          targetsError={targetsError}
          localError={localError}
          remoteError={formError}
          busy={busy}
          onSubmit={() => void submit()}
          onCancel={closeForm}
        />
      ) : null}
    </div>
  );
}

