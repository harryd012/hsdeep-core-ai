"use client";

import { useState, useEffect } from "react";
import { FileText, Download, AlertCircle } from "lucide-react";

interface Invoice {
  id: string;
  date: string;
  amount: number;
  status: "paid" | "failed" | "pending";
  pdfUrl: string | null;
}

/**
 * Billing History table showing invoice history from Stripe.
 * Falls back to empty state if no data or endpoint unavailable.
 */
export default function BillingHistory() {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    // TODO: wire to GET /api/billing/invoices once backend endpoint exists
    // For now, show empty state
    setLoading(false);
  }, []);

  const statusBadge = (status: Invoice["status"]) => {
    const colors: Record<string, { bg: string; text: string }> = {
      paid: { bg: "rgba(0,211,141,0.12)", text: "var(--green)" },
      failed: { bg: "rgba(255,77,79,0.12)", text: "var(--red)" },
      pending: { bg: "rgba(255,178,0,0.12)", text: "var(--amber)" },
    };
    const c = colors[status] ?? colors.pending;
    return (
      <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 8px", borderRadius: 4, background: c.bg, color: c.text }}>
        {status.toUpperCase()}
      </span>
    );
  };

  if (loading) {
    return <p style={{ fontSize: 12, color: "var(--muted)" }}>Loading billing history…</p>;
  }

  return (
    <div style={{ marginTop: 24 }}>
      <h3 style={{ color: "var(--cyan)", fontSize: 14, fontWeight: 700, margin: "0 0 12px", textTransform: "uppercase", letterSpacing: 0.5 }}>
        BILLING HISTORY
      </h3>

      {error ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 12, borderRadius: 8, background: "rgba(255,77,79,0.08)", border: "1px solid rgba(255,77,79,0.2)" }}>
          <AlertCircle size={14} style={{ color: "var(--red)" }} />
          <span style={{ fontSize: 12, color: "var(--red)" }}>Unable to load billing history.</span>
        </div>
      ) : invoices.length === 0 ? (
        <div style={{ textAlign: "center", padding: "32px 16px", borderRadius: 8, border: "1px dashed var(--line)" }}>
          <FileText size={24} style={{ color: "var(--muted)", marginBottom: 8 }} />
          <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>No billing history yet</p>
          <p style={{ fontSize: 11, color: "var(--muted)", margin: "4px 0 0", opacity: 0.7 }}>
            Invoices will appear here once your first payment is processed.
          </p>
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)" }}>
                <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--muted)", fontWeight: 600 }}>Date</th>
                <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--muted)", fontWeight: 600 }}>Amount</th>
                <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--muted)", fontWeight: 600 }}>Status</th>
                <th style={{ textAlign: "right", padding: "8px 12px", color: "var(--muted)", fontWeight: 600 }}>Receipt</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                  <td style={{ padding: "8px 12px", color: "var(--text)" }}>{new Date(inv.date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</td>
                  <td style={{ padding: "8px 12px", color: "var(--text)", fontWeight: 600 }}>${(inv.amount / 100).toFixed(2)}</td>
                  <td style={{ padding: "8px 12px" }}>{statusBadge(inv.status)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right" }}>
                    {inv.pdfUrl ? (
                      <a href={inv.pdfUrl} target="_blank" rel="noopener noreferrer" style={{ color: "var(--cyan)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <Download size={12} /> PDF
                      </a>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
