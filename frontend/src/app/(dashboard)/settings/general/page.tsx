"use client";

import { useEffect, useState } from "react";
import { Settings } from "lucide-react";
import { SettingsSection, SettingsCard, SaveBar } from "@/components/settings/SettingsSection";
import SettingsBackButton from "@/components/settings/SettingsBackButton";
import { useAuth } from "@/lib/auth-context";
import {
  getGeneralSettings,
  updateGeneralSettings,
  GeneralSettings,
} from "@/lib/api";

export default function GeneralSettingsPage() {
  const { tenantId } = useAuth();
  const [data, setData] = useState<GeneralSettings | null>(null);
  const [draft, setDraft] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGeneralSettings(tenantId)
      .then((row) => {
        if (!cancelled) {
          setData(row);
          setDraft({
            platform_name: row.platform_name,
            timezone: row.timezone,
            branding_logo_url: row.branding_logo_url ?? "",
            primary_color: row.primary_color ?? "",
            locale: row.locale,
          } as Record<string, string | null>);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load settings");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  function handleRetry() {
    setLoading(true);
    setError(null);
    getGeneralSettings(tenantId)
      .then((row) => {
        setData(row);
        setDraft({
          platform_name: row.platform_name,
          timezone: row.timezone,
          branding_logo_url: row.branding_logo_url ?? "",
          primary_color: row.primary_color ?? "",
          locale: row.locale,
        } as Record<string, string | null>);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Unable to load settings");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  const hasChanges = data
    ? (draft.platform_name ?? "") !== data.platform_name
      || (draft.timezone ?? "") !== data.timezone
      || (draft.branding_logo_url ?? "") !== (data.branding_logo_url ?? "")
      || (draft.primary_color ?? "") !== (data.primary_color ?? "")
      || (draft.locale ?? "") !== data.locale
    : false;

  async function onSave() {
    setSaving(true);
    setSuccess(null);
    try {
      const payload = {
        platform_name: draft.platform_name ?? "",
        timezone: draft.timezone ?? "",
        branding_logo_url: draft.branding_logo_url === "" ? null : draft.branding_logo_url,
        primary_color: draft.primary_color === "" ? null : draft.primary_color,
        locale: draft.locale ?? "",
      };
      const updated = await updateGeneralSettings(tenantId, payload);
      setData(updated);
      setDraft({
        platform_name: updated.platform_name,
        timezone: updated.timezone,
        branding_logo_url: updated.branding_logo_url ?? "",
        primary_color: updated.primary_color ?? "",
        locale: updated.locale,
      } as Record<string, string | null>);
      setSuccess("Settings saved successfully");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <SettingsBackButton />
            <h2>GENERAL SETTINGS</h2>
          </div>

          {loading && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--muted)" }}>
              <Settings size={32} style={{ margin: "0 auto 12px", color: "var(--cyan)" }} />
              <p>Loading general settings…</p>
            </div>
          )}

          {!loading && error && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--amber)" }}>
              <p>{error}</p>
              <button
                onClick={handleRetry}
                style={{ marginTop: 12, background: "var(--cyan)", color: "#001a1f", border: "none", borderRadius: 6, padding: "8px 16px", cursor: "pointer" }}
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && data && (
            <div>
              <SettingsSection title="Platform Identity" description="Platform name and localization">
                <SettingsCard title="Platform Name" value={draft.platform_name ?? ""} onChange={(v) => setDraft((d) => ({ ...d, platform_name: v } as Record<string, string | null>))} />
                <SettingsCard title="Timezone" value={draft.timezone ?? ""} onChange={(v) => setDraft((d) => ({ ...d, timezone: v } as Record<string, string | null>))} />
                <SettingsCard title="Locale" value={draft.locale ?? ""} onChange={(v) => setDraft((d) => ({ ...d, locale: v } as Record<string, string | null>))} />
              </SettingsSection>
              <SettingsSection title="Branding" description="Logo URL and theme color">
                <SettingsCard title="Logo URL" value={draft.branding_logo_url ?? ""} onChange={(v) => setDraft((d) => ({ ...d, branding_logo_url: v } as Record<string, string | null>))} />
                <SettingsCard title="Primary Color" value={draft.primary_color ?? ""} onChange={(v) => setDraft((d) => ({ ...d, primary_color: v } as Record<string, string | null>))} />
              </SettingsSection>
              {success && (
                <div style={{ color: "#0f8", fontSize: 12, marginTop: 8 }}>{success}</div>
              )}
            </div>
          )}
        </section>
      </div>

      <SaveBar onSave={onSave} saving={saving} disabled={loading || !hasChanges} hasChanges={hasChanges} />
    </div>
  );
}