"use client";

import { useEffect, useState } from "react";
import { SaveBar } from "@/components/settings/SettingsSection";
import { Banner, DataTable, PageShell, Panel, Pill, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import SettingsBackButton from "@/components/settings/SettingsBackButton";
import { useAuth } from "@/lib/auth-context";
import {
  getNotificationSettings,
  listNotificationDeliveries,
  sendTestNotification,
  updateNotificationSettings,
  NotificationDelivery,
  NotificationSettings,
} from "@/lib/api";

const DELIVERY_COLUMNS: Column<NotificationDelivery>[] = [
  { key: "when", header: "When", render: (d) => ago(d.sent_at ?? d.created_at), sortValue: (d) => d.sent_at ?? d.created_at },
  { key: "channel", header: "Channel", render: (d) => titleCaseWords(d.channel) },
  { key: "event", header: "Event", render: (d) => (d.event === "opened" ? "Incident opened" : "Incident resolved") },
  {
    key: "status",
    header: "Result",
    render: (d) => (d.status === "sent" ? <Pill tone="good">Sent</Pill> : <Pill tone="bad">Failed · {d.attempts} tries</Pill>),
  },
  { key: "error", header: "Error", render: (d) => d.last_error ?? "—" },
  { key: "incident", header: "Incident", render: (d) => <a href={`/incidents/${d.incident_id}`}>Open</a> },
];

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
  const [deliveries, setDeliveries] = useState<NotificationDelivery[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    listNotificationDeliveries().then(setDeliveries).catch(() => setDeliveries(null));
  }, []);

  async function onTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const { results } = await sendTestNotification();
      setTestResult(Object.entries(results).map(([ch, r]) => `${titleCaseWords(ch)}: ${r}`).join(" · "));
    } catch (err) {
      setTestResult(err instanceof Error ? err.message : "Test failed");
    } finally {
      setTesting(false);
    }
  }

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

  const channelState = (on: boolean, configured: boolean) =>
    on ? (configured ? <Pill tone="good">Enabled</Pill> : <Pill tone="warn">Enabled · not configured</Pill>) : <Pill tone="neutral">Off</Pill>;

  return (
    <PageShell
      title="Notifications"
      subtitle="Where alerts and incidents are delivered for this tenant."
      crumbs={[{ label: "Settings", href: "/settings" }, { label: "Notifications" }]}
      actions={<SettingsBackButton />}
    >
      {loading && <Panel title="Notification channels" state="loading" skeletonHeight={260} />}

      {!loading && error && (
        <Panel title="Notification channels" state="error" errorText={error} onRetry={handleRetry} />
      )}

      {!loading && !error && data && (
        <>
          {success && <Banner tone="good">{success}</Banner>}
          <div className="pk-grid">
            <Panel title="Email" info="Send alert notifications by email." aside={channelState(emailEnabled, !!emailRecipients?.trim())} state="ready" className="pk-col-4">
              <div className="pk-form">
                <label className="pk-switch">
                  <input type="checkbox" checked={emailEnabled} onChange={(e) => setEmailEnabled(e.target.checked)} />
                  Send email notifications
                </label>
                <div className="pk-field">
                  <label htmlFor="email-recipients">Recipients</label>
                  <textarea
                    id="email-recipients"
                    className="pk-input"
                    value={emailRecipients ?? ""}
                    placeholder="ops@example.com, noc@example.com"
                    onChange={(e) => setEmailRecipients(e.target.value)}
                  />
                  <small>Comma-separated email addresses.</small>
                </div>
              </div>
            </Panel>

            <Panel title="Slack" info="Post alerts to a Slack channel through an incoming webhook." aside={channelState(slackEnabled, !!slackWebhook?.trim())} state="ready" className="pk-col-4">
              <div className="pk-form">
                <label className="pk-switch">
                  <input type="checkbox" checked={slackEnabled} onChange={(e) => setSlackEnabled(e.target.checked)} />
                  Post to Slack
                </label>
                <div className="pk-field">
                  <label htmlFor="slack-webhook">Webhook URL</label>
                  <input id="slack-webhook" className="pk-input" type="password" autoComplete="off" value={slackWebhook ?? ""} placeholder="https://hooks.slack.com/services/…" onChange={(e) => setSlackWebhook(e.target.value)} />
                  <small>Must be a Slack incoming webhook (hooks.slack.com). Stored for this tenant; shown masked.</small>
                </div>
                <div className="pk-field">
                  <label htmlFor="slack-channel">Channel</label>
                  <input id="slack-channel" className="pk-input" value={slackChannel ?? ""} placeholder="#noc-alerts" onChange={(e) => setSlackChannel(e.target.value)} />
                </div>
              </div>
            </Panel>

            <Panel title="PagerDuty" info="Route incidents to on-call through a PagerDuty Events integration." aside={channelState(pagerdutyEnabled, !!pagerdutyKey?.trim())} state="ready" className="pk-col-4">
              <div className="pk-form">
                <label className="pk-switch">
                  <input type="checkbox" checked={pagerdutyEnabled} onChange={(e) => setPagerdutyEnabled(e.target.checked)} />
                  Page on-call through PagerDuty
                </label>
                <div className="pk-field">
                  <label htmlFor="pd-key">Integration key</label>
                  <input id="pd-key" className="pk-input" type="password" autoComplete="off" value={pagerdutyKey ?? ""} onChange={(e) => setPagerdutyKey(e.target.value)} />
                  <small>Stored for this tenant; shown masked.</small>
                </div>
                <div className="pk-field">
                  <label htmlFor="pd-severity">Minimum severity</label>
                  <select id="pd-severity" className="pk-input" value={pagerdutySeverity ?? ""} onChange={(e) => setPagerdutySeverity(e.target.value)}>
                    <option value="">Not set</option>
                    {["critical", "error", "warning", "info"].map((s) => (
                      <option key={s} value={s}>{titleCaseWords(s)}</option>
                    ))}
                    {pagerdutySeverity && !["critical", "error", "warning", "info"].includes(pagerdutySeverity) && (
                      <option value={pagerdutySeverity}>{pagerdutySeverity}</option>
                    )}
                  </select>
                </div>
              </div>
            </Panel>
          </div>

          <Panel
            title="Recent deliveries"
            info="Incidents at warning or above notify email and Slack; PagerDuty uses its minimum severity. Each incident is sent once per channel when it opens and again when it resolves."
            aside={
              <button type="button" className="pk-button" onClick={onTest} disabled={testing || hasChanges}>
                {testing ? "Sending…" : "Send test notification"}
              </button>
            }
            state={deliveries === null ? "error" : "ready"}
            errorText="Couldn't load the delivery log."
          >
            {testResult && <Banner tone="info">{testResult}</Banner>}
            {deliveries && (
              <DataTable columns={DELIVERY_COLUMNS} rows={deliveries} rowKey={(d) => `${d.incident_id}-${d.channel}-${d.event}`}
                emptyText="No incident notifications sent yet." maxHeight={360} caption="Recent incident notification deliveries" />
            )}
          </Panel>
        </>
      )}

      <SaveBar onSave={onSave} saving={saving} disabled={loading || !hasChanges} hasChanges={hasChanges} />
    </PageShell>
  );
}
