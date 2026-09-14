"use client";

export function SettingsCard({ title, value, onChange, type = "text", placeholder, disabled }: {
  title: string;
  value?: string | boolean | null;
  onChange?: (value: string | null) => void;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  if (type === "checkbox") {
    const checked = value === true || value === "true";
    return (
      <div style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange?.(e.target.checked ? "true" : "false")}
          disabled={disabled}
          style={{
            accentColor: "var(--cyan)",
            width: 16,
            height: 16,
            cursor: disabled ? "not-allowed" : "pointer",
            opacity: disabled ? 0.6 : 1,
          }}
        />
        <label style={{ color: "#9dc5d4", fontSize: 12, fontWeight: 500, cursor: disabled ? "not-allowed" : "pointer" }}>{title}</label>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label style={{ color: "#9dc5d4", fontSize: 12, fontWeight: 500 }}>{title}</label>
      <input
        type={type}
        value={(value ?? "") as string}
        onChange={(e) => onChange?.(e.target.value === "" ? null : e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        style={{
          background: "rgba(0,0,0,0.35)",
          border: "1px solid var(--line)",
          borderRadius: 6,
          color: "#e8f8ff",
          padding: "10px 12px",
          fontSize: 14,
          outline: "none",
          opacity: disabled ? 0.6 : 1,
        }}
      />
    </div>
  );
}
