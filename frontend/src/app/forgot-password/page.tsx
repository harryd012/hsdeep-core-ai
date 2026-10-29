"use client";

/** Request a password reset link. The answer is the same for any email. */
import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { accountErrorMessage, requestPasswordReset } from "@/lib/account";
import { AccountCard, BUTTON_CLASS, INPUT_CLASS, LABEL_CLASS } from "@/components/auth/AccountCard";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;
    if (!email.trim()) return setError("Enter the email you sign in with.");
    setSubmitting(true);
    setError(null);
    try {
      const res = await requestPasswordReset(email.trim());
      setNotice(`${res.detail} The link expires in 30 minutes.`);
    } catch (err) {
      setError(err instanceof ApiError ? accountErrorMessage(err.status, err.message) : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AccountCard subtitle="Reset your password" error={error} notice={notice} onSubmit={handleSubmit}>
      <label htmlFor="email" className={LABEL_CLASS}>Email</label>
      <input id="email" type="email" autoComplete="email" required value={email}
        onChange={(e) => setEmail(e.target.value)} className={INPUT_CLASS} />
      <button type="submit" disabled={submitting} className={BUTTON_CLASS}>
        {submitting ? "Sending…" : "Email me a reset link"}
      </button>
    </AccountCard>
  );
}
