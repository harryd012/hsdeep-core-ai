"use client";

/** Confirms the email address from the link, once, on load. */
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api";
import { accountErrorMessage, verifyEmail } from "@/lib/account";
import { AccountCard, BUTTON_CLASS } from "@/components/auth/AccountCard";

function VerifyEmail() {
  const token = useSearchParams()?.get("token") ?? "";
  const started = useRef(false);
  const [state, setState] = useState<{ error: string | null; notice: string | null }>({
    error: token ? null : "This link is missing its token. Open the link from your email again.",
    notice: null,
  });

  useEffect(() => {
    // Tokens are single use: never send twice (React strict mode re-runs effects).
    if (!token || started.current) return;
    started.current = true;
    verifyEmail(token)
      .then((res) => setState({ error: null, notice: res.detail }))
      .catch((err) =>
        setState({
          error: err instanceof ApiError ? accountErrorMessage(err.status, err.message) : "Something went wrong.",
          notice: null,
        }),
      );
  }, [token]);

  const pending = token && !state.error && !state.notice;
  return (
    <AccountCard subtitle="Confirm your email" error={state.error} notice={pending ? "Confirming…" : state.notice}>
      {state.notice && (
        <a href="/login" className={`${BUTTON_CLASS} block text-center`}>
          Sign in
        </a>
      )}
    </AccountCard>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <VerifyEmail />
    </Suspense>
  );
}
