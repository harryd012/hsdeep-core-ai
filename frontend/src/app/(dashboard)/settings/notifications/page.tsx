"use client";

import { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { SettingsSection, SettingsCard, SaveBar } from "@/components/settings/SettingsSection";
import SettingsBackButton from "@/components/settings/SettingsBackButton";
import { useAuth } from "@/lib/auth-context";
import {
  getNotificationSettings,
  updateNotificationSettings,
  NotificationSettings,
} from "@/lib/api";

export default function NotificationsSettingsPage() {
  const { tenantId } = useAuth();
  const [data, setData] = useState<NotificationSettings | null>(null);
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailRecipients, setEmailRecipients] = useState<string | null>(null);
  const [slackEnabled, setSlackEnabled] = useState<boolean>(false);
  const [slackWebhook, setSlackWebhook] = useState<string | null>(null);
  const [slackChannel, setSlackChannel] = useState<string | null>(null);
  const [pagerdutyEnabled, setPagerdutyEnabled] = useState<boolean>(false);
  const [pagerdutyKey, setPagerdutyKey] = useState<string | null>(null);
  const [pagerdutySeverity, setPagerdutySeverity] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNotificationSettings(tenantId)
      .then((row) => {
        if (!cancelled) {
          setData(row);
          setEmailEnabled(Boolean(row.email_enabled));
          setEmailRecipients(row.email_recipients ?? "");
          setSlackEnabled(Boolean(row.slack_enabled));
          setSlackWebhook(row.slack_webhook_url ?? "");
          setSlackChannel(row.slack_channel ?? "");
          setPagerdutyEnabled(Boolean(row.pagerduty_enabled));
          setPagerdutyKey(row.pagerduty_integration_key ?? "");
          setPagerdutySeverity(row.pagerduty_severity ?? "");
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load notification settings");
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
    getNotificationSettings(tenantId)
      .then((row) => {
        setData(row);
        setEmailEnabled(Boolean(row.email_enabled));
        setEmailRecipients(row.email_recipients ?? "");
        setSlackEnabled(Boolean(row.slack_enabled));
        setSlackWebhook(row.slack_webhook_url ?? "");
        setSlackChannel(row.slack_channel ?? "");
        setPagerdutyEnabled(Boolean(row.pagerduty_enabled));
        setPagerdutyKey(row.pagerduty_integration_key ?? "");
        setPagerdutySeverity(row.pagerduty_severity ?? "");
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Unable to load notification settings");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  const hasChanges = data
    ? emailEnabled !== data.email_enabled
      || emailRecipients !== (data.email_recipients ?? "")
      || slackEnabled !== data.slack_enabled
      || slackWebhook !== (data.slack_webhook_url ?? "")
      || slackChannel !== (data.slack_channel ?? "")
      || pagerdutyEnabled !== data.pagerduty_enabled
      || pagerdutyKey !== (data.pagerduty_integration_key ?? "")
      || pagerdutySeverity !== (data.pagerduty_severity ?? "")
    : false;

  async function onSave() {
    setSaving(true);
    setSuccess(null);
    try {
      const payload = {
        email_enabled: emailEnabled,
        email_recipients: emailRecipients === "" ? null : emailRecipients,
        slack_enabled: slackEnabled,
        slack_webhook_url: slackWebhook === "" ? null : slackWebhook,
        slack_channel: slackChannel === "" ? null : slackChannel,
        pagerduty_enabled: pagerdutyEnabled,
        pagerduty_integration_key: pagerdutyKey === "" ? null : pagerdutyKey,
        pagerduty_severity: pagerdutySeverity === "" ? null : pagerdutySeverity,
      };
      const updated = await updateNotificationSettings(tenantId, payload);
      setData(updated);
      setEmailEnabled(updated.email_enabled);
      setEmailRecipients(updated.email_recipients ?? "");
      setSlackEnabled(Boolean(updated.slack_enabled));
      setSlackWebhook(updated.slack_webhook_url ?? "");
      setSlackChannel(updated.slack_channel ?? "");
      setPagerdutyEnabled(Boolean(updated.pagerduty_enabled));
      setPagerdutyKey(updated.pagerduty_integration_key ?? "");
      setPagerdutySeverity(updated.pagerduty_severity ?? "");
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
            <h2>NOTIFICATION SETTINGS</h2>
          </div>

          {loading && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--muted)" }}>
              <Bell size={32} style={{ margin: "0 auto 12px", color: "var(--cyan)" }} />
              <p>Loading notification settings…</p>
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
              <SettingsSection title="Email Notifications" description="Configure outbound email alerts">
                <SettingsCard title="Email Enabled" type="checkbox" value={emailEnabled ? "true" : "false"} onChange={(v) => setEmailEnabled(v === "true")} />
          <SettingsCard title="Recipients (comma separated)" value={emailRecipients ?? ""} onChange={(v) => setEmailRecipients(v)} />
              </SettingsSection>
              <SettingsSection title="Slack" description="Slack channel webhook integration">
                <SettingsCard title="Slack Enabled" type="checkbox" value={slackEnabled ? "true" : "false"} onChange={(v) => setSlackEnabled(v === "true")} />
                <SettingsCard title="Webhook URL" value={slackWebhook ?? ""} onChange={(v) => setSlackWebhook(v)} />
                <SettingsCard title="Channel" value={slackChannel ?? ""} onChange={(v) => setSlackChannel(v)} />
              </SettingsSection>
              <SettingsSection title="PagerDuty" description="On-call routing and escalation">
                <SettingsCard title="PagerDuty Enabled" type="checkbox" value={pagerdutyEnabled ? "true" : "false"} onChange={(v) => setPagerdutyEnabled(v === "true")} />
                <SettingsCard title="Integration Key" value={pagerdutyKey ?? ""} onChange={(v) => setPagerdutyKey(v)} />
                <SettingsCard title="Severity" value={pagerdutySeverity ?? ""} onChange={(v) => setPagerdutySeverity(v)} />
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