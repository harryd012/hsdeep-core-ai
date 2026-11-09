"use client";

/**
 * /settings/sso — enterprise identity (security programme S6): verified email
 * domains, the organization's identity provider over OpenID Connect or SAML 2.0
 * (Entra ID, Okta, Google Workspace, ADFS …), "require SSO", just-in-time
 * accounts, and SCIM provisioning.
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
  updateSaml,
  updateSso,
  verifyDomain,
  type Identity,
  type SamlUpdate,
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

function SamlFields({ data, onSaved }: { data: Identity; onSaved: (m: Msg) => void }) {
  const qc = useQueryClient();
  const m = data.saml;
  const [mode, setMode] = useState<"metadata" | "manual">(m.idp_entity_id ? "manual" : "metadata");
  const [xml, setXml] = useState("");
  const [entityId, setEntityId] = useState(m.idp_entity_id ?? "");
  const [ssoUrl, setSsoUrl] = useState(m.idp_sso_url ?? "");
  const [certs, setCerts] = useState("");
  const [emailAttr, setEmailAttr] = useState(m.email_attribute ?? "");
  const [now] = useState(() => Date.now());
  const save = useMutation({
    mutationFn: (body: SamlUpdate) => updateSaml(body),
    onSuccess: (d) => {
      qc.setQueryData(KEY, d); setXml(""); setCerts("");
      setEntityId(d.saml.idp_entity_id ?? ""); setSsoUrl(d.saml.idp_sso_url ?? "");
      onSaved({ tone: "good", text: "SAML identity provider saved." });
    },
    onError: (e) => onSaved({ tone: "bad", text: e instanceof Error ? e.message : "Could not save." }),
  });
  const submit = () => {
    const body: SamlUpdate = { email_attribute: emailAttr.trim() };
    if (mode === "metadata") body.metadata_xml = xml;
    else {
      body.idp_entity_id = entityId.trim();
      body.idp_sso_url = ssoUrl.trim();
      if (certs.trim()) body.idp_certificates = certs;
    }
    save.mutate(body);
  };
  return (
    <div className="sso-form">
      <span className="sec-help" style={{ margin: 0 }}>Give these to your identity provider, or point it at our metadata URL:</span>
      <div className="sso-field"><span>Entity ID (audience)</span><CopyText value={m.sp_entity_id} /></div>
      <div className="sso-field"><span>Assertion consumer service (ACS) URL</span><CopyText value={m.acs_url} /></div>
      <div className="sso-field"><span>Service provider metadata</span><CopyText value={m.metadata_url} /></div>
      <div className="sso-seg" role="radiogroup" aria-label="How to set the identity provider">
        <button type="button" role="radio" aria-checked={mode === "metadata"} className={`pk-button${mode === "metadata" ? " pk-button--primary" : ""}`} onClick={() => setMode("metadata")}>Paste metadata XML</button>
        <button type="button" role="radio" aria-checked={mode === "manual"} className={`pk-button${mode === "manual" ? " pk-button--primary" : ""}`} onClick={() => setMode("manual")}>Enter fields</button>
      </div>
      {mode === "metadata" ? (
        <label className="sso-field"><span>Identity provider metadata</span>
          <textarea className="pk-input sso-textarea" rows={6} spellCheck={false} placeholder="<EntityDescriptor …> from Entra ID, Okta, ADFS or Google Workspace"
            value={xml} onChange={(e) => setXml(e.target.value)} /></label>
      ) : (
        <>
          <label className="sso-field"><span>Identity provider entity ID (issuer)</span>
            <input className="pk-input" value={entityId} onChange={(e) => setEntityId(e.target.value)} /></label>
          <label className="sso-field"><span>Sign-in URL (HTTP-Redirect binding)</span>
            <input className="pk-input" placeholder="https://" value={ssoUrl} onChange={(e) => setSsoUrl(e.target.value)} /></label>
          <label className="sso-field"><span>Signing certificate(s), PEM</span>
            <textarea className="pk-input sso-textarea" rows={4} spellCheck={false}
              placeholder={m.certificates.length ? "Leave blank to keep the stored certificates" : "-----BEGIN CERTIFICATE-----"}
              value={certs} onChange={(e) => setCerts(e.target.value)} /></label>
        </>
      )}
      <label className="sso-field"><span>Email attribute (optional)</span>
        <input className="pk-input" placeholder="Defaults to the NameID, then the usual email attributes" value={emailAttr} onChange={(e) => setEmailAttr(e.target.value)} /></label>
      {m.certificates.length > 0 && (
        <ul className="sso-certs">
          {m.certificates.map((c) => {
            const soon = new Date(c.expires).getTime() - now < 30 * 86_400_000;
            return (
              <li key={c.subject + c.expires}>
                <KeyRound size={13} aria-hidden="true" /> <code>{c.subject}</code>
                <Pill tone={soon ? "warn" : "good"}>expires {new Date(c.expires).toLocaleDateString()}</Pill>
              </li>
            );
          })}
        </ul>
      )}
      <div className="sec-actions" style={{ justifyContent: "flex-start" }}>
        <button type="button" className="pk-button pk-button--primary" disabled={save.isPending || (mode === "metadata" && !xml.trim())} onClick={submit}>
          Save SAML provider
        </button>
      </div>
    </div>
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
  const isSaml = s.protocol === "saml";
  const samlReady = !!(data.saml.idp_entity_id && data.saml.idp_sso_url && data.saml.certificates.length);
  const ready = isSaml ? samlReady : !!(s.issuer && s.client_id);
  return (
    <Panel title="Identity provider" info="OpenID Connect or SAML 2.0: Microsoft Entra ID, Okta, Google Workspace, ADFS, Auth0, Ping and others." state="ready"
      aside={s.enabled ? <Pill tone="good">SSO on · {isSaml ? "SAML" : "OIDC"}</Pill> : <Pill tone="neutral">SSO off</Pill>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <div className="sso-seg" role="radiogroup" aria-label="Protocol">
        {(["oidc", "saml"] as const).map((p) => (
          <button key={p} type="button" role="radio" aria-checked={s.protocol === p} disabled={save.isPending}
            className={`pk-button${s.protocol === p ? " pk-button--primary" : ""}`}
            onClick={() => { if (s.protocol !== p) save.mutate({ protocol: p }); }}>
            {p === "oidc" ? "OpenID Connect" : "SAML 2.0"}
          </button>
        ))}
      </div>
      {isSaml ? <SamlFields data={data} onSaved={setMsg} /> : (
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
      )}
      <fieldset className="sec-fieldset" disabled={save.isPending} style={{ marginTop: 14 }}>
        <label className="sec-row">
          <span><b>Turn on single sign-on</b><small>{!hasVerified ? "Verify an email domain first." : !ready ? "Set up the identity provider first." : "People on your verified domains can sign in with your identity provider."}</small></span>
          <input type="checkbox" className="sec-check" checked={s.enabled} disabled={!s.enabled && (!hasVerified || !ready)}
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
            <ProviderPanel key={`${data.sso.protocol}-${data.sso.issuer}-${data.sso.client_id}`} data={data} />
          </div>
          <div className="sec-stack">
            <ScimPanel data={data} />
            <Panel title="How it works" state="ready">
              <ol className="mfa-setup__steps">
                <li><ShieldCheck size={13} aria-hidden="true" /> Verify each email domain with a DNS TXT record.</li>
                <li><Fingerprint size={13} aria-hidden="true" /> OIDC: register the redirect URI and paste the issuer, client ID and secret. SAML: give your provider our entity ID and ACS URL (or metadata URL), then paste its metadata XML.</li>
                <li><Users size={13} aria-hidden="true" /> Turn SSO on. People enter their work email on the sign-in page and choose &quot;Sign in with SSO&quot;.</li>
                <li><KeyRound size={13} aria-hidden="true" /> Optional: require SSO, and connect SCIM so joiners and leavers are handled automatically.</li>
              </ol>
              <p className="sec-help" style={{ margin: 0 }}>SAML sign-in starts from our sign-in page (service-provider initiated). Responses must be signed; encrypted assertions are not supported yet.</p>
            </Panel>
          </div>
        </div>
      )}
    </PageShell>
  );
}
