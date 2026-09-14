"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { SettingsSection, SettingsCard, SaveBar } from "@/components/settings/SettingsSection";
import SettingsBackButton from "@/components/settings/SettingsBackButton";
import { useAuth } from "@/lib/auth-context";
import {
  getSecuritySettings,
  updateSecuritySettings,
  SecuritySettings,
} from "@/lib/api";

export default function SecuritySettingsPage() {
  const { tenantId } = useAuth();
  const [data, setData] = useState<SecuritySettings | null>(null);
  const [draft, setDraft] = useState<Record<string, string | number | boolean | null>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSecuritySettings(tenantId)
      .then((row) => {
        if (!cancelled) {
          setData(row);
          setDraft({
            min_password_length: row.min_password_length,
            require_uppercase: row.require_uppercase,
            require_numbers: row.require_numbers,
            require_special_chars: row.require_special_chars,
            mfa_enabled: row.mfa_enabled,
            session_timeout_minutes: row.session_timeout_minutes,
            max_login_attempts: row.max_login_attempts,
          });
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load security settings");
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
    getSecuritySettings(tenantId)
      .then((row) => {
        setData(row);
        setDraft({
          min_password_length: row.min_password_length,
          require_uppercase: row.require_uppercase,
          require_numbers: row.require_numbers,
          require_special_chars: row.require_special_chars,
          mfa_enabled: row.mfa_enabled,
          session_timeout_minutes: row.session_timeout_minutes,
          max_login_attempts: row.max_login_attempts,
        });
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Unable to load security settings");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  const hasChanges = data
    ? (draft.min_password_length ?? 0) !== data.min_password_length
      || (draft.require_uppercase ?? false) !== data.require_uppercase
      || (draft.require_numbers ?? false) !== data.require_numbers
      || (draft.require_special_chars ?? false) !== data.require_special_chars
      || (draft.mfa_enabled ?? false) !== data.mfa_enabled
      || (draft.session_timeout_minutes ?? 0) !== data.session_timeout_minutes
      || (draft.max_login_attempts ?? 0) !== data.max_login_attempts
    : false;

  async function onSave() {
    setSaving(true);
    setSuccess(null);
    try {
      const payload = {
        min_password_length: Number(draft.min_password_length) || 8,
        require_uppercase: Boolean(draft.require_uppercase),
        require_numbers: Boolean(draft.require_numbers),
        require_special_chars: Boolean(draft.require_special_chars),
        mfa_enabled: Boolean(draft.mfa_enabled),
        session_timeout_minutes: Number(draft.session_timeout_minutes) || 60,
        max_login_attempts: Number(draft.max_login_attempts) || 5,
      };
      const updated = await updateSecuritySettings(tenantId, payload);
      setData(updated);
      setDraft({
        min_password_length: updated.min_password_length,
        require_uppercase: updated.require_uppercase,
        require_numbers: updated.require_numbers,
        require_special_chars: updated.require_special_chars,
        mfa_enabled: updated.mfa_enabled,
        session_timeout_minutes: updated.session_timeout_minutes,
        max_login_attempts: updated.max_login_attempts,
      });
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
            <h2>SECURITY SETTINGS</h2>
          </div>

          {loading && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--muted)" }}>
              <ShieldCheck size={32} style={{ margin: "0 auto 12px", color: "var(--cyan)" }} />
              <p>Loading security settings…</p>
            </div>
          )}

          {!loading && error && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--amber)" }}>
              <p>{error}</p>
              <button onClick={handleRetry} style={{ marginTop: 12, background: "var(--cyan)", color: "#001a1f", border: "none", borderRadius: 6, padding: "8px 16px", cursor: "pointer" }}>
                Retry
              </button>
            </div>
          )}

          {!loading && !error && data && (
            <div>
              <SettingsSection title="Password Policy" description="Validation rules for user passwords">
                <SettingsCard title="Minimum Password Length" type="number" value={String(draft.min_password_length ?? "")} onChange={(v) => setDraft((d) => ({ ...d, min_password_length: v === "" ? 0 : Number(v) }))} />
                <SettingsCard title="Require Uppercase" type="checkbox" value={draft.require_uppercase ? "true" : "false"} onChange={(v) => setDraft((d) => ({ ...d, require_uppercase: v === "true" }))} />
                <SettingsCard title="Require Numbers" type="checkbox" value={draft.require_numbers ? "true" : "false"} onChange={(v) => setDraft((d) => ({ ...d, require_numbers: v === "true" }))} />
                <SettingsCard title="Require Special Characters" type="checkbox" value={draft.require_special_chars ? "true" : "false"} onChange={(v) => setDraft((d) => ({ ...d, require_special_chars: v === "true" }))} />
              </SettingsSection>
              <SettingsSection title="Session & Login Protection" description="MFA and login attempt limits">
                <SettingsCard title="MFA Enabled" type="checkbox" value={draft.mfa_enabled ? "true" : "false"} onChange={(v) => setDraft((d) => ({ ...d, mfa_enabled: v === "true" }))} />
                <SettingsCard title="Session Timeout Minutes" type="number" value={String(draft.session_timeout_minutes ?? "")} onChange={(v) => setDraft((d) => ({ ...d, session_timeout_minutes: v === "" ? 0 : Number(v) }))} />
                <SettingsCard title="Max Login Attempts" type="number" value={String(draft.max_login_attempts ?? "")} onChange={(v) => setDraft((d) => ({ ...d, max_login_attempts: v === "" ? 0 : Number(v) }))} />
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
