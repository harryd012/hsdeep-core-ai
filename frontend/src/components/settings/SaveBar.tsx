"use client";

import { useState } from "react";

export function SaveBar({ onSave, saving, disabled, hasChanges }: {
  onSave: () => void;
  saving: boolean;
  disabled: boolean;
  hasChanges: boolean;
}) {
  const [copied, setCopied] = useState(false);

  if (!hasChanges) return null;

  return (
    <div style={{
      position: "fixed",
      bottom: 20,
      right: 20,
      background: "rgba(10,20,25,0.95)",
      border: "1px solid var(--cyan)",
      borderRadius: 10,
      padding: 12,
      display: "flex",
      gap: 10,
      alignItems: "center",
      boxShadow: "0 0 20px rgba(0,255,255,0.15)",
      zIndex: 50,
    }}>
      <span style={{ color: "#c5f3ff", fontSize: 12 }}>You have unsaved changes</span>
      <button
        onClick={onSave}
        disabled={saving || disabled}
        style={{
          background: copied ? "#0f8" : "var(--cyan)",
          color: "#001a1f",
          border: "none",
          borderRadius: 6,
          padding: "8px 16px",
          fontSize: 12,
          fontWeight: 600,
          cursor: saving || disabled ? "not-allowed" : "pointer",
          opacity: saving || disabled ? 0.6 : 1,
        }}
      >
        {saving ? "Saving..." : "Save"}
      </button>
    </div>
  );
}