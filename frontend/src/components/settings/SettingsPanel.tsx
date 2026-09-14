"use client";

import type { SettingsSection } from "@/lib/settingsSections";
import { SETTINGS_SECTIONS } from "@/lib/settingsSections";

export interface SettingsPanelProps {
  /**
   * Sections to render as navigation cards. When omitted, the panel falls
   * back to the full `SETTINGS_SECTIONS` config (still a real data source,
   * not an inline mock).
   */
  sections?: SettingsSection[];
  /**
   * Callback fired when a card is clicked. Receives the clicked section so
   * the caller can navigate to `section.href` (e.g. via the Next.js router).
   */
  onNavigate: (section: SettingsSection) => void;
}

export default function SettingsPanel({
  sections,
  onNavigate,
}: SettingsPanelProps) {
  const visible = sections ?? SETTINGS_SECTIONS;

  if (visible.length === 0) {
    return (
      <div
        style={{
          border: "1px dashed var(--line)",
          borderRadius: 8,
          padding: 32,
          textAlign: "center",
          color: "var(--muted)",
        }}
      >
        No settings sections are available for your role.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
        gap: 16,
        minHeight: 200,
        alignContent: "center",
      }}
    >
      {visible.map((section) => {
        const Icon = section.icon;
        return (
          <button
            key={section.id}
            type="button"
            aria-label={`Navigate to ${section.title} settings`}
            onClick={() => onNavigate(section)}
            style={{
              textAlign: "center",
              padding: 20,
              border: "1px solid var(--line)",
              borderRadius: 8,
              cursor: "pointer",
              background: "transparent",
              color: "inherit",
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              const t = e.currentTarget as HTMLElement;
              t.style.borderColor = "var(--cyan)";
              t.style.boxShadow = "0 0 12px rgba(0,217,255,0.1)";
            }}
            onMouseLeave={(e) => {
              const t = e.currentTarget as HTMLElement;
              t.style.borderColor = "var(--line)";
              t.style.boxShadow = "none";
            }}
          >
            <Icon
              size={32}
              style={{ color: "var(--cyan)", margin: "0 auto 12px" }}
            />
            <b
              style={{
                display: "block",
                color: "#c5f3ff",
                marginBottom: 4,
                fontSize: 14,
              }}
            >
              {section.title}
            </b>
            <p
              style={{ color: "var(--muted)", fontSize: 12, margin: 0 }}
            >
              {section.description}
            </p>
          </button>
        );
      })}
    </div>
  );
}
