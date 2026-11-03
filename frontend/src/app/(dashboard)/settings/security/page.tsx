"use client";

/**
 * /settings/security — the tenant security policy and the signed-in user's
 * sessions (enterprise security programme S1).
 *
 * Every policy value on this page is enforced by the backend
 * (services/security_policy.py): password rules wherever a password is set,
 * the lockout threshold at sign-in, the idle timeout when a session refreshes.
 * Changing it needs security.manage and is recorded in the audit log.
 * "Require two-step verification" makes users without MFA set it up before
 * they can do anything else (S2); the Two-step verification panel is where
 * each user sets up their own.
 */

import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { History, LaptopMinimal, LogOut, ShieldCheck, Smartphone } from "lucide-react";
import MfaPanel from "@/components/settings/MfaPanel";
import { Banner, PageShell, Panel, Pill, ago } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import { getSecuritySettings, updateSecuritySettings, type SecuritySettings, type SecuritySettingsUpdate } from "@/lib/api";
import {
  describeDevice,
  endMyOtherSessions,
  endMySession,
  listMySessions,
  signOutEverywhere,
  type AuthSession,
} from "@/lib/security";

const TIMEOUTS = [
  { value: 15, label: "15 minutes" },
  { value: 30, label: "30 minutes" },
  { value: 60, label: "1 hour" },
  { value: 240, label: "4 hours" },
  { value: 480, label: "8 hours (a working day)" },
  { value: 1440, label: "24 hours" },
  { value: 10080, label: "7 days" },
  { value: 43200, label: "30 days" },
];

type Policy = Required<SecuritySettingsUpdate>;

function toPolicy(s: SecuritySettings): Policy {
  return {
    min_password_length: s.min_password_length,
    require_uppercase: s.require_uppercase,
    require_numbers: s.require_numbers,
    require_special_chars: s.require_special_chars,
    session_timeout_minutes: s.session_timeout_minutes,
    max_login_attempts: s.max_login_attempts,
    mfa_enabled: s.mfa_enabled,
  };
}

function PolicyPanel({ canManage }: { canManage: boolean }) {
  const { tenantId } = useAuth();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings", "security"], queryFn: () => getSecuritySettings(tenantId) });
  // Unsaved edits on top of the saved policy.
  const [edits, setEdits] = useState<Partial<Policy>>({});
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  const save = useMutation({
    mutationFn: (body: Policy) => updateSecuritySettings(tenantId, body),
    onSuccess: (s) => {
      qc.setQueryData(["settings", "security"], s);
      setEdits({});
      setMessage({ tone: "good", text: "Security policy saved. It applies from the next sign-in, password change or session refresh." });
    },
    onError: (e) => setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The policy could not be saved." }),
  });

  const saved = settings.data ? toPolicy(settings.data) : null;
  const draft: Policy | null = saved ? { ...saved, ...edits } : null;
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved);
  const set = <K extends keyof Policy>(k: K, v: Policy[K]) => setEdits((e) => ({ ...e, [k]: v }));
  const timeoutChoices = draft && !TIMEOUTS.some((t) => t.value === draft.session_timeout_minutes)
    ? [...TIMEOUTS, { value: draft.session_timeout_minutes, label: `${draft.session_timeout_minutes} minutes` }]
    : TIMEOUTS;

  return (
    <Panel
      title="Security policy"
      info="Enforced by the platform for every user in your organization."
      state={draft ? "ready" : settings.isError ? "error" : "loading"}
      onRetry={() => settings.refetch()}
      skeletonHeight={320}
      aside={settings.data && (settings.data.is_default ? <Pill tone="neutral">Platform defaults</Pill> : <Pill tone="good">Custom policy</Pill>)}
    >
      {draft && (
        <form
          className="sec-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty) save.mutate(draft);
          }}
        >
          {message && <Banner tone={message.tone}>{message.text}</Banner>}
          {!canManage && <Banner tone="info">You can view this policy. Changing it needs the security.manage permission (Owner or Admin).</Banner>}
          <fieldset disabled={!canManage || save.isPending} className="sec-fieldset">
            <section className="sec-group">
              <h3>Passwords</h3>
              <p className="sec-help">Applies when a password is created or changed: sign-up, invitations, resets and admin changes. Existing passwords keep working.</p>
              <label className="sec-row">
                <span>
                  <b>Minimum length</b>
                  <small>8 to 64 characters. Length matters more than character rules.</small>
                </span>
                <input className="pk-input sec-num" type="number" min={8} max={64} value={draft.min_password_length}
                  onChange={(e) => set("min_password_length", Number(e.target.value))} />
              </label>
              <label className="sec-row">
                <span><b>Require an upper-case letter</b></span>
                <input type="checkbox" className="sec-check" checked={draft.require_uppercase} onChange={(e) => set("require_uppercase", e.target.checked)} />
              </label>
              <label className="sec-row">
                <span><b>Require a number</b></span>
                <input type="checkbox" className="sec-check" checked={draft.require_numbers} onChange={(e) => set("require_numbers", e.target.checked)} />
              </label>
              <label className="sec-row">
                <span><b>Require a symbol</b></span>
                <input type="checkbox" className="sec-check" checked={draft.require_special_chars} onChange={(e) => set("require_special_chars", e.target.checked)} />
              </label>
            </section>

            <section className="sec-group">
              <h3>Sign-in protection</h3>
              <label className="sec-row">
                <span>
                  <b>Lock an account after</b>
                  <small>Failed sign-ins in a row (3 to 20). A locked account opens again after 15 minutes or an admin password reset.</small>
                </span>
                <span className="sec-inline">
                  <input className="pk-input sec-num" type="number" min={3} max={20} value={draft.max_login_attempts}
                    onChange={(e) => set("max_login_attempts", Number(e.target.value))} />
                  attempts
                </span>
              </label>
              <label className="sec-row">
                <span>
                  <b>End idle sessions after</b>
                  <small>A session not used for this long must sign in again.</small>
                </span>
                <select className="pk-input sec-select" value={draft.session_timeout_minutes}
                  onChange={(e) => set("session_timeout_minutes", Number(e.target.value))}>
                  {timeoutChoices.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <label className="sec-row">
                <span>
                  <b>Require two-step verification</b>
                  <small>Everyone signs in with an authenticator-app code. Users who haven&apos;t set it up must do so before anything else at their next sign-in.</small>
                </span>
                <input type="checkbox" className="sec-check" checked={draft.mfa_enabled} onChange={(e) => set("mfa_enabled", e.target.checked)} />
              </label>
            </section>
          </fieldset>
          {canManage && (
            <div className="sec-actions">
              <button type="button" className="pk-button" disabled={!dirty || save.isPending} onClick={() => setEdits({})}>Discard</button>
              <button type="submit" className="pk-button pk-button--primary" disabled={!dirty || save.isPending}>
                {save.isPending ? "Saving…" : "Save policy"}
              </button>
            </div>
          )}
        </form>
      )}
    </Panel>
  );
}

