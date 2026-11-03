"use client";

/**
 * Two-step verification for the signed-in user (enterprise security programme
 * S2): set up an authenticator app (QR code or key), confirm with a code, save
 * the recovery codes shown once, replace them, or turn MFA off (not allowed
 * while the organization requires it).
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, KeyRound, ShieldCheck } from "lucide-react";
import { Banner, Panel, Pill } from "@/components/ui/PageKit";
import { refreshAuthToken } from "@/lib/api";
import {
  confirmMfa,
  disableMfa,
  getMfaStatus,
  groupSecret,
  regenerateRecoveryCodes,
  startMfaSetup,
  type MfaSetup,
} from "@/lib/security";

type Prompt = "regenerate" | "disable" | null;

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const text = `HSDEEP CORE AI recovery codes\nEach code works once.\n\n${codes.join("\n")}\n`;
  const [copied, setCopied] = useState(false);
  return (
    <div className="mfa-codes">
      <Banner tone="warn">Save these recovery codes now. They are shown only once and let you sign in if you lose your device.</Banner>
      <ol className="mfa-codes__list">
        {codes.map((c) => <li key={c}><code>{c}</code></li>)}
      </ol>
      <div className="sec-actions">
        <button type="button" className="pk-button" onClick={() => { void navigator.clipboard?.writeText(text); setCopied(true); }}>
          <Copy size={14} aria-hidden="true" /> {copied ? "Copied" : "Copy"}
        </button>
        <a className="pk-button" download="hsdeep-recovery-codes.txt" href={`data:text/plain;charset=utf-8,${encodeURIComponent(text)}`}>
          <Download size={14} aria-hidden="true" /> Download
        </a>
        <button type="button" className="pk-button pk-button--primary" onClick={onDone}>I&apos;ve saved them</button>
      </div>
    </div>
  );
}

function CodeForm({ label, busy, onSubmit, onCancel, submitText }: {
  label: string;
  busy: boolean;
  onSubmit: (code: string) => void;
  onCancel?: () => void;
  submitText: string;
}) {
  const [code, setCode] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (code.trim()) onSubmit(code.trim());
  }
  return (
    <form className="mfa-code" onSubmit={submit}>
      <label htmlFor="mfa-panel-code" className="sec-help">{label}</label>
      <span className="mfa-code__row">
        <input id="mfa-panel-code" className="pk-input mfa-code__input" value={code} autoFocus autoComplete="one-time-code"
          inputMode="text" placeholder="123456" maxLength={32} onChange={(e) => setCode(e.target.value)} disabled={busy} />
        <button type="submit" className="pk-button pk-button--primary" disabled={busy || code.trim().length < 6}>{submitText}</button>
        {onCancel && <button type="button" className="pk-button" onClick={onCancel} disabled={busy}>Cancel</button>}
      </span>
    </form>
  );
}

export default function MfaPanel() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth", "mfa"], queryFn: getMfaStatus });
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [prompt, setPrompt] = useState<Prompt>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["auth", "mfa"] });
  const fail = (e: unknown) => setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The request failed." });

  const begin = useMutation({
    mutationFn: startMfaSetup,
    onSuccess: (s) => { setSetup(s); setMessage(null); },
    onError: fail,
  });
  const confirm = useMutation({
    mutationFn: (code: string) => confirmMfa(code),
    onSuccess: async (r) => {
      setSetup(null);
      setCodes(r.codes);
      setMessage({ tone: "good", text: "Two-step verification is on. You'll be asked for a code each time you sign in." });
      // Drop any "set up MFA first" restriction from the current session.
      await refreshAuthToken().catch(() => undefined);
      done();
    },
    onError: fail,
  });
  const regenerate = useMutation({
    mutationFn: (code: string) => regenerateRecoveryCodes(code),
    onSuccess: (r) => { setPrompt(null); setCodes(r.codes); setMessage(null); done(); },
    onError: fail,
  });
  const disable = useMutation({
    mutationFn: (code: string) => disableMfa(code),
    onSuccess: () => { setPrompt(null); setMessage({ tone: "good", text: "Two-step verification is off." }); done(); },
    onError: fail,
  });
  const busy = begin.isPending || confirm.isPending || regenerate.isPending || disable.isPending;
  const s = status.data;

  return (
    <Panel
      title="Two-step verification"
      info="A code from an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password …) on top of your password."
      state={s ? "ready" : status.isError ? "error" : "loading"}
      onRetry={() => status.refetch()}
      skeletonHeight={160}
      aside={s && (s.enabled ? <Pill tone="good">On</Pill> : <Pill tone={s.required_by_policy ? "bad" : "neutral"}>Off</Pill>)}
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      {codes ? (
        <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />
      ) : s && !s.enabled ? (
        setup ? (
          <div className="mfa-setup">
            <ol className="mfa-setup__steps">
              <li>Open your authenticator app and add an account.</li>
              <li>Scan this QR code, or enter the key by hand.</li>
              <li>Enter the 6-digit code the app shows.</li>
            </ol>
            <div className="mfa-setup__qr">
              {/* eslint-disable-next-line @next/next/no-img-element -- server-rendered SVG as a data URI */}
              <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(setup.qr_svg)}`} alt="QR code for your authenticator app" width={180} height={180} />
              <div>
                <span className="sec-help">Key</span>
                <code className="mfa-setup__secret">{groupSecret(setup.secret)}</code>
              </div>
            </div>
            <CodeForm label="Code from your app" busy={busy} submitText="Turn on" onSubmit={(c) => confirm.mutate(c)} onCancel={() => setSetup(null)} />
          </div>
        ) : (
          <div className="mfa-off">
            {s.required_by_policy && (
              <Banner tone="warn">Your organization requires two-step verification. Set it up to continue using HSDEEP CORE AI.</Banner>
            )}
            <p className="sec-help">Protect your account even if your password is stolen. You&apos;ll need your phone (or another authenticator) when you sign in.</p>
            <button type="button" className="pk-button pk-button--primary" disabled={busy} onClick={() => begin.mutate()}>
              <ShieldCheck size={14} aria-hidden="true" /> Set up two-step verification
            </button>
          </div>
        )
      ) : s ? (
        <div className="mfa-on">
          <p className="sec-help">
            On since {s.confirmed_at ? new Date(s.confirmed_at).toLocaleDateString() : "—"}.{" "}
            <span className={s.recovery_codes_remaining <= 3 ? "mfa-low" : undefined}>
              {s.recovery_codes_remaining} recovery code{s.recovery_codes_remaining === 1 ? "" : "s"} left.
            </span>
          </p>
          {prompt ? (
            <CodeForm
              label={prompt === "disable" ? "Enter a current code to turn two-step verification off" : "Enter a current code to replace your recovery codes"}
              busy={busy}
              submitText={prompt === "disable" ? "Turn off" : "Replace codes"}
              onSubmit={(c) => (prompt === "disable" ? disable.mutate(c) : regenerate.mutate(c))}
              onCancel={() => setPrompt(null)}
            />
          ) : (
            <div className="sec-actions" style={{ justifyContent: "flex-start" }}>
              <button type="button" className="pk-button" onClick={() => setPrompt("regenerate")}>
                <KeyRound size={14} aria-hidden="true" /> New recovery codes
              </button>
              {s.required_by_policy ? (
                <span className="sec-help" style={{ margin: 0 }}>Required by your organization.</span>
              ) : (
                <button type="button" className="pk-button sec-danger" onClick={() => setPrompt("disable")}>Turn off</button>
              )}
            </div>
          )}
        </div>
      ) : null}
    </Panel>
  );
}
