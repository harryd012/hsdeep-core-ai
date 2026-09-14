"use client";

import { Database, FileText, Download } from "lucide-react";

export default function ReportsPage() {
  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <h2>REPORTS</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, minHeight: 200, alignContent: "center" }}>
            {[
              { title: "Infrastructure Summary", desc: "Sites, devices, and sensor inventory report" },
              { title: "Alert Analysis", desc: "Alert trends, severity breakdown, and MTTR" },
              { title: "Collector Performance", desc: "Sync success rates, duration, and lag" },
              { title: "SOP Execution Log", desc: "Automated procedure run history" },
            ].map((r) => (
              <div key={r.title} style={{ textAlign: "center", padding: 20, border: "1px solid var(--line)", borderRadius: 8 }}>
                <FileText size={32} style={{ color: "var(--cyan)", margin: "0 auto 12px" }} />
                <b style={{ display: "block", color: "#c5f3ff", marginBottom: 4, fontSize: 14 }}>{r.title}</b>
                <p style={{ color: "var(--muted)", fontSize: 12, margin: "4px 0 12px" }}>{r.desc}</p>
                <button style={{ border: "1px solid var(--line)", background: "rgba(1,10,17,0.8)", color: "var(--cyan)", padding: "8px 16px", cursor: "pointer", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <Download size={14} /> Generate
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}