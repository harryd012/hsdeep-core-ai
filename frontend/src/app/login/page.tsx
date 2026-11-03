"use client";

/* /login — HSDEEP CORE AI enterprise sign-in: a glass sign-in card on the
 * shared full-bleed brand scene (components/auth/AuthScene).
 *
 * Authentication flow is unchanged: POST /api/auth/login via lib/api.ts's
 * loginWithCredentials wrapped by AuthProvider. Redirects honor ?next=.
 *
 * Credential safety: the form is method="post" and its submit button stays
 * disabled until the page has hydrated, so a click before JavaScript loads can
 * never fall back to a GET that puts the email and password in the URL. If a
 * URL still carries them (an old bookmark or history entry), they are removed
 * from the address bar on load.
 */

import { useState, Suspense, type FormEvent, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Building2, Eye, EyeOff, Lock, User } from "lucide-react";
import AuthScene, { useHydrated } from "@/components/auth/AuthScene";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function safeNextPath(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/dashboard";
}

function LoginForm() {
  const { login, isAuthenticated, isLoading: sessionLoading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams?.get("next") ?? null);
  const hydrated = useHydrated();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in (e.g. dev bootstrap, or navigated here by mistake)?
  // Bounce straight to the destination rather than showing the form.
  useEffect(() => {
    if (!sessionLoading && isAuthenticated) {
      router.replace(next);
    }
  }, [sessionLoading, isAuthenticated, router, next]);

  // Credentials must never live in the URL: strip them if a GET submit or an
  // old link put them there (keeps ?next=).
  useEffect(() => {
    if (!searchParams?.has("password") && !searchParams?.has("email")) return;
    const clean = new URLSearchParams(searchParams.toString());
    clean.delete("password");
    clean.delete("email");
    const qs = clean.toString();
    window.history.replaceState(null, "", qs ? `/login?${qs}` : "/login");
  }, [searchParams]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;

    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setError("Enter both your email and password.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await login({ email: trimmedEmail, password });
      router.replace(next);
    } catch (err) {
      // Surface the real cause rather than one generic message.
      if (err instanceof ApiError) {
        if (err.status === 0) {
          setError("Can't reach the server. Check your connection and try again.");
        } else if (err.status === 401 || err.status === 400) {
          setError("Invalid email or password.");
        } else if (err.status === 403) {
          // e.g. "Confirm your email address before signing in."
          setError(err.message || "Your account is not permitted to sign in. Contact your administrator.");
        } else if (err.status === 429) {
          setError("Too many attempts. Please wait a moment and try again.");
        } else if (err.status >= 500) {
          setError("The server could not complete sign-in. Please try again shortly.");
        } else {
          setError(err.message || "Sign-in failed. Please try again.");
        }
      } else if (err instanceof DOMException && err.name === "AbortError") {
        setError("The sign-in request timed out. Please try again.");
      } else {
        setError(err instanceof Error && err.message ? `Sign-in failed: ${err.message}` : "Unexpected error. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="lx-card" aria-labelledby="lx-card-title">
      <p className="lx-card__eyebrow">Welcome to</p>
      <h2 id="lx-card-title" className="lx-card__title">
        HSDEEP CORE <span>AI</span>
      </h2>
      <p className="lx-card__lead">Sign in to your enterprise workspace</p>

      {error && (
        <div role="alert" className="lx-card__error">
          {error}
        </div>
      )}

      <form method="post" action="/login" onSubmit={handleSubmit} noValidate className="lx-form">
        <div className="lx-field">
          <label htmlFor="email" className="lx-sr-only">
            Email address
          </label>
          <User className="lx-field__icon" size={18} aria-hidden="true" />
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
            placeholder="Email address"
          />
        </div>

        <div className="lx-field">
          <label htmlFor="password" className="lx-sr-only">
            Password
          </label>
          <Lock className="lx-field__icon" size={18} aria-hidden="true" />
          <input
            id="password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            required
            className="lx-input"
            placeholder="Password"
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

        <div className="lx-form__row">
          <a className="lx-link" href="/forgot-password">
            Forgot password?
          </a>
        </div>

        <button type="submit" disabled={!hydrated || submitting} className="lx-submit">
          {submitting ? "Signing in…" : hydrated ? "Sign In" : "Loading…"}
          {!submitting && hydrated && <ArrowRight size={17} aria-hidden="true" />}
        </button>

        <div className="lx-divider">
          <span>or</span>
        </div>

        <button type="button" className="lx-sso" disabled title="Single sign-on is set up per organization by your administrator">
          <Building2 size={18} aria-hidden="true" />
          Sign in with SSO
        </button>
        <p className="lx-card__hint">SSO is enabled per organization by your administrator.</p>
      </form>

      <p className="lx-card__foot">
        New to HSDEEP CORE AI? <a href="/signup">Create an account</a>
      </p>
    </section>
  );
}

/** Suspense fallback shown while the login form hydrates / fetches session. */
function LoginFallback() {
  return <section className="lx-card lx-card--loading" aria-busy="true" />;
}

function LoginPage() {
  return (
    <AuthScene title="HSDEEP CORE AI — AI-Native IT Operations — Sign in">
      <Suspense fallback={<LoginFallback />}>
        <LoginForm />
      </Suspense>
    </AuthScene>
  );
}

export default LoginPage;
