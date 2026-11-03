"use client";

/* /login — HSDEEP CORE AI enterprise sign-in.
 *
 * Full-bleed brand scene: the "Digital Sphere" backdrop (globe + particle wave,
 * public/branding) with the platform's capabilities orbiting the globe, the
 * brand and operating pillars on the left, and a glass sign-in card on the
 * right. Every capability named here is a shipped module.
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

import { useState, Suspense, type FormEvent, useEffect, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  ArrowRight,
  BarChart3,
  BrainCircuit,
  Building2,
  Crosshair,
  Eye,
  EyeOff,
  Lock,
  Network,
  Rocket,
  Settings,
  ShieldCheck,
  Siren,
  User,
  Workflow,
} from "lucide-react";
import "@/app/styles/login.css";
import AuthBackdrop from "@/components/auth/AuthBackdrop";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function safeNextPath(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/dashboard";
}

/** False on the server and during hydration, true once React runs in the browser. */
function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/** Infinity brand mark. */
function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="hsdeep-infinity" x1="0" y1="0" x2="48" y2="24" gradientUnits="userSpaceOnUse">
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

function Brand() {
  return (
    <div className="lx-brand">
      <BrandMark className="lx-brand__mark" />
      <span className="lx-brand__rule" aria-hidden="true" />
      <div>
        <div className="lx-brand__name">
          HSDEEP CORE <span>AI</span>
        </div>
        <div className="lx-brand__tag">AI-Native IT Operations</div>
      </div>
    </div>
  );
}

/** The operating loop, left to right. */
const PILLARS = [
  { icon: Crosshair, title: "Observe", text: "Unified visibility" },
  { icon: BarChart3, title: "Analyze", text: "AI-driven insight" },
  { icon: Settings, title: "Automate", text: "Runbooks & SOPs" },
  { icon: Siren, title: "Respond", text: "Faster resolution" },
  { icon: Rocket, title: "Scale", text: "Multi-tenant ready" },
];

/** Capabilities orbiting the globe (positions are % of the backdrop stage). */
const ORBIT = [
  { icon: Activity, label: ["Unified", "observability"], side: "left", x: 37.5, y: 22 },
  { icon: BrainCircuit, label: ["AI root-cause", "analysis"], side: "left", x: 33.5, y: 46 },
  { icon: Siren, label: ["Real-time", "alerting"], side: "left", x: 37, y: 70 },
  { icon: Network, label: ["Topology &", "dependency maps"], side: "right", x: 59.5, y: 22 },
  { icon: Workflow, label: ["Runbook", "automation"], side: "right", x: 62.5, y: 46 },
  { icon: ShieldCheck, label: ["Tenant-isolated", "security"], side: "right", x: 59.5, y: 70 },
] as const;

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
    <div className="lx">
      <h1 className="lx-sr-only">HSDEEP CORE AI — AI-Native IT Operations — Sign in</h1>

      {/* Backdrop stage: sized like object-fit: cover, so the orbit nodes stay on the globe at any window size. */}
      <div className="lx-stage" aria-hidden="true">
        <AuthBackdrop />
        <ul className="lx-orbit">
          {ORBIT.map(({ icon: Icon, label, side, x, y }) => (
            <li key={label.join(" ")} className={`lx-node lx-node--${side}`} style={{ left: `${x}%`, top: `${y}%` }}>
              <span className="lx-node__ring">
                <Icon size={22} />
              </span>
              <span className="lx-node__label">
                {label[0]}
                <br />
                {label[1]}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="lx-scrim" aria-hidden="true" />

      <div className="lx-layout">
        <header className="lx-top">
          <Brand />
          <p className="lx-top__claims">
            Secure <i>|</i> Scalable <i>|</i> Intelligent
          </p>
        </header>

        <div className="lx-hero">
          <h2 className="lx-hero__title">
            Intelligence.
            <br />
            Automation.
            <br />
            <span>Resilient Operations.</span>
          </h2>
          <span className="lx-hero__rule" aria-hidden="true" />
          <ul className="lx-pillars">
            {PILLARS.map(({ icon: Icon, title, text }) => (
              <li key={title}>
                <Icon size={24} aria-hidden="true" />
                <b>{title}</b>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>

        <main className="lx-main">
          <Suspense fallback={<LoginFallback />}>
            <LoginForm />
          </Suspense>
        </main>

        <footer className="lx-foot">
          <span className="lx-foot__built">Built for what&apos;s next</span>
          <span className="lx-foot__tagline">Enterprise IT. Intelligently operated.</span>
          <span className="lx-foot__copy">© {new Date().getFullYear()} HSDEEP CORE AI</span>
        </footer>
      </div>
    </div>
  );
}

export default LoginPage;
