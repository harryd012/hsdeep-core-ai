"use client";

/**
 * Notification policies panel (observability plan P9 slice 1), on the
 * Notifications settings page. Without enabled policies every enabled channel
 * gets every incident at its minimum severity, as before; with policies, each
 * incident goes only where a matching policy sends it.
 */
import { useEffect, useState } from "react";

import { listAssetSites, type AssetSite } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  createPolicy,
  deletePolicy,
  listPolicies,
  testPolicy,
  updatePolicy,
  type NotificationPolicy,
  type PolicyChannel,
  type PolicyInput,
  type PolicySeverity,
} from "@/lib/notificationPolicies";
import { Banner, Panel, Pill } from "@/components/ui/PageKit";

const CHANNELS: { key: PolicyChannel; label: string }[] = [
  { key: "email", label: "Email" },
  { key: "slack", label: "Slack" },
  { key: "pagerduty", label: "PagerDuty" },
];
const PRIORITIES = ["P1", "P2", "P3", "P4"];

function Editor({ initial, sites, onSave, onCancel, busy }: {
  initial: NotificationPolicy | null;
  sites: AssetSite[];
  onSave: (body: PolicyInput) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [minSeverity, setMinSeverity] = useState<PolicySeverity>(initial?.min_severity ?? "warning");
  const [priorities, setPriorities] = useState<string[]>(initial?.priorities ?? []);
  const [siteIds, setSiteIds] = useState<string[]>(initial?.site_ids ?? []);
  const [channels, setChannels] = useState<PolicyChannel[]>(initial?.channels ?? []);
  const [recipients, setRecipients] = useState(initial?.email_recipients ?? "");
  const [teams, setTeams] = useState(initial?.teams_webhook ?? "");
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const hasDestination = channels.length > 0 || !!teams.trim();

  return (
    <div className="pk-form" style={{ display: "grid", gap: 10, border: "1px solid var(--ecc-border)", borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
        <div className="pk-field"><label htmlFor="np-name">Name</label>
          <input id="np-name" className="pk-input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="e.g. Critical to on-call" /></div>
        <div className="pk-field"><label htmlFor="np-sev">Incidents at or above</label>
          <select id="np-sev" className="pk-input" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as PolicySeverity)}>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info (everything)</option>
          </select></div>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="pk-muted" style={{ fontSize: 12 }}>Only these priorities (none ticked = any)</legend>
        <div style={{ display: "flex", gap: 12 }}>
          {PRIORITIES.map((p) => (
            <label key={p} style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <input type="checkbox" checked={priorities.includes(p)} onChange={() => setPriorities(toggle(priorities, p))} />{p}
            </label>
          ))}
        </div>
      </fieldset>
      {sites.length > 0 && (
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="pk-muted" style={{ fontSize: 12 }}>Only these sites (none ticked = any)</legend>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", maxHeight: 120, overflowY: "auto" }}>
            {sites.map((s) => (
              <label key={s.id} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <input type="checkbox" checked={siteIds.includes(s.id)} onChange={() => setSiteIds(toggle(siteIds, s.id))} />{s.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="pk-muted" style={{ fontSize: 12 }}>Send to the organization&apos;s channels</legend>
        <div style={{ display: "flex", gap: 12 }}>
          {CHANNELS.map((c) => (
            <label key={c.key} style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <input type="checkbox" checked={channels.includes(c.key)} onChange={() => setChannels(toggle(channels, c.key))} />{c.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
        <div className="pk-field"><label htmlFor="np-mail">Email recipients for this policy (optional)</label>
          <input id="np-mail" className="pk-input" value={recipients} maxLength={2000} onChange={(e) => setRecipients(e.target.value)}
            placeholder="Instead of the organization's list; comma-separated" disabled={!channels.includes("email")} /></div>
        <div className="pk-field"><label htmlFor="np-teams">Microsoft Teams webhook (optional)</label>
          <input id="np-teams" className="pk-input" value={teams} maxLength={2048} onChange={(e) => setTeams(e.target.value)}
            placeholder="https://….webhook.office.com/… or a Workflows URL" autoComplete="off" /></div>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" className="pk-button pk-button--primary" disabled={busy || !name.trim() || !hasDestination}
          onClick={() => onSave({
            name: name.trim(), min_severity: minSeverity, priorities, site_ids: siteIds, channels,
            email_recipients: channels.includes("email") ? recipients.trim() || null : null,
            teams_webhook_url: teams.trim(),
          })}>
          {initial ? "Save policy" : "Add policy"}
        </button>
        <button type="button" className="pk-button" disabled={busy} onClick={onCancel}>Cancel</button>
        {!hasDestination && <span className="pk-muted">Choose a channel or add a Teams webhook.</span>}
      </div>
    </div>
  );
}

export default function NotificationPolicies() {
  const { tenantId } = useAuth();
  const [policies, setPolicies] = useState<NotificationPolicy[] | null>(null);
  const [sites, setSites] = useState<AssetSite[]>([]);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad" | "info"; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listPolicies().then((p) => alive && setPolicies(p)).catch((e) => alive && setError(e instanceof Error ? e.message : "Policies could not be loaded."));
    if (tenantId) listAssetSites(tenantId, { limit: 500 }).then((s) => alive && setSites(s)).catch(() => undefined);
    return () => { alive = false; };
  }, [tenantId]);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setPolicies(await listPolicies());
      setEditing(null);
      setMessage({ tone: "good", text: done });
    } catch (e) {
      setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The change could not be saved." });
    } finally {
      setBusy(false);
    }
  }

  async function test(p: NotificationPolicy) {
    setBusy(true);
    setMessage(null);
    try {
      const r = await testPolicy(p.id);
      setMessage({ tone: "info", text: `Test for "${p.name}": ${Object.entries(r).map(([k, v]) => `${k} ${v}`).join(" · ") || "no destination"}` });
    } catch (e) {
      setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The test could not be sent." });
    } finally {
      setBusy(false);
    }
  }

  const enabled = (policies ?? []).filter((p) => p.enabled).length;
  const siteName = (id: string) => sites.find((s) => s.id === id)?.name ?? "a site";

  return (
    <Panel
      title="Notification policies"
      info="Route incidents by severity, priority and site. With no enabled policy, every enabled channel gets every incident at its minimum severity."
      aside={editing === null ? <button type="button" className="pk-button" onClick={() => { setEditing("new"); setMessage(null); }}>Add policy</button> : undefined}
      state={error ? "error" : policies === null ? "loading" : "ready"}
      errorText={error ?? undefined}
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      {policies && (
        <Banner tone="info">
          {enabled === 0
            ? "No policy is enabled: every enabled channel above receives every incident at its minimum severity."
            : `${enabled} polic${enabled === 1 ? "y is" : "ies are"} enabled: each incident goes only where a matching policy sends it.`}
        </Banner>
      )}
      {editing === "new" && <Editor initial={null} sites={sites} busy={busy} onCancel={() => setEditing(null)}
        onSave={(body) => void run(() => createPolicy(body), "Policy added.")} />}
      {(policies ?? []).length === 0 && editing !== "new" && <p className="pk-muted">No notification policies yet.</p>}
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
        {(policies ?? []).map((p) => editing === p.id ? (
          <li key={p.id}><Editor initial={p} sites={sites} busy={busy} onCancel={() => setEditing(null)}
            onSave={(body) => void run(() => updatePolicy(p.id, body), "Policy saved.")} /></li>
        ) : (
          <li key={p.id} style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", borderBottom: "1px solid var(--ecc-border)", paddingBottom: 8 }}>
            <strong style={{ minWidth: 160 }}>{p.name}</strong>
            <Pill tone={p.enabled ? "good" : "neutral"}>{p.enabled ? "Enabled" : "Off"}</Pill>
            <span className="pk-muted">
              {p.min_severity === "info" ? "All incidents" : `${p.min_severity[0].toUpperCase()}${p.min_severity.slice(1)} and above`}
              {p.priorities.length > 0 && ` · ${p.priorities.join(", ")}`}
              {p.site_ids.length > 0 && ` · ${p.site_ids.map(siteName).join(", ")}`}
              {" → "}
              {[...p.channels.map((c) => (c === "email" && p.email_recipients ? `email (${p.email_recipients})` : c)),
                ...(p.teams_webhook ? [`Teams ${p.teams_webhook}`] : [])].join(", ") || "nowhere"}
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <button type="button" className="pk-button" disabled={busy} onClick={() => void run(() => updatePolicy(p.id, { enabled: !p.enabled }), p.enabled ? "Policy turned off." : "Policy enabled.")}>
                {p.enabled ? "Turn off" : "Enable"}
              </button>
              <button type="button" className="pk-button" disabled={busy} onClick={() => void test(p)}>Test</button>
              <button type="button" className="pk-button" disabled={busy} onClick={() => { setEditing(p.id); setMessage(null); }}>Edit</button>
              <button type="button" className="pk-button" disabled={busy}
                onClick={() => { if (window.confirm(`Delete the policy "${p.name}"?`)) void run(() => deletePolicy(p.id), "Policy deleted."); }}>Delete</button>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
