"use client";

/* /login — HSDEEP CORE AI enterprise login.
 *
 * Centered enterprise authentication screen:
 * - compact brand header: HSDEEP CORE AI, Autonomous Infrastructure Intelligence
 * - centered glass sign-in card: email and password
 * - no hero video, no split-screen, no decorative imagery
 *
 * Authentication flow is unchanged: POST /api/auth/login via lib/api.ts's
 * loginWithCredentials wrapped by AuthProvider. Redirects honor?next=.
 */

import { useState, Suspense, type FormEvent, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Mail, Lock, Eye, EyeOff, Layers, Sparkles, Workflow, ShieldCheck } from "lucide-react";
import "@/app/styles/login.css";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function safeNextPath(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/dashboard";
}

/** Compact infinity brand mark used in the login brand bar. */
function BrandMark() {
  return (
    <svg
      viewBox="0 0 48 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="login-brandbar__mark"
      aria-hidden="true"
    >
      <defs>
        <linearGradient
          id="hsdeep-infinity"
          x1="0"
          y1="0"
          x2="48"
          y2="24"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="55%" stopColor="#0ea5e9" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
      </defs>
      <path
        d="M24 12C20 4 8 4 8 12C8 20 20 20 24 12C28 4 40 4 40 12C40 20 28 20 24 12Z"
        stroke="url(#hsdeep-infinity)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * Left brand zone — the React-rendered identity that replaces the branding
 * the background video used to carry (baked-in logo, headline, pillar copy).
 */
function LoginBrandBar() {
  return (
    <header className="login-brandbar">
      <div className="login-brandbar__identity">
        <BrandMark />
        <div>
          <div className="login-brandbar__name">
            HSDEEP CORE <span className="accent">AI</span>
          </div>
          <div className="login-brandbar__label">Enterprise Operations Platform</div>
        </div>
      </div>
      <p className="login-brandbar__subtitle">Autonomous Infrastructure Intelligence</p>
    </header>
  );
}

function LoginForm() {
  const { login, isAuthenticated, isLoading: sessionLoading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams?.get("next") ?? null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForgotNotice, setShowForgotNotice] = useState(false);

  // Already signed in (e.g. dev bootstrap, or navigated here by mistake)?
  // Bounce straight to the destination rather than showing the form.
  useEffect(() => {
    if (!sessionLoading && isAuthenticated) {
      router.replace(next);
    }
  }, [sessionLoading, isAuthenticated, router, next]);

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
      // Surface the REAL cause. The original code collapsed every non-ApiError
      // failure (network TypeError, timeout AbortError, malformed response)
      // into one generic "Unexpected error. Please try again.", which made a
      // diagnosable failure look like an unexplained one.
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
        setError(
          err instanceof Error && err.message
            ? `Sign-in failed: ${err.message}`
            : "Unexpected error. Please try again.",
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
        <div className="login-card">
          <div className="login-card__header">
            <p className="login-card__welcome">Welcome back</p>
            <h2 className="login-card__title">Sign in to your workspace</h2>
          </div>

          {error && (
            <div role="alert" className="login-card__error">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate className="login-form">
            <div className="login-card__field">
              <label htmlFor="email" className="login-card__label">
                EMAIL
              </label>
              <div className="login-card__input-wrap">
                <Mail className="login-card__input-icon" size={18} />
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                  required
                  className="login-card__input"
                  placeholder="Enter your email"
                />
              </div>
            </div>

            <div className="login-card__field">
              <label htmlFor="password" className="login-card__label">
                PASSWORD
              </label>
              <div className="login-card__input-wrap">
                <Lock className="login-card__input-icon" size={18} />
                <input
                  id="password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                  required
                  className="login-card__input"
                  placeholder="Enter your password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="login-card__toggle"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <div className="login-card__options">
              <label className="login-card__checkbox">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                />
                <span>Remember me</span>
              </label>
              <button
                type="button"
                className="login-card__link"
                onClick={() => setShowForgotNotice((v) => !v)}
              >
                Forgot password?
              </button>
            </div>
            {showForgotNotice && (
              <p role="status" className="login-card__notice">
                Self-service password reset is not available yet. Please contact
                your administrator to reset your password.
              </p>
            )}

            <button type="submit" disabled={submitting} className="login-card__submit">
              {submitting ? "Signing in…" : "Sign in"}
            </button>

            <p className="login-card__enterprise">
              <a href="/forgot-password">Forgot password?</a>
            </p>

            <div className="login-card__divider">
              <span className="login-card__divider-text">or</span>
            </div>

            <button type="button" className="login-card__sso" disabled>
              <span>Sign in with SSO</span>
            </button>

            <p className="login-card__enterprise">
              Contact your administrator for SSO access
            </p>
          </form>
        </div>
  );
}

/** Suspense fallback shown while the login form hydrates / fetches session. */
function LoginFallback() {
  return <div className="login-card login-card--loading" />;
}

/** What the platform does — each line describes a shipped capability. */
const PILLARS = [
  { icon: Layers, title: "One view of the estate", text: "Devices, sensors, network and cloud from every connected source." },
  { icon: Sparkles, title: "AI-assisted incidents", text: "Correlates related alerts into incidents with root-cause evidence." },
  { icon: Workflow, title: "Guided remediation", text: "SOP-driven response with approvals and a full audit trail." },
  { icon: ShieldCheck, title: "Built for MSPs", text: "Multi-tenant isolation with role-based access for every team." },
];

function LoginShowcase() {
  return (
    <aside className="lg-show" aria-label="About HSDEEP CORE AI">
      <LoginBrandBar />
      <div>
        <h2>Autonomous infrastructure intelligence for operations teams.</h2>
        <p className="lg-lead">
          See what is failing, why it is failing and what to do next, across every site and tenant you run.
        </p>
        <ul className="lg-pillars">
          {PILLARS.map(({ icon: Icon, title, text }) => (
            <li key={title}>
              <i aria-hidden="true"><Icon size={17} /></i>
              <div>
                <b>{title}</b>
                <span>{text}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
      <footer>© {new Date().getFullYear()} HSDEEP CORE AI · Enterprise Operations Platform</footer>
    </aside>
  );
}

function LoginPage() {
  return (
    <div className="login-page lg">
      <h1 className="sr-only">HSDEEP CORE AI — Autonomous Infrastructure Intelligence — Sign in</h1>
      <div className="login-page__layout">
        <LoginShowcase />
        <main className="lg-form">
          <div className="lg-form__brand"><LoginBrandBar /></div>
          <Suspense fallback={<LoginFallback />}>
            <LoginForm />
          </Suspense>
        </main>
      </div>
    </div>
  );
}

export default LoginPage;
