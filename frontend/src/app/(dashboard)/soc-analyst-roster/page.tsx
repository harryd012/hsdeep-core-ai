"use client";

import { ShieldCheck } from "lucide-react";
import SocAnalystRoster from "@/components/noc/SocAnalystRoster";

export default function SocAnalystRosterPage() {
  return (
    <div style={{ padding: "16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <ShieldCheck size={18} style={{ color: "var(--cyan)" }} />
        <h1 style={{ margin: 0, fontSize: 18, color: "#ccecf4", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          SOC Analyst Roster
        </h1>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>· real backend state · polled every 5s</span>
      </div>
      <SocAnalystRoster />
    </div>
  );
}
