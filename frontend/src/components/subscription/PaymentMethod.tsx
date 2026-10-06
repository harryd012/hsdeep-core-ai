"use client";

import { useState } from "react";
import { CreditCard, ExternalLink } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface PaymentMethodProps {
  stripeCustomerId?: string | null;
}

/**
 * Payment method section showing card on file.
 * Opens Stripe Customer Portal via an async fetch, then window.open.
 * Never navigates away directly — the backend returns a JSON { url } redirect.
 */
export default function PaymentMethod({ stripeCustomerId }: PaymentMethodProps) {
  // TODO: fetch full card details from GET /api/billing/payment-method once available
  const hasPaymentMethod = false; // stub until endpoint exists

  const [portalLoading, setPortalLoading] = useState(false);
  const [portalError, setPortalError] = useState<string | null>(null);

  async function openPortal() {
    setPortalLoading(true);
    setPortalError(null);
    try {
      const result = await apiFetch<{ url: string }>("/api/billing/portal");
      window.open(result.url, "_blank", "noopener,noreferrer");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (
        msg.includes("not configured") ||
        msg.includes("404") ||
        msg.includes("501") ||
        msg.includes("503") ||
        msg.includes("400")
      ) {
        setPortalError("Billing portal unavailable — payment provider not configured.");
      } else {
        setPortalError(`Could not open billing portal: ${msg}`);
      }
    } finally {
      setPortalLoading(false);
    }
  }

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
            <button
              onClick={openPortal}
              disabled={portalLoading}
              style={{
                fontSize: 12,
                color: "var(--cyan)",
                background: "transparent",
                border: "none",
                padding: 0,
                cursor: portalLoading ? "wait" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              {portalLoading ? "Opening…" : "Update"} <ExternalLink size={11} />
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <CreditCard size={20} style={{ color: "var(--muted)", opacity: 0.5 }} />
              <span style={{ fontSize: 13, color: "var(--muted)" }}>No payment method on file</span>
            </div>
            <button
              onClick={openPortal}
              disabled={portalLoading}
              style={{
                fontSize: 12,
                color: "var(--cyan)",
                background: "transparent",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid var(--cyan)",
                cursor: portalLoading ? "wait" : "pointer",
              }}
            >
              {portalLoading ? "Opening…" : "Add payment method"} <ExternalLink size={11} />
            </button>
          </div>
        )}
      </div>

      {portalError && (
        <p style={{ fontSize: 11, color: "var(--amber)", marginTop: 6 }}>{portalError}</p>
      )}
    </div>
  );
}
