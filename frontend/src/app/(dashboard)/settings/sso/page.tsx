"use client";

/**
 * /settings/sso — enterprise identity (security programme S6): verified email
 * domains, the organization's OIDC identity provider (Entra ID, Okta, Google
 * Workspace …), "require SSO", just-in-time accounts, and SCIM provisioning.
 * Needs security.manage; every change is recorded in the audit log.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Fingerprint, Globe, KeyRound, Plus, ShieldCheck, Trash2, Users } from "lucide-react";
import { Banner, PageShell, Panel, Pill, ago } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import { listRoles } from "@/lib/api";
import {
  addDomain,
  createScimToken,
  getIdentity,
  removeDomain,
  revokeScimToken,
  updateSso,
  verifyDomain,
  type Identity,
  type SsoUpdate,
} from "@/lib/identity";

type Msg = { tone: "good" | "bad"; text: string } | null;
const KEY = ["settings", "identity"];

function CopyText({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="sso-copy">
      <code>{value}</code>
      <button type="button" className="pk-button" style={{ height: 24 }} aria-label="Copy"
        onClick={() => { void navigator.clipboard?.writeText(value); setCopied(true); }}>
        <Copy size={12} aria-hidden="true" /> {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

function DomainsPanel({ data }: { data: Identity }) {
  const qc = useQueryClient();
  const [domain, setDomain] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const fail = (e: unknown) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "The request failed." });
  const add = useMutation({ mutationFn: () => addDomain(domain), onSuccess: () => { setDomain(""); setMsg(null); refresh(); }, onError: fail });
  const verify = useMutation({ mutationFn: verifyDomain, onSuccess: (d) => { setMsg({ tone: "good", text: `${d.domain} is verified.` }); refresh(); }, onError: fail });
  const remove = useMutation({ mutationFn: removeDomain, onSuccess: () => refresh(), onError: fail });
  const busy = add.isPending || verify.isPending || remove.isPending;
  return (
    <Panel title="Email domains" info="People whose email is on a verified domain are sent to your identity provider; a domain can be verified by one organization only." state="ready"
      aside={<Pill tone={data.domains.some((d) => d.verified) ? "good" : "neutral"}>{data.domains.filter((d) => d.verified).length} verified</Pill>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      {data.domains.length > 0 && (
        <ul className="sec-sessions">
          {data.domains.map((d) => (
            <li key={d.id} className="sso-domain">
              <span className="sec-sessions__icon" aria-hidden="true"><Globe size={16} /></span>
              <span className="sec-sessions__main">
                <b>{d.domain} {d.verified ? <Pill tone="good">Verified</Pill> : <Pill tone="warn">Waiting for DNS</Pill>}</b>
                {d.verified ? (
                  <small>verified {ago(d.verified_at)}</small>
                ) : (
                  <span className="sso-txt">
                    <small>Add this TXT record at your DNS provider, then verify:</small>
                    <CopyText value={d.txt_name} />
                    <CopyText value={d.txt_value} />
                  </span>
                )}
              </span>
              <span style={{ display: "inline-flex", gap: 6 }}>
                {!d.verified && <button type="button" className="pk-button" disabled={busy} onClick={() => verify.mutate(d.id)}>Verify</button>}
                <button type="button" className="pk-button" disabled={busy} aria-label={`Remove ${d.domain}`}
                  onClick={() => { if (window.confirm(`Remove ${d.domain}?`)) remove.mutate(d.id); }}><Trash2 size={14} aria-hidden="true" /></button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form className="net-form" onSubmit={(e: FormEvent) => { e.preventDefault(); if (domain.trim()) add.mutate(); }}>
        <label className="ecc-sr-only" htmlFor="sso-domain">Domain</label>
        <input id="sso-domain" className="pk-input" placeholder="example.com" value={domain} onChange={(e) => setDomain(e.target.value)} maxLength={253} disabled={busy} />
        <button type="submit" className="pk-button pk-button--primary" disabled={busy || !domain.trim()}><Plus size={14} aria-hidden="true" /> Add domain</button>
      </form>
    </Panel>
  );
}

function ProviderPanel({ data }: { data: Identity }) {
  const qc = useQueryClient();
  const { tenantId } = useAuth();
  const roles = useQuery({ queryKey: ["auth", "roles"], queryFn: () => listRoles(tenantId) });
  const s = data.sso;
  const [issuer, setIssuer] = useState(s.issuer ?? "");
  const [clientId, setClientId] = useState(s.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const save = useMutation({
    mutationFn: (body: SsoUpdate) => updateSso(body),
    onSuccess: (d) => { qc.setQueryData(KEY, d); setSecret(""); setMsg({ tone: "good", text: "Saved." }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Could not save." }),
  });
  const hasVerified = data.domains.some((d) => d.verified);
  return (
    <Panel title="Identity provider (OIDC)" info="Works with Microsoft Entra ID, Okta, Google Workspace, Auth0, Ping and any OpenID Connect provider." state="ready"
      aside={s.enabled ? <Pill tone="good">SSO on</Pill> : <Pill tone="neutral">SSO off</Pill>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <div className="sso-form">
        <span className="sec-help" style={{ margin: 0 }}>Register this redirect URI in your identity provider:</span>
        <CopyText value={s.redirect_uri} />
        <label className="sso-field"><span>Issuer URL</span>
          <input className="pk-input" placeholder="https://login.microsoftonline.com/<tenant-id>/v2.0" value={issuer} onChange={(e) => setIssuer(e.target.value)} /></label>
        <label className="sso-field"><span>Client ID</span>
          <input className="pk-input" value={clientId} onChange={(e) => setClientId(e.target.value)} /></label>
        <label className="sso-field"><span>Client secret {s.client_secret_set && <Pill tone="good">Stored</Pill>}</span>
          <input className="pk-input" type="password" autoComplete="new-password" placeholder={s.client_secret_set ? "Leave blank to keep the stored secret" : "From your identity provider"}
            value={secret} onChange={(e) => setSecret(e.target.value)} /></label>
        <div className="sec-actions" style={{ justifyContent: "flex-start" }}>
          <button type="button" className="pk-button pk-button--primary" disabled={save.isPending}
            onClick={() => save.mutate({ issuer: issuer.trim() || null, client_id: clientId.trim() || null, ...(secret ? { client_secret: secret } : {}) })}>
            Save provider
          </button>
        </div>
      </div>
      <fieldset className="sec-fieldset" disabled={save.isPending} style={{ marginTop: 14 }}>
        <label className="sec-row">
          <span><b>Turn on single sign-on</b><small>{hasVerified ? "People on your verified domains can sign in with your identity provider." : "Verify an email domain first."}</small></span>
          <input type="checkbox" className="sec-check" checked={s.enabled} disabled={!hasVerified || !s.issuer || !s.client_id}
            onChange={(e) => save.mutate({ enabled: e.target.checked, ...(e.target.checked ? {} : { require_sso: false }) })} />
        </label>
        <label className="sec-row">
          <span><b>Require single sign-on</b><small>Password sign-in is refused for your verified domains. Owners keep password access as break-glass accounts.</small></span>
          <input type="checkbox" className="sec-check" checked={s.require_sso} disabled={!s.enabled} onChange={(e) => save.mutate({ require_sso: e.target.checked })} />
        </label>
        <label className="sec-row">
          <span><b>Create accounts on first sign-in</b><small>Otherwise people need an account (invited, or provisioned by SCIM) before they can use SSO.</small></span>
          <input type="checkbox" className="sec-check" checked={s.jit_provisioning} onChange={(e) => save.mutate({ jit_provisioning: e.target.checked })} />
        </label>
        <label className="sec-row">
          <span><b>Role for new accounts</b><small>Used for accounts created by SSO or SCIM.</small></span>
          <select className="pk-input sec-select" value={s.default_role_id ?? ""} onChange={(e) => save.mutate({ default_role_id: e.target.value || null })}>
            <option value="">No role (assign later)</option>
            {(roles.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
      </fieldset>
    </Panel>
  );
}

function ScimPanel({ data }: { data: Identity }) {
  const qc = useQueryClient();
  const [token, setToken] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const create = useMutation({ mutationFn: createScimToken, onSuccess: (r) => { setToken(r.token); qc.invalidateQueries({ queryKey: KEY }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Failed." }) });
  const revoke = useMutation({ mutationFn: revokeScimToken, onSuccess: () => { setToken(null); qc.invalidateQueries({ queryKey: KEY }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Failed." }) });
  const s = data.sso;
  return (
    <Panel title="User provisioning (SCIM 2.0)" info="Your identity provider creates, updates, deactivates and removes users automatically." state="ready"
      aside={s.scim_enabled ? <Pill tone="good">Connected</Pill> : <Pill tone="neutral">Off</Pill>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <span className="sec-help" style={{ margin: 0 }}>SCIM base URL</span>
      <CopyText value={s.scim_base_url} />
      {token && (
        <>
          <Banner tone="warn">Copy this token now and paste it into your identity provider. It is shown only once.</Banner>
          <CopyText value={token} />
        </>
      )}
      <p className="sec-help">
        {s.scim_enabled ? <>Token <code>{s.scim_token_prefix}…</code> is active. Deactivated users are signed out at once.</> : "Create a token to connect your identity provider."}
        {" "}Groups are not provisioned yet; assign roles here.
      </p>
      <div className="sec-actions" style={{ justifyContent: "flex-start" }}>
        <button type="button" className="pk-button pk-button--primary" disabled={create.isPending}
          onClick={() => { if (!s.scim_enabled || window.confirm("Replace the current token? Your identity provider must be updated with the new one.")) create.mutate(); }}>
          <KeyRound size={14} aria-hidden="true" /> {s.scim_enabled ? "Replace token" : "Create token"}
        </button>
        {s.scim_enabled && (
          <button type="button" className="pk-button sec-danger" disabled={revoke.isPending}
            onClick={() => { if (window.confirm("Stop SCIM provisioning? Your identity provider will no longer be able to manage users.")) revoke.mutate(); }}>
            Revoke
          </button>
        )}
      </div>
    </Panel>
  );
}

export default function SsoSettingsPage() {
  const { user } = useAuth();
  const allowed = !!user && (user.is_superuser || user.permissions.includes("security.manage"));
  const identity = useQuery({ queryKey: KEY, queryFn: getIdentity, enabled: allowed });
  const data = identity.data;
  return (
    <PageShell title="Single sign-on" icon={Fingerprint}
      subtitle="Sign in with your identity provider, verify your email domains and provision users automatically."
      crumbs={[{ label: "Settings", href: "/settings" }, { label: "Single sign-on" }]}>
      {!allowed ? (
        <Banner tone="info">Single sign-on settings need the security.manage permission (Owner or Admin).</Banner>
      ) : !data ? (
        <Panel title="Single sign-on" state={identity.isError ? "error" : "loading"} onRetry={() => identity.refetch()} skeletonHeight={300} />
      ) : (
        <div className="sec-grid">
          <div className="sec-stack">
            <DomainsPanel data={data} />
            <ProviderPanel key={`${data.sso.issuer}-${data.sso.client_id}`} data={data} />
          </div>
          <div className="sec-stack">
            <ScimPanel data={data} />
            <Panel title="How it works" state="ready">
              <ol className="mfa-setup__steps">
                <li><ShieldCheck size={13} aria-hidden="true" /> Verify each email domain with a DNS TXT record.</li>
                <li><Fingerprint size={13} aria-hidden="true" /> Register the redirect URI with your identity provider and paste its issuer, client ID and secret here.</li>
                <li><Users size={13} aria-hidden="true" /> Turn SSO on. People enter their work email on the sign-in page and choose &quot;Sign in with SSO&quot;.</li>
                <li><KeyRound size={13} aria-hidden="true" /> Optional: require SSO, and connect SCIM so joiners and leavers are handled automatically.</li>
              </ol>
              <p className="sec-help" style={{ margin: 0 }}>SAML 2.0 is not available yet; every major identity provider also supports OpenID Connect.</p>
            </Panel>
          </div>
        </div>
      )}
    </PageShell>
  );
}
