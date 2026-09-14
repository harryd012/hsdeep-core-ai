"use client";

import { CreditCard, ExternalLink } from "lucide-react";

interface PaymentMethodProps {
  stripeCustomerId?: string | null;
}

/**
 * Payment method section showing card on file.
 * Links to Stripe Customer Portal for updates.
 */
export default function PaymentMethod({ stripeCustomerId }: PaymentMethodProps) {
  // TODO: fetch full card details from GET /api/billing/payment-method once available
  const hasPaymentMethod = false; // stub until endpoint exists

  return (
    <div style={{ marginTop: 24 }}>
      <h3 style={{ color: "var(--cyan)", fontSize: 14, fontWeight: 700, margin: "0 0 12px", textTransform: "uppercase", letterSpacing: 0.5 }}>
        PAYMENT METHOD
      </h3>

      <div style={{
        padding: 16,
        borderRadius: 8,
        border: "1px solid var(--line)",
        background: "rgba(7, 19, 27, 0.4)",
      }}>
        {hasPaymentMethod ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <CreditCard size={20} style={{ color: "var(--muted)" }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                  •••• •••• •••• 4242
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>Expires 12/2027</div>
              </div>
            </div>
            <a
              href="/api/billing/portal"
              style={{ fontSize: 12, color: "var(--cyan)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              Update <ExternalLink size={11} />
            </a>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <CreditCard size={20} style={{ color: "var(--muted)", opacity: 0.5 }} />
              <span style={{ fontSize: 13, color: "var(--muted)" }}>No payment method on file</span>
            </div>
            <a
              href="/api/billing/portal"
              style={{ fontSize: 12, color: "var(--cyan)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4, padding: "6px 12px", borderRadius: 6, border: "1px solid var(--cyan)" }}
            >
              Add payment method <ExternalLink size={11} />
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
