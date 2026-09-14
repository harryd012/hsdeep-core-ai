"use client";

/**
 * /signup — self-service account creation (PRODUCTION_READINESS.md: SaaS
 * subscription gap fix).
 *
 * Calls the pre-existing backend endpoint POST /api/billing/signup, which
 * already does everything correctly server-side: creates a Tenant, seeds
 * RBAC roles, creates the first user as "Owner", starts a 14-day trial
 * subscription, and returns tokens (auto-login). This page just gives that
 * endpoint a real front door — previously there was no way for a brand-new
 * customer (MSP or enterprise IT team) to create an account at all.
 */
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SignupPage() {
  const { signup } = useAuth();
  const router = useRouter();

  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;

    const trimmedOrg = orgName.trim();
    const trimmedEmail = email.trim();
    if (!trimmedOrg || !trimmedEmail || password.length < 8) {
      setError(
        password.length > 0 && password.length < 8
          ? "Password must be at least 8 characters."
          : "Enter your organization name, email, and a password."
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await signup({
        org_name: trimmedOrg,
        email: trimmedEmail,
        password,
        full_name: fullName.trim() || undefined,
      });
      router.replace("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 0) {
          setError("Can't reach the server. Check your connection and try again.");
        } else if (err.status === 409) {
          setError("An account with this email already exists. Try signing in instead.");
        } else if (err.status === 429) {
          setError("Too many signups from this network. Please try again later.");
        } else if (err.status === 422) {
          setError(err.message || "Please check your details and try again.");
        } else {
          setError(err.message || "Sign-up failed. Please try again.");
        }
      } else {
        setError("Unexpected error. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-black px-4 py-12">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-white/10 bg-black/60 p-8 shadow-xl"
        noValidate
      >
        <div className="mb-6 text-center">
          <h1 className="text-lg font-semibold tracking-wide text-white">HSDEEP CORE AI</h1>
          <p className="mt-1 text-xs uppercase tracking-wide text-white/50">
            Start your 14-day free trial
          </p>
        </div>

        {error && (
          <div
            role="alert"
            className="mb-4 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300"
          >
            {error}
          </div>
        )}

        <label htmlFor="orgName" className="mb-1 block text-xs uppercase tracking-wide text-white/60">
          Organization name
        </label>
        <input
          id="orgName"
          name="orgName"
          type="text"
          autoComplete="organization"
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
          disabled={submitting}
          required
          className="mb-4 w-full rounded border border-white/15 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          placeholder="Acme MSP Inc."
        />

        <label htmlFor="fullName" className="mb-1 block text-xs uppercase tracking-wide text-white/60">
          Your name (optional)
        </label>
        <input
          id="fullName"
          name="fullName"
          type="text"
          autoComplete="name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          disabled={submitting}
          className="mb-4 w-full rounded border border-white/15 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          placeholder="Jane Doe"
        />

        <label htmlFor="email" className="mb-1 block text-xs uppercase tracking-wide text-white/60">
          Work email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={submitting}
          required
          className="mb-4 w-full rounded border border-white/15 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          placeholder="you@company.com"
        />

        <label htmlFor="password" className="mb-1 block text-xs uppercase tracking-wide text-white/60">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
          minLength={8}
          className="mb-2 w-full rounded border border-white/15 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          placeholder="At least 8 characters"
        />
        <p className="mb-6 text-[11px] text-white/40">
          You'll start on the Starter plan with a 14-day trial — no payment required now.
        </p>

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-white/90 px-3 py-2 text-sm font-medium text-black transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Creating your workspace…" : "Start free trial"}
        </button>

        <p className="mt-4 text-center text-xs text-white/50">
          Already have an account?{" "}
          <a href="/login" className="text-white/80 underline hover:text-white">
            Sign in
          </a>
        </p>
      </form>
    </div>
  );
}
