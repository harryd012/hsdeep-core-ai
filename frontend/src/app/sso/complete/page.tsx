"use client";

/**
 * /sso/complete — where single sign-on lands (security programme S6). The
 * backend sends the browser here with a one-time code (60 s) or an error; the
 * code is exchanged for a session at once and removed from the address bar.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import AuthScene from "@/components/auth/AuthScene";
import { useAuth } from "@/lib/auth-context";

function safeNext(raw: string | null): string {
  return raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : "/dashboard";
}

function Complete() {
  const params = useSearchParams();
  const router = useRouter();
  const { completeSso } = useAuth();
  const started = useRef(false);
  const code = params?.get("code") ?? "";
  const next = safeNext(params?.get("next") ?? null);
  const [error, setError] = useState<string | null>(params?.get("error") ?? (code ? null : "The sign-in did not return a code."));

  useEffect(() => {
    if (!code || started.current) return;
    started.current = true; // the code works once; strict mode re-runs effects
    window.history.replaceState(null, "", "/sso/complete");
    completeSso(code)
      .then(() => router.replace(next))
      .catch((e) => setError(e instanceof Error ? e.message : "The sign-in could not be completed."));
  }, [code, completeSso, next, router]);

  return (
    <section className="lx-card" aria-live="polite">
      <p className="lx-card__eyebrow">Single sign-on</p>
      <h2 className="lx-card__title lx-card__title--sm">{error ? "Sign-in didn't complete" : "Signing you in…"}</h2>
      {error ? (
        <>
          <div role="alert" className="lx-card__error" style={{ marginTop: 12 }}>{error}</div>
          <a className="lx-submit lx-submit--block" href="/login">Back to sign in</a>
        </>
      ) : (
        <p className="lx-card__lead">Your identity provider confirmed who you are. One moment.</p>
      )}
    </section>
  );
}

export default function SsoCompletePage() {
  return (
    <AuthScene title="HSDEEP CORE AI — Single sign-on">
      <Suspense fallback={null}>
        <Complete />
      </Suspense>
    </AuthScene>
  );
}