function SessionsPanel() {
  const qc = useQueryClient();
  const { logout } = useAuth();
  const sessions = useQuery({ queryKey: ["auth", "sessions"], queryFn: listMySessions, refetchInterval: 60_000 });
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [showAll, setShowAll] = useState(false);
  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: ["auth", "sessions"] }), [qc]);
  const fail = (e: unknown) => setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The request failed." });

  const endOne = useMutation({
    mutationFn: (s: AuthSession) => endMySession(s.session_id),
    onSuccess: () => { setMessage({ tone: "good", text: "That session was signed out." }); refresh(); },
    onError: fail,
  });
  const endOthers = useMutation({
    mutationFn: endMyOtherSessions,
    onSuccess: (r) => { setMessage({ tone: "good", text: `Signed out of ${r.sessions_ended} other session${r.sessions_ended === 1 ? "" : "s"}.` }); refresh(); },
    onError: fail,
  });
  const everywhere = useMutation({
    mutationFn: signOutEverywhere,
    onSuccess: () => logout(),
    onError: fail,
  });
  const busy = endOne.isPending || endOthers.isPending || everywhere.isPending;
  const list = sessions.data ?? [];
  const others = list.filter((s) => !s.current).length;
  const SHOWN = 6;
  const visible = showAll ? list : list.slice(0, SHOWN);

  return (
    <Panel
      title="Your sessions"
      info="Browsers and devices signed in to your account. Sign out any you don't recognise."
      state={sessions.data ? "ready" : sessions.isError ? "error" : "loading"}
      onRetry={() => sessions.refetch()}
      skeletonHeight={200}
      aside={sessions.data ? `${list.length} active` : undefined}
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      <ul className="sec-sessions">
        {visible.map((s) => {
          // Sessions started before device details were recorded have none.
          const device = describeDevice(s.user_agent) ?? (s.ip_address ? "Unknown browser" : "Earlier sign-in");
          const Icon = /Android|iOS/.test(device) ? Smartphone : LaptopMinimal;
          return (
            <li key={s.session_id}>
              <span className="sec-sessions__icon" aria-hidden="true"><Icon size={18} /></span>
              <span className="sec-sessions__main">
                <b>
                  {device}
                  {s.current && <Pill tone="good">This browser</Pill>}
                </b>
                <small>
                  {s.ip_address ? `${s.ip_address} · ` : ""}signed in {ago(s.started_at)} · last active {ago(s.last_seen_at)}
                </small>
              </span>
              {!s.current && (
                <button type="button" className="pk-button" disabled={busy} onClick={() => endOne.mutate(s)}>
                  <LogOut size={14} aria-hidden="true" /> Sign out
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {list.length > SHOWN && (
        <button type="button" className="ecc-link sec-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${list.length} sessions`}
        </button>
      )}
      <div className="sec-actions">
        <button type="button" className="pk-button" disabled={busy || others === 0} onClick={() => endOthers.mutate()}>
          Sign out of other sessions
        </button>
        <button
          type="button"
          className="pk-button sec-danger"
          disabled={busy}
          onClick={() => {
            if (window.confirm("Sign out of every session, including this one?")) everywhere.mutate();
          }}
        >
          Sign out everywhere
        </button>
      </div>
    </Panel>
  );
}

export default function SecuritySettingsPage() {
  const { user } = useAuth();
  const has = (p: string) => !!user && (user.is_superuser || user.permissions.includes(p));
  return (
    <PageShell
      title="Security"
      icon={ShieldCheck}
      subtitle="Your organization's security policy and the places you're signed in."
      crumbs={[{ label: "Settings", href: "/settings" }, { label: "Security" }]}
      actions={has("audit.read") ? (
        <Link href="/settings/audit-log" className="pk-button"><History size={14} aria-hidden="true" /> Audit log</Link>
      ) : undefined}
    >
      <div className="sec-grid">
        <PolicyPanel canManage={has("security.manage")} />
        <div className="sec-stack">
          <MfaPanel />
          <SessionsPanel />
        </div>
      </div>
    </PageShell>
  );
}
