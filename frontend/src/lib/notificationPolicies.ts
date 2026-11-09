/**
 * Notification policies API (observability plan P9 slice 1): route incidents
 * by severity, priority and site to the organization's channels, a policy's
 * own email recipients, or its own Microsoft Teams webhook. Admin only.
 */
import { apiFetch } from "@/lib/api";

export type PolicySeverity = "critical" | "warning" | "info";
export type PolicyChannel = "email" | "slack" | "pagerduty";

export interface NotificationPolicy {
  id: string;
  name: string;
  enabled: boolean;
  position: number;
  min_severity: PolicySeverity;
  priorities: string[];
  site_ids: string[];
  channels: PolicyChannel[];
  email_recipients: string | null;
  /** Masked: "••••" and the last 4 characters, or null when none is set. */
  teams_webhook: string | null;
  created_at: string;
  updated_at: string;
}

export interface PolicyInput {
  name?: string;
  enabled?: boolean;
  position?: number;
  min_severity?: PolicySeverity;
  priorities?: string[];
  site_ids?: string[];
  channels?: PolicyChannel[];
  email_recipients?: string | null;
  /** A new URL; "" removes it; the masked value keeps the stored one. */
  teams_webhook_url?: string | null;
}

const BASE = "/api/settings/notifications/policies";

export function listPolicies(): Promise<NotificationPolicy[]> {
  return apiFetch<NotificationPolicy[]>(BASE);
}

export function createPolicy(body: PolicyInput): Promise<NotificationPolicy> {
  return apiFetch<NotificationPolicy>(BASE, { method: "POST", body: JSON.stringify(body) });
}

export function updatePolicy(id: string, body: PolicyInput): Promise<NotificationPolicy> {
  return apiFetch<NotificationPolicy>(`${BASE}/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deletePolicy(id: string): Promise<void> {
  return apiFetch<void>(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function testPolicy(id: string): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>(`${BASE}/${encodeURIComponent(id)}/test`, { method: "POST" });
}

/** A delivery log channel key ("teams:<policy>", "email:<policy>", "slack") as a label. */
export function destinationLabel(key: string, policies: NotificationPolicy[] = []): string {
  const [kind, short] = key.split(":");
  const name = { email: "Email", slack: "Slack", pagerduty: "PagerDuty", teams: "Microsoft Teams" }[kind] ?? kind;
  if (!short) return name;
  const policy = policies.find((p) => p.id.replace(/-/g, "").startsWith(short));
  return `${name} · ${policy ? policy.name : "policy"}`;
}
