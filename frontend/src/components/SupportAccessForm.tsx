"use client";

import { useState } from "react";
import { openSupportGrant } from "@/lib/api";

/**
 * Break-glass support access (R1.2). A platform support account must state a
 * reason and a duration before acting in a customer tenant; the grant is
 * audited in the customer's tenant and the customer can revoke it.
 */

const DURATIONS = [
  { minutes: 30, label: "30 minutes" },
  { minutes: 60, label: "1 hour" },
  { minutes: 120, label: "2 hours" },
  { minutes: 240, label: "4 hours" },
  { minutes: 480, label: "8 hours" },
];
const MIN_REASON = 10;

export default function SupportAccessForm({
  tenantId,
  tenantName,
  onOpened,
  onCancel,
}: {
  tenantId: string;
  tenantName: string;
  onOpened: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = reason.trim().length >= MIN_REASON;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await openSupportGrant(tenantId, reason.trim(), minutes);
      onOpened();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open support access.");
      setBusy(false);
    }
  }

  return (
    <form className="hs-support-form" onSubmit={submit} aria-label={`Open support access to ${tenantName}`}>
      <p className="hs-support-form__title">Support access to {tenantName}</p>
      <p className="hs-support-form__hint">Recorded in the customer&apos;s audit log. They can see and revoke it.</p>
      <label className="hs-support-form__label">
        Reason
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="Ticket number and what you need to check"
          required
        />
      </label>
      <label className="hs-support-form__label">
        Duration
        <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
          {DURATIONS.map((d) => (
            <option key={d.minutes} value={d.minutes}>{d.label}</option>
          ))}
        </select>
      </label>
      {error && <p className="hs-support-form__error" role="alert">{error}</p>}
      <div className="hs-support-form__actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" disabled={!valid || busy} title={valid ? undefined : `At least ${MIN_REASON} characters`}>
          {busy ? "Opening…" : "Open access"}
        </button>
      </div>
    </form>
  );
}
