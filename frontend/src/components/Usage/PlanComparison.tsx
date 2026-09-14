"use client";

import React from "react";
import { Plan } from "@/lib/usage-api";

interface Props { plans: Plan[] }

const nf = new Intl.NumberFormat();

function fmtNumber(n: number | null | undefined) {
  if (n === null || n === undefined) return "—";
  return String(n === 0 ? "Unlimited" : nf.format(n));
}

function boolMark(v: any) {
  if (v === true) return "✓";
  if (v === false) return "—";
  return v ?? "—";
}

const rows: { key: string; label: string; extractor: (p: Plan) => string }[] = [
  { key: "seats", label: "Seats", extractor: (p) => fmtNumber(p.seats) },
  { key: "devices", label: "Devices", extractor: (p) => fmtNumber(p.device_limit) },
  { key: "sensors", label: "Sensors", extractor: (p) => fmtNumber(p.sensor_limit) },
  { key: "api_requests", label: "API Requests/mo", extractor: (p) => fmtNumber(p.api_rate_limit) },
  { key: "alerts", label: "Alerts/mo", extractor: (p) => (p.alert_limit ? nf.format(p.alert_limit) : "Not configured") },
  { key: "collector_runs", label: "Collector runs/mo", extractor: (p) => (p.collector_run_limit ? nf.format(p.collector_run_limit) : "Not configured") },
  { key: "sso", label: "SSO", extractor: (p) => boolMark(p.features_json?.sso) },
  { key: "audit", label: "Audit Log", extractor: (p) => boolMark(p.features_json?.audit_log) },
];

export default function PlanComparison({ plans }: Props) {
  if (!plans || plans.length === 0) return null;

  // order by display_order if present
  const sorted = [...plans].sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0));

  return (
    <div style={{ marginTop: 20 }} className="panel">
      <h3 style={{ color: "var(--cyan)", fontSize: 14, margin: "0 0 12px" }}>COMPARE PLANS</h3>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 720 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--muted)" }}>Capability</th>
              {sorted.map((p) => (
                <th key={p.id} style={{ padding: "8px 12px", textAlign: "left", color: "var(--muted)" }}>{p.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{r.label}</td>
                {sorted.map((p) => (
                  <td key={p.id + r.key} style={{ padding: "10px 12px" }}>{r.extractor(p)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
