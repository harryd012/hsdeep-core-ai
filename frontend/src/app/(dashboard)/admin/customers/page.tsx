"use client";

// Platform-admin customer management (internal ops view).
// Gated server-side by GET /api/billing/admin/customers (superuser only);
// this page additionally hides itself for non-superusers. Read-only this
// sprint - no plan/status mutation controls are rendered because wiring
// them to real actions is explicitly out of scope here.

import { useEffect, useState } from "react";
import Link from "next/link";

type Customer = {
  tenant_id: string;
  name: string;
  slug: string;
  plan_name: string | null;
  plan_tier: string | null;
  subscription_status: string;
  trial_ends_at: string | null;
  over_limit_blocked: boolean;
  created_at: string | null;
  last_activity_at: string | null;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function AdminCustomersPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const meRes = await fetch(`${API}/api/auth/me`, { credentials: "include" });
        if (!meRes.ok) {
          setAllowed(false);
          return;
        }
        const me = await meRes.json();
        if (!me.is_superuser) {
          setAllowed(false);
          return;
        }
        setAllowed(true);
        const res = await fetch(`${API}/api/billing/admin/customers`, {
          credentials: "include",
        });
        if (!res.ok) {
          setError(`Failed to load customers (${res.status})`);
          return;
        }
        setCustomers(await res.json());
      } catch {
        setError("Failed to reach the backend");
      }
    })();
  }, []);

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>PLATFORM ADMIN · CUSTOMERS</h2>
          {allowed === false && (
            <p style={{ color: "#a7bec7" }}>
              Platform-admin access required. <Link href="/dashboard">Back to dashboard</Link>
            </p>
          )}
          {error && <p style={{ color: "#ff6b6b" }}>{error}</p>}
          {allowed && customers && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--cyan)" }}>
                  <th style={th}>Customer</th>
                  <th style={th}>Plan</th>
                  <th style={th}>Status</th>
                  <th style={th}>Created</th>
                  <th style={th}>Last activity</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c) => (
                  <tr key={c.tenant_id} style={{ borderTop: "1px solid var(--line)" }}>
                    <td style={td}>
                      {c.name}
                      <span style={{ color: "var(--muted)" }}> ({c.slug})</span>
                    </td>
                    <td style={td}>{c.plan_name ?? "-"}</td>
                    <td style={td}>
                      {c.subscription_status}
                      {c.over_limit_blocked ? " · over-limit" : ""}
                    </td>
                    <td style={td}>{fmt(c.created_at)}</td>
                    <td style={td}>{fmt(c.last_activity_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "8px 10px" };
const td: React.CSSProperties = { padding: "8px 10px", color: "#a7bec7" };

function fmt(iso: string | null): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}