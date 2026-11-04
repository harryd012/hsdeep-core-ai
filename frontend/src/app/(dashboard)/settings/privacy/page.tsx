"use client";

/**
 * /settings/privacy — privacy & compliance (enterprise security programme S5):
 * how AI may use this organization's data, how long telemetry is kept, a full
 * data export, and (Owner only) deleting the organization after a 30-day grace
 * period. Every change is recorded in the audit log.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Download, FileArchive, Lock, ShieldCheck, Trash2 } from "lucide-react";
import { Banner, PageShell, Panel, Pill, ago } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import {
  cancelDeletion,
  downloadExport,
  formatBytes,
  getPrivacy,
  listExports,
  requestDeletion,
  requestExport,
  updatePrivacy,
  type PrivacySettings,
  type PrivacyUpdate,
} from "@/lib/privacy";

const RETENTION = [
  { value: "", label: "Platform default" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "180 days" },
  { value: "365", label: "1 year" },
  { value: "730", label: "2 years" },
  { value: "1825", label: "5 years" },
];

type Msg = { tone: "good" | "bad"; text: string } | null;

function useSave(onDone: (s: PrivacySettings) => void, setMsg: (m: Msg) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PrivacyUpdate) => updatePrivacy(body),
    onSuccess: (s) => { qc.setQueryData(["settings", "privacy"], s); onDone(s); setMsg({ tone: "good", text: "Saved." }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Could not save." }),
  });
}

function AiPanel({ p, canManage }: { p: PrivacySettings; canManage: boolean }) {
  const [msg, setMsg] = useState<Msg>(null);
  const save = useSave(() => undefined, setMsg);
  const set = (body: PrivacyUpdate) => save.mutate(body);
  return (
    <Panel title="AI data use" info="Applies to the AI Copilot, AI diagnosis and every other AI feature for your organization." state="ready"
      aside={p.ai_mode === "on" ? <Pill tone="good">AI on</Pill> : <Pill tone="neutral">AI off</Pill>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <fieldset className="sec-fieldset" disabled={!canManage || save.isPending}>
        <label className="sec-row">
          <span>
            <b>Allow AI features</b>
            <small>Off: no data from your organization is sent to any AI model, and AI features show as unavailable.</small>
          </span>
          <input type="checkbox" className="sec-check" checked={p.ai_mode === "on"} onChange={(e) => set({ ai_mode: e.target.checked ? "on" : "off" })} />
        </label>
        <div className="sec-row sec-row--muted">
          <span>
            <b>Remove credentials from prompts</b>
            <small>Passwords, tokens, keys and connection strings are always removed before anything reaches a model.</small>
          </span>
          <Pill tone="good">Always on</Pill>
        </div>
        <label className="sec-row">
          <span><b>Remove email addresses from prompts</b><small>Replaced with [EMAIL].</small></span>
          <input type="checkbox" className="sec-check" checked={p.ai_redact_emails} onChange={(e) => set({ ai_redact_emails: e.target.checked })} />
        </label>
        <label className="sec-row">
          <span><b>Remove IP addresses from prompts</b><small>Replaced with [IP]. Diagnoses that depend on addresses get less specific.</small></span>
          <input type="checkbox" className="sec-check" checked={p.ai_redact_ips} onChange={(e) => set({ ai_redact_ips: e.target.checked })} />
        </label>
      </fieldset>
    </Panel>
  );
}

function RetentionPanel({ p, canManage }: { p: PrivacySettings; canManage: boolean }) {
  const [msg, setMsg] = useState<Msg>(null);
  const save = useSave(() => undefined, setMsg);
  const platform = p.platform_metrics_retention_days ? `${p.platform_metrics_retention_days} days` : "kept indefinitely";
  const pick = (field: "metrics_retention_days" | "events_retention_days", v: string) =>
    save.mutate({ [field]: v ? Number(v) : null } as PrivacyUpdate);
  const opts = (current: number | null) =>
    current && !RETENTION.some((r) => r.value === String(current)) ? [...RETENTION, { value: String(current), label: `${current} days` }] : RETENTION;
  return (
    <Panel title="Data retention" info="Older history is deleted automatically every hour." state="ready">
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <fieldset className="sec-fieldset" disabled={!canManage || save.isPending}>
        <label className="sec-row">
          <span><b>Metric and sensor history</b><small>Platform default: {platform}.</small></span>
          <select className="pk-input sec-select" value={p.metrics_retention_days ?? ""} onChange={(e) => pick("metrics_retention_days", e.target.value)}>
            {opts(p.metrics_retention_days).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <label className="sec-row">
          <span><b>Raw events from sources</b><small>Platform default: kept indefinitely.</small></span>
          <select className="pk-input sec-select" value={p.events_retention_days ?? ""} onChange={(e) => pick("events_retention_days", e.target.value)}>
            {opts(p.events_retention_days).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <p className="sec-help" style={{ margin: 0 }}>
          Alerts, incidents and the audit log are not affected. The audit log is kept for the life of your organization.
        </p>
      </fieldset>
    </Panel>
  );
}

function ExportPanel() {
  const qc = useQueryClient();
  const exports = useQuery({
    queryKey: ["settings", "exports"],
    queryFn: listExports,
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === "pending" || e.status === "running") ? 5000 : false),
  });
  const [msg, setMsg] = useState<Msg>(null);
  const request = useMutation({
    mutationFn: requestExport,
    onSuccess: () => { setMsg({ tone: "good", text: "Export queued. It is usually ready within a few minutes." }); qc.invalidateQueries({ queryKey: ["settings", "exports"] }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Could not start the export." }),
  });
  const list = exports.data ?? [];
  const busy = list.some((e) => e.status === "pending" || e.status === "running");
  return (
    <Panel title="Export your data" info="Everything your organization owns, one JSON-lines file per data set, in a ZIP kept for 7 days."
      state={exports.data ? "ready" : exports.isError ? "error" : "loading"} onRetry={() => exports.refetch()} skeletonHeight={120}
      aside={<button type="button" className="pk-button pk-button--primary" style={{ height: 28 }} disabled={busy || request.isPending} onClick={() => request.mutate()}>
        <FileArchive size={14} aria-hidden="true" /> {busy ? "Preparing…" : "Export all data"}
      </button>}>
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      <p className="sec-help">Passwords, keys, tokens and other credentials are never included. Each download is recorded in the audit log.</p>
      {list.length > 0 && (
        <ul className="sec-sessions">
          {list.map((e) => (
            <li key={e.id}>
              <span className="sec-sessions__icon" aria-hidden="true"><FileArchive size={16} /></span>
              <span className="sec-sessions__main">
                <b>
                  {new Date(e.created_at).toLocaleString()}
                  <Pill tone={e.status === "ready" ? "good" : e.status === "failed" ? "bad" : e.status === "expired" ? "neutral" : "info"}>{e.status}</Pill>
                </b>
                <small>
                  {e.requested_by ?? "—"}
                  {e.status === "ready" && <> · {formatBytes(e.size_bytes)} · {Object.values(e.summary).reduce((a, b) => a + b, 0).toLocaleString("en-US")} records · available until {e.expires_at ? new Date(e.expires_at).toLocaleDateString() : "—"}</>}
                  {e.status === "failed" && <> · failed ({e.error})</>}
                </small>
              </span>
              {e.status === "ready" && (
                <button type="button" className="pk-button" onClick={() => downloadExport(e.id).catch((err) => setMsg({ tone: "bad", text: err instanceof Error ? err.message : "Download failed." }))}>
                  <Download size={14} aria-hidden="true" /> Download
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function DeletionPanel({ p, orgName }: { p: PrivacySettings; orgName: string | null }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const done = (s: PrivacySettings) => { qc.setQueryData(["settings", "privacy"], s); setConfirm(""); };
  const request = useMutation({ mutationFn: () => requestDeletion(confirm), onSuccess: (s) => { done(s); setMsg(null); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Could not schedule deletion." }) });
  const cancel = useMutation({ mutationFn: cancelDeletion, onSuccess: (s) => { done(s); setMsg({ tone: "good", text: "Deletion cancelled. Nothing was removed." }); },
    onError: (e) => setMsg({ tone: "bad", text: e instanceof Error ? e.message : "Could not cancel." }) });
  return (
    <Panel title="Delete organization" info="Owners only. Everything is removed after a 30-day grace period; until then it can be cancelled." state="ready" className="priv-danger">
      {msg && <Banner tone={msg.tone}>{msg.text}</Banner>}
      {p.deletion_scheduled_for ? (
        <>
          <Banner tone="bad">
            {orgName ?? "This organization"} and all of its data will be permanently deleted on{" "}
            <b>{new Date(p.deletion_scheduled_for).toLocaleDateString()}</b> (requested by {p.deletion_requested_by ?? "an owner"} {ago(p.deletion_requested_at)}).
          </Banner>
          <button type="button" className="pk-button pk-button--primary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel deletion</button>
        </>
      ) : (
        <form className="priv-delete" onSubmit={(e) => { e.preventDefault(); if (confirm.trim()) request.mutate(); }}>
          <p className="sec-help">
            Schedules permanent deletion of every device, alert, incident, setting, user and audit record in 30 days. Export your data first if you need it.
          </p>
          <label htmlFor="priv-confirm" className="sec-help" style={{ margin: 0 }}>Type <b>{orgName ?? "your organization's name"}</b> to confirm</label>
          <span className="net-form">
            <input id="priv-confirm" className="pk-input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
            <button type="submit" className="pk-button sec-danger" disabled={!confirm.trim() || request.isPending}>
              <Trash2 size={14} aria-hidden="true" /> Schedule deletion
            </button>
          </span>
        </form>
      )}
    </Panel>
  );
}

export default function PrivacyPage() {
  const { user } = useAuth();
  const has = (perm: string) => !!user && (user.is_superuser || user.permissions.includes(perm));
  const privacy = useQuery({ queryKey: ["settings", "privacy"], queryFn: getPrivacy });
  const p = privacy.data;
  const canManage = has("privacy.manage");
  const orgName = (user as { tenant_name?: string | null } | null)?.tenant_name ?? null;

  return (
    <PageShell
      title="Privacy & data"
      icon={Lock}
      subtitle="How AI may use your data, how long it is kept, a full export, and deleting your organization."
      crumbs={[{ label: "Settings", href: "/settings" }, { label: "Privacy & data" }]}
      actions={<Link href="/trust" className="pk-button"><ShieldCheck size={14} aria-hidden="true" /> Trust & security</Link>}
    >
      {p?.deletion_scheduled_for && <Banner tone="bad">This organization is scheduled for deletion on {new Date(p.deletion_scheduled_for).toLocaleDateString()}.</Banner>}
      {!canManage && p && <Banner tone="info">You can view these settings. Changing them needs the privacy.manage permission (Owner or Admin).</Banner>}
      {!p ? (
        <Panel title="Privacy" state={privacy.isError ? "error" : "loading"} onRetry={() => privacy.refetch()} skeletonHeight={300} />
      ) : (
        <div className="sec-grid">
          <div className="sec-stack">
            <AiPanel p={p} canManage={canManage} />
            <RetentionPanel p={p} canManage={canManage} />
          </div>
          <div className="sec-stack">
            {canManage && <ExportPanel />}
            {has("tenant.delete") && <DeletionPanel p={p} orgName={orgName} />}
          </div>
        </div>
      )}
    </PageShell>
  );
}
