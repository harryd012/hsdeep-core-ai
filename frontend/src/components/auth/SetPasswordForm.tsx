"use client";

/** Set a password from an emailed link (/reset-password, /accept-invite). */
import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api";
import { acceptInvite, accountErrorMessage, resetPassword } from "@/lib/account";
import { AccountCard, BUTTON_CLASS, INPUT_CLASS, LABEL_CLASS } from "@/components/auth/AccountCard";

export function SetPasswordForm({ mode }: { mode: "reset" | "invite" }) {
  const token = useSearchParams()?.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(token ? null : "This link is missing its token. Open the link from your email again.");
  const [done, setDone] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting || done || !token) return;
    if (password.length < 8) return setError("Password must be at least 8 characters.");
    if (password !== confirm) return setError("The passwords don't match.");
    setSubmitting(true);
    setError(null);
    try {
      const res = mode === "reset" ? await resetPassword(token, password) : await acceptInvite(token, password);
      setDone(res.detail);
    } catch (err) {
      setError(err instanceof ApiError ? accountErrorMessage(err.status, err.message) : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AccountCard
      subtitle={mode === "reset" ? "Choose a new password" : "Accept your invitation"}
      error={error}
      notice={done}
      onSubmit={handleSubmit}
    >
      {done ? (
        <a href="/login" className={`${BUTTON_CLASS} block text-center`}>
          Sign in
        </a>
      ) : (
        <>
          <label htmlFor="password" className={LABEL_CLASS}>New password</label>
          <input id="password" type="password" autoComplete="new-password" required minLength={8}
            value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT_CLASS} />
          <label htmlFor="confirm" className={LABEL_CLASS}>Confirm password</label>
          <input id="confirm" type="password" autoComplete="new-password" required minLength={8}
            value={confirm} onChange={(e) => setConfirm(e.target.value)} className={INPUT_CLASS} />
          <button type="submit" disabled={submitting || !token} className={BUTTON_CLASS}>
            {submitting ? "Saving…" : mode === "reset" ? "Set new password" : "Set password and continue"}
          </button>
        </>
      )}
    </AccountCard>
  );
}
