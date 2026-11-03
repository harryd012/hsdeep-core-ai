"use client";

/**
 * /signup — self-service account creation (PRODUCTION_READINESS.md: SaaS
 * subscription gap fix), on the shared sign-in scene (components/auth/AuthScene).
 *
 * Calls the pre-existing backend endpoint POST /api/billing/signup, which
 * already does everything correctly server-side: creates a Tenant, seeds
 * RBAC roles, creates the first user as "Owner", starts a 14-day trial
 * subscription, and returns tokens (auto-login). This page just gives that
 * endpoint a real front door — previously there was no way for a brand-new
 * customer (MSP or enterprise IT team) to create an account at all.
 *
 * Credential safety, as on /login: the form is method="post", the submit
 * button waits for hydration, and a password or email that reached the URL
 * is removed from the address bar on load.
 */
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Building2, Eye, EyeOff, Lock, Mail, User } from "lucide-react";
import AuthScene, { useHydrated } from "@/components/auth/AuthScene";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SignupPage() {
  const { signup } = useAuth();
  const router = useRouter();
  const hydrated = useHydrated();

  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Credentials must never live in the URL.
  useEffect(() => {
    const url = new URL(window.location.href);
    const leaked = ["password", "email", "orgName", "fullName"].filter((k) => url.searchParams.has(k));
    if (!leaked.length) return;
    leaked.forEach((k) => url.searchParams.delete(k));
    window.history.replaceState(null, "", url.pathname + url.search);
  }, []);

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
        } else if (err.status === 403) {
          setError("Sign-up is by invitation only. Ask your administrator for an account.");
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
    <AuthScene title="HSDEEP CORE AI — Start your free trial">
      <section className="lx-card" aria-labelledby="lx-card-title">
        <p className="lx-card__eyebrow">Get started with</p>
        <h2 id="lx-card-title" className="lx-card__title">
          HSDEEP CORE <span>AI</span>
        </h2>
        <p className="lx-card__lead">Start your 14-day free trial. No payment required now.</p>

        {error && (
          <div role="alert" className="lx-card__error">
            {error}
          </div>
        )}

        <form method="post" action="/signup" onSubmit={handleSubmit} noValidate className="lx-form">
          <div className="lx-field">
            <label htmlFor="orgName" className="lx-sr-only">Organization name</label>
            <Building2 className="lx-field__icon" size={18} aria-hidden="true" />
            <input
              id="orgName"
              name="orgName"
              type="text"
              autoComplete="organization"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              disabled={submitting}
              required
              className="lx-input"
              placeholder="Organization name"
            />
          </div>

          <div className="lx-field">
            <label htmlFor="fullName" className="lx-sr-only">Your name (optional)</label>
            <User className="lx-field__icon" size={18} aria-hidden="true" />
            <input
              id="fullName"
              name="fullName"
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={submitting}
              className="lx-input"
              placeholder="Your name (optional)"
            />
          </div>

          <div className="lx-field">
            <label htmlFor="email" className="lx-sr-only">Work email</label>
            <Mail className="lx-field__icon" size={18} aria-hidden="true" />
            <input
              id="email"
              name="email"
              type="email"
              inputMode="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
              required
              className="lx-input"
              placeholder="Work email"
            />
          </div>

          <div className="lx-field">
            <label htmlFor="password" className="lx-sr-only">Password</label>
            <Lock className="lx-field__icon" size={18} aria-hidden="true" />
            <input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
              required
              minLength={8}
              aria-describedby="signup-password-hint"
              className="lx-input"
              placeholder="Password (8+ characters)"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="lx-field__toggle"
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
            >
              {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          <p id="signup-password-hint" className="lx-card__hint lx-card__hint--left">
            You&apos;ll start on the Starter plan as the workspace owner.
          </p>

          <button type="submit" disabled={!hydrated || submitting} className="lx-submit">
            {submitting ? "Creating your workspace…" : hydrated ? "Start free trial" : "Loading…"}
            {!submitting && hydrated && <ArrowRight size={17} aria-hidden="true" />}
          </button>
        </form>

        <p className="lx-card__foot">
          Already have an account? <a href="/login">Sign in</a>
        </p>
      </section>
    </AuthScene>
  );
}
