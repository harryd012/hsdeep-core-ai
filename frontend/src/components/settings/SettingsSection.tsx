"use client";

import { ReactNode } from "react";

export { SettingsCard } from "./SettingsCard";
export { SaveBar } from "./SaveBar";

export function SettingsSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 20, marginBottom: 16 }}>
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 4px", color: "#c5f3ff", fontSize: 16 }}>{title}</h3>
        {description && <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>{description}</p>}
      </div>
      <div style={{ display: "grid", gap: 16 }}>{children}</div>
    </div>
  );
}