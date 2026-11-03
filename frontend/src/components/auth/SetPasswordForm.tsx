"use client";

/** Set a password from an emailed link (/reset-password, /accept-invite). */
import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api";
import { acceptInvite, accountErrorMessage, resetPassword } from "@/lib/account";
import { AccountCard, BUTTON_CLASS } from "@/components/auth/AccountCard";
import PasswordField, { MIN_PASSWORD_LENGTH } from "@/components/auth/PasswordField";

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
    if (password.length < MIN_PASSWORD_LENGTH) return setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
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
      lead={done ? undefined : mode === "reset" ? "Set a new password for your account." : "Set a password to join your team's workspace."}
      error={error}
      notice={done}
      onSubmit={handleSubmit}
    >
      {done ? (
        <a href="/login" className={BUTTON_CLASS}>
          Sign in
        </a>
      ) : (
        <>
          <PasswordField id="password" value={password} onChange={setPassword} placeholder="New password"
            autoComplete="new-password" disabled={submitting} meter />
          <PasswordField id="confirm" value={confirm} onChange={setConfirm} placeholder="Confirm password"
            autoComplete="new-password" disabled={submitting} invalid={!!confirm && confirm !== password} />
          {confirm && confirm !== password && <p className="lx-caps">The passwords don&apos;t match yet.</p>}
          <button type="submit" disabled={submitting || !token} className={BUTTON_CLASS}>
            {submitting ? "Saving…" : mode === "reset" ? "Set new password" : "Set password and continue"}
          </button>
        </>
      )}
    </AccountCard>
  );
}
