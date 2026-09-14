"use client";

/**
 * PAYMENT SUCCESS — Stripe Checkout redirect landing state.
 *
 * CRITICAL SECURITY RULE: a Stripe redirect is NOT subscription activation.
 * This page NEVER activates anything client-side. It polls the backend
 * subscription status; only verified Stripe webhook processing
 * (checkout.session.completed / invoice.paid) can move the subscription to
 * active and apply entitlements.
 *
 * States:
 *   pending   -> "PAYMENT SUBMITTED — confirming with Stripe…" (polling)
 *   active    -> "✓ SUBSCRIPTION ACTIVE" (webhook confirmed)
 *   failed    -> "PAYMENT FAILED" (backend reports past_due/incomplete/canceled)
 *   timeout   -> still confirming; user directed to the subscription page
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Clock, XCircle } from "lucide-react";
import { getSubscription, SubscriptionWithPlan } from "@/lib/usage-api";

type Phase = "pending" | "active" | "failed" | "timeout";

const POLL_INTERVAL_MS = 3_000;
const MAX_POLLS = 20; // ~60s of confirmation polling

export default function PaymentSuccessPage() {
  const [phase, setPhase] = useState<Phase>("pending");
  const [sub, setSub] = useState<SubscriptionWithPlan | null>(null);
  const phaseRef = useRef<Phase>("pending");

  function setPhaseTracked(p: Phase) {
    phaseRef.current = p;
    setPhase(p);
  }

  useEffect(() => {
    let stopped = false;
    let polls = 0;

    async function loop() {
      while (!stopped && polls < MAX_POLLS) {
        polls += 1;
        try {
          const s = await getSubscription();
          if (!stopped && s) {
            setSub(s);
            if (s.status === "active") setPhaseTracked("active");
            else if (["past_due", "incomplete", "canceled"].includes(s.status)) {
              setPhaseTracked("failed");
            }
          }
        } catch {
          // Transient network/auth errors: keep polling until the deadline.
        }
        if (stopped || phaseRef.current !== "pending") return;
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
      if (!stopped && phaseRef.current === "pending") setPhaseTracked("timeout");
    }

    void loop();
    return () => { stopped = true; };
  }, []);

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel" style={{ maxWidth: 640, margin: "0 auto", width: "100%" }}>
          {phase === "pending" && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <Clock size={22} color="var(--cyan)" />
                <h2 style={{ color: "#c5f3ff", fontSize: 18, margin: 0 }}>PAYMENT SUBMITTED</h2>
              </div>
              <p style={{ color: "var(--muted)", fontSize: 13 }}>
                Your payment was submitted successfully.
              </p>
              <p style={{ color: "var(--muted)", fontSize: 13 }}>
                We are confirming your subscription with Stripe…
              </p>
              <div style={{
                marginTop: 16, padding: "10px 14px", borderRadius: 8,
                background: "rgba(0,229,255,.06)", border: "1px solid var(--line)",
                color: "var(--cyan)", fontSize: 12,
              }}>
                Waiting for Stripe confirmation… This usually takes a few seconds.
              </div>
            </>
          )}

          {phase === "active" && sub && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <CheckCircle2 size={22} color="var(--green)" />
                <h2 style={{ color: "var(--green)", fontSize: 18, margin: 0 }}>
                  ✓ SUBSCRIPTION ACTIVE
                </h2>
              </div>
              <p style={{ color: "#c5f3ff", fontSize: 15, fontWeight: 700 }}>{sub.plan_name}</p>
              <p style={{ color: "var(--muted)", fontSize: 13 }}>
                Billing confirmed by Stripe ({sub.billing_interval === "year" ? "annual" : "monthly"}).
              </p>
              <Link href="/usage" className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block", marginTop: 8 }}>
                GO TO DASHBOARD
              </Link>
            </>
          )}

          {phase === "failed" && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <XCircle size={22} color="var(--red)" />
                <h2 style={{ color: "var(--red)", fontSize: 18, margin: 0 }}>PAYMENT FAILED</h2>
              </div>
              <p style={{ color: "var(--muted)", fontSize: 13 }}>
                Your subscription has not been activated. Please try again.
              </p>
              <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
                <Link href="/usage" className="btn btn-primary" style={{ textDecoration: "none" }}>
                  TRY AGAIN
                </Link>
                <Link href="/subscription" className="btn btn-secondary" style={{ textDecoration: "none" }}>
                  BACK TO PLANS
                </Link>
              </div>
            </>
          )}

          {phase === "timeout" && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <Clock size={22} color="var(--amber)" />
                <h2 style={{ color: "var(--amber)", fontSize: 18, margin: 0 }}>STILL CONFIRMING…</h2>
              </div>
              <p style={{ color: "var(--muted)", fontSize: 13 }}>
                Stripe is taking longer than usual to confirm this payment. Your
                subscription will activate automatically once the webhook
                confirmation arrives — check the subscription page in a moment.
              </p>
              <Link href="/subscription" className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block", marginTop: 8 }}>
                GO TO SUBSCRIPTION
              </Link>
            </>
          )}
        </section>
      </div>
    </div>
  );
}