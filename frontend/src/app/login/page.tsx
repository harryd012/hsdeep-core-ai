"use client";

/**
 * /login — enterprise SaaS login page for HSDEEP CORE AI.
 *
 * Clean, professional two-column layout:
 * - Left: Branding, headline, capability items
 * - Right: Login form with email, password, SSO, signup link
 *
 * Preserves existing authentication flow (POST /api/auth/login via
 * lib/api.ts's loginWithCredentials, wrapped by AuthProvider).
 */
import { useEffect, useState, Suspense, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Mail, Lock, Eye, EyeOff, Activity, Brain, Zap } from "lucide-react";
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
  const next = safeNextPath(searchParams.get("next"));

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
      if (err instanceof ApiError) {
        if (err.status === 0) {
          setError("Can't reach the server. Check your connection and try again.");
        } else if (err.status === 401 || err.status === 400) {
          setError("Invalid email or password.");
        } else if (err.status === 429) {
          setError("Too many attempts. Please wait a moment and try again.");
        } else {
          setError(err.message || "Sign-in failed. Please try again.");
        }
      } else {
        setError("Unexpected error. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      {/* Left Section - Branding */}
      <div className="login-page__branding">
        <div className="login-page__brand-header">
          <div className="login-page__logo">
            <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" className="login-page__logo-icon">
              <circle cx="20" cy="20" r="18" stroke="currentColor" strokeWidth="1.5" opacity="0.7" />
              <circle cx="20" cy="20" r="12" stroke="currentColor" strokeWidth="1" opacity="0.4" />
              <path d="M12 20h16M20 12v16M14.34 14.34l11.32 11.32M25.66 14.34L14.34 25.66" stroke="currentColor" strokeWidth="1" strokeLinecap="round" opacity="0.5" />
              <circle cx="20" cy="20" r="3" fill="currentColor" opacity="0.9" />
            </svg>
            <span className="login-page__brand-name">HSDEEP CORE AI</span>
          </div>
          <p className="login-page__tagline">AI-Native IT Operations</p>
        </div>

        <div className="login-page__hero">
          <h1 className="login-page__headline">
            Intelligence. Automation.<br />Resilient Operations.
          </h1>
          <p className="login-page__description">
            Unified infrastructure intelligence platform that observes, analyzes,
            and automates your entire IT ecosystem.
          </p>
        </div>

        <div className="login-page__capabilities">
          <div className="login-page__capability">
            <Activity className="login-page__capability-icon" size={20} />
            <div>
              <h3 className="login-page__capability-title">Observe</h3>
              <p className="login-page__capability-desc">Real-time monitoring across all systems</p>
            </div>
          </div>
          <div className="login-page__capability">
            <Brain className="login-page__capability-icon" size={20} />
            <div>
              <h3 className="login-page__capability-title">Analyze</h3>
              <p className="login-page__capability-desc">AI-powered insights and anomaly detection</p>
            </div>
          </div>
          <div className="login-page__capability">
            <Zap className="login-page__capability-icon" size={20} />
            <div>
              <h3 className="login-page__capability-title">Automate</h3>
              <p className="login-page__capability-desc">Intelligent remediation and response</p>
            </div>
          </div>
        </div>
      </div>

      {/* Right Section - Login Form */}
      <div className="login-page__form-section">
        <div className="login-card">
          <div className="login-card__header">
            <h2 className="login-card__welcome">Welcome to</h2>
            <h1 className="login-card__title">HSDEEP CORE AI</h1>
            <p className="login-card__subtitle">Sign in to your enterprise workspace</p>
          </div>

          {error && (
            <div role="alert" className="login-card__error">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate>
            <div className="login-card__field">
              <label htmlFor="email" className="login-card__label">EMAIL</label>
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
              <label htmlFor="password" className="login-card__label">PASSWORD</label>
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
                Self-service password reset isn't available yet. Please contact your
                administrator to reset your password.
              </p>
            )}

            <button type="submit" disabled={submitting} className="login-card__submit">
              {submitting ? "Signing in…" : "SIGN IN →"}
            </button>

            <div className="login-card__divider">
              <span className="login-card__divider-text">or</span>
            </div>

            <button type="button" className="login-card__sso" disabled>
              <span>Sign in with SSO</span>
            </button>

            <p className="login-card__enterprise">
              Contact your administrator for SSO access
            </p>

            <p className="login-card__signup">
              New to HSDEEP CORE AI?{" "}
              <a href="/signup" className="login-card__link">
                Start a free trial
              </a>
            </p>
          </form>
        </div>
      </div>
    </div>
  );

}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen w-full items-center justify-center bg-[#030b10] text-white/50">
          Loading…
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
