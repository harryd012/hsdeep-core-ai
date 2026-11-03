"use client";

/**
 * Second sign-in step (enterprise security programme S2): the 6-digit code
 * from the authenticator app, or a one-time recovery code. Shown in place of
 * the sign-in card after the password was accepted for an account with MFA.
 */

import { useState, type FormEvent } from "react";
import { ArrowLeft, KeyRound, ShieldCheck } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function MfaCodeStep({
  challenge,
  onDone,
  onCancel,
}: {
  challenge: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { completeMfa } = useAuth();
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = code.trim();
    if (submitting || !value) return;
    setSubmitting(true);
    setError(null);
    try {
      await completeMfa(challenge, value);
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError("Too many attempts. Please wait a moment and try again.");
      else if (err instanceof ApiError && err.status === 0) setError("Can't reach the server. Check your connection and try again.");
      else setError(err instanceof Error && err.message ? err.message : "That code didn't work.");
      setCode("");
      document.getElementById("mfa-code")?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="lx-card" aria-labelledby="lx-mfa-title">
      <p className="lx-card__eyebrow lx-mfa__eyebrow">
        <ShieldCheck size={18} aria-hidden="true" /> Two-step verification
      </p>
      <h2 id="lx-mfa-title" className="lx-card__title lx-card__title--sm">
        {recovery ? "Use a recovery code" : "Enter your code"}
      </h2>
      <p className="lx-card__lead">
        {recovery
          ? "Enter one of the recovery codes you saved when you set up two-step verification. Each code works once."
          : "Open your authenticator app and enter the 6-digit code for HSDEEP CORE AI."}
      </p>

      {error && (
        <div role="alert" className="lx-card__error">
          {error}
        </div>
      )}

      <form method="post" onSubmit={submit} noValidate className="lx-form">
        <div className="lx-field">
          <label htmlFor="mfa-code" className="lx-sr-only">
            {recovery ? "Recovery code" : "6-digit code"}
          </label>
          <KeyRound className="lx-field__icon" size={18} aria-hidden="true" />
          <input
            id="mfa-code"
            key={recovery ? "recovery" : "totp"}
            className={`lx-input${recovery ? "" : " lx-input--code"}`}
            value={code}
            onChange={(e) => setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode={recovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            autoFocus
            placeholder={recovery ? "xxxxx-xxxxx" : "000000"}
            disabled={submitting}
            aria-invalid={error ? true : undefined}
          />
        </div>
        <button type="submit" className="lx-submit" disabled={submitting || (recovery ? code.trim().length < 10 : code.length !== 6)}>
          {submitting ? "Verifying…" : "Verify and sign in"}
        </button>
        <button type="button" className="lx-link lx-mfa__switch" onClick={() => { setRecovery((r) => !r); setCode(""); setError(null); }}>
          {recovery ? "Use your authenticator app instead" : "Lost your device? Use a recovery code"}
        </button>
      </form>

      <p className="lx-card__foot">
        <button type="button" className="lx-back lx-mfa__back" onClick={onCancel}>
          <ArrowLeft size={15} aria-hidden="true" /> Sign in with a different account
        </button>
      </p>
    </section>
  );
}
