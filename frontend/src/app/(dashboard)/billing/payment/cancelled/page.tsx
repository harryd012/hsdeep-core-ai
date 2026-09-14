"use client";

/**
 * CHECKOUT CANCELLED — user exited Stripe Checkout without paying.
 *
 * No payment was completed, so NOTHING changes: the existing subscription,
 * plan and entitlements are untouched (the backend was never notified of a
 * successful payment and no webhook fires for an abandoned checkout).
 */

import Link from "next/link";
import { XCircle } from "lucide-react";

export default function PaymentCancelledPage() {
  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel" style={{ maxWidth: 640, margin: "0 auto", width: "100%" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <XCircle size={22} color="var(--amber)" />
            <h2 style={{ color: "#c5f3ff", fontSize: 18, margin: 0 }}>CHECKOUT CANCELLED</h2>
          </div>
          <p style={{ color: "var(--muted)", fontSize: 13 }}>
            No payment was completed.
          </p>
          <p style={{ color: "var(--muted)", fontSize: 13 }}>
            Your current subscription remains unchanged.
          </p>
          <Link href="/usage" className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block", marginTop: 12 }}>
            RETURN TO PLANS
          </Link>
        </section>
      </div>
    </div>
  );
}