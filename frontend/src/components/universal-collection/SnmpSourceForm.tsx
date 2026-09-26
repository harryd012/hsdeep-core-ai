"use client";

/**
 * Phase 9.9 - SNMP monitoring source onboarding.
 *
 *   Add/Edit -> Name, Host/IP, Port, SNMP version, credential
 *     -> Save (POST/PATCH /api/monitoring/sources)
 *     -> readiness probe (POST /api/monitoring/sources/{id}/readiness)
 *     -> result shown VERBATIM (status + stage + message)
 *     -> the existing Collect action runs the real collection cycle
 *
 * Two deliberate choices:
 *
 * 1. SAVE-THEN-TEST: the backend only probes a PERSISTED source, and duplicating
 *    credential handling (or keeping a secret in temporary storage) just to
 *    preview a probe is forbidden. A new source is saved first, probed
 *    immediately after, and a failure is shown honestly rather than hidden.
 * 2. THE CREDENTIAL IS WRITE-ONLY: it lives in this component's state only while
 *    it is being typed, is sent exactly once, and is cleared the moment the save
 *    succeeds. A stored credential is never fetched, prefilled or rendered -
 *    only "Configured", plus an explicit "Replace credential" action.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, KeyRound, Plus, Save, X } from "lucide-react";

import type { MonitoringSourceOut } from "@/lib/api";
import {
  createMonitoringSource,
  probeSourceReadiness,
  type MonitoringSourceInput,
  type ReadinessResultOut,
  updateMonitoringSource,
} from "@/lib/monitoringApi";

const SNMP_VERSIONS = ["v2c", "v1", "v3"] as const;

const inputStyle: React.CSSProperties = {
  background: "var(--panel, #0b1220)",
  border: "1px solid var(--line)",
  borderRadius: 4,
  color: "inherit",
  fontSize: 12,
  padding: "6px 8px",
  width: "100%",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 10,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "var(--muted)",
  marginBottom: 3,
};

function buttonStyle(color: string, disabled = false): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    padding: "5px 10px",
    borderRadius: 4,
    border: `1px solid ${color}`,
    background: "transparent",
    color,
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 12,
    opacity: disabled ? 0.55 : 1,
  };
}

type Fields = { name: string; hostname: string; port: string; snmp_version: string; username: string };

const EMPTY: Fields = { name: "", hostname: "", port: "161", snmp_version: "v2c", username: "" };

export default function SnmpSourceForm({
  sources,
  onSaved,
}: {
  sources: MonitoringSourceOut[];
  onSaved: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MonitoringSourceOut | null>(null);
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [credential, setCredential] = useState("");
  const [replaceCredential, setReplaceCredential] = useState(false);
  const [busy, setBusy] = useState<null | "save">(null);
  const [result, setResult] = useState<ReadinessResultOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** Never keep a typed secret once the form is closed or a save succeeds. */
  const resetSecret = useCallback(() => {
    setCredential("");
    setReplaceCredential(false);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setEditing(null);
    setFields(EMPTY);
    setResult(null);
    setError(null);
    resetSecret();
  }, [resetSecret]);

  useEffect(() => {
    if (!open) return;
    // Clear any secret still held from an earlier, unsaved session of the form.
    return () => setCredential("");
  }, [open]);

  const startEdit = useCallback(
    (source: MonitoringSourceOut) => {
      setEditing(source);
      setOpen(true);
      setResult(null);
      setError(null);
      resetSecret();
      setFields({
        name: source.name,
        hostname: source.hostname ?? "",
        port: source.port ? String(source.port) : "161",
        snmp_version: source.snmp_version ?? "v2c",
        username: source.username ?? "",
      });
    },
    [resetSecret],
  );

  const credentialRequired = !editing || replaceCredential;
  const credentialConfigured = Boolean(editing?.credential_configured);

  const onSave = useCallback(async () => {
    setError(null);
    setResult(null);
    if (!fields.name.trim() || !fields.hostname.trim()) {
      setError("Name and host are required.");
      return;
    }
    if (credentialRequired && !credential.trim()) {
      setError(
        editing
          ? "Enter a replacement credential, or keep the stored one."
          : "A community string / authentication key is required.",
      );
      return;
    }
    setBusy("save");
    try {
      const payload: MonitoringSourceInput = {
        name: fields.name.trim(),
        source_type: "snmp",
        hostname: fields.hostname.trim(),
        port: Number(fields.port) || 161,
        protocol: "snmp",
        snmp_version: fields.snmp_version,
        username: fields.snmp_version === "v3" ? fields.username.trim() || null : null,
        is_active: true,
        poll_interval_seconds: editing?.poll_interval_seconds ?? 60,
        credential: credential.trim() ? credential : null,
      };
      const saved = editing
        ? await updateMonitoringSource(editing.id, payload)
        : await createMonitoringSource(payload);

      // The secret is stored now; drop it from client state immediately.
      resetSecret();
      setEditing(saved);
      setFields({
        name: saved.name,
        hostname: saved.hostname ?? "",
        port: saved.port ? String(saved.port) : "161",
        snmp_version: saved.snmp_version ?? "v2c",
        username: saved.username ?? "",
      });

      // Save-then-test, through the existing readiness path only.
      try {
        setResult(await probeSourceReadiness(saved.id));
      } catch (probeErr) {
        setError(
          probeErr instanceof Error
            ? `Saved, but the readiness probe failed: ${probeErr.message}`
            : "Saved, but the readiness probe failed.",
        );
      }
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save the source");
    } finally {
      setBusy(null);
    }
  }, [credential, credentialRequired, editing, fields, onSaved, resetSecret]);

  const snmpSources = sources.filter((s) => s.source_type === "snmp");

  return (
    <div style={{ marginTop: 16 }} data-testid="snmp-onboarding">
      <h3 style={{ fontSize: 13, color: "var(--cyan)" }}>SNMP SOURCES ({snmpSources.length})</h3>
      <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
        Configure an SNMP-capable device, then test it and collect. The credential is stored
        encrypted and is never shown again after saving.
      </p>

      <div style={{ display: "grid", gap: 6, marginTop: 8 }} data-testid="snmp-source-list">
        {snmpSources.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--muted)" }}>No SNMP sources configured yet.</p>
        )}
        {snmpSources.map((source) => (
          <div
            key={source.id}
            data-testid={`snmp-source-row-${source.id}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 10px",
              border: "1px solid var(--line)",
              borderRadius: 6,
              flexWrap: "wrap",
            }}
          >
            <strong style={{ fontSize: 13 }}>{source.name}</strong>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              {source.hostname ?? "no host"}
              {source.port ? `:${source.port}` : ""} · {source.snmp_version ?? "v2c"} ·{" "}
              {source.status}
            </span>
            <span
              data-testid={`snmp-credential-state-${source.id}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: 11,
                color: source.credential_configured ? "var(--green)" : "var(--muted)",
              }}
            >
              <KeyRound size={12} />
              {source.credential_configured ? "credential configured" : "no credential"}
            </span>
            <span style={{ marginLeft: "auto" }}>
              <button
                type="button"
                data-testid={`snmp-edit-${source.id}`}
                onClick={() => startEdit(source)}
                style={buttonStyle("var(--cyan)")}
                title="Edit this source. The stored credential is never shown."
              >
                Edit
              </button>
            </span>
          </div>
        ))}
      </div>

      {!open ? (
        <button
          type="button"
          data-testid="snmp-add"
          onClick={() => {
            setOpen(true);
            setEditing(null);
            setFields(EMPTY);
            setResult(null);
            resetSecret();
          }}
          style={{ ...buttonStyle("var(--green)"), marginTop: 10 }}
        >
          <Plus size={13} /> Add SNMP source
        </button>
      ) : (
        <div
          data-testid="snmp-source-form"
          style={{
            marginTop: 10,
            padding: 12,
            border: "1px solid var(--line)",
            borderRadius: 6,
            display: "grid",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <strong style={{ fontSize: 12 }}>
              {editing ? `Edit ${editing.name}` : "New SNMP source"}
            </strong>
            <button
              type="button"
              data-testid="snmp-cancel"
              onClick={close}
              style={{ ...buttonStyle("var(--muted)"), marginLeft: "auto" }}
            >
              <X size={13} /> Cancel
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "2fr 2fr 1fr 1fr", gap: 8 }}>
            <label style={labelStyle}>
              Source name
              <input
                data-testid="snmp-name"
                style={inputStyle}
                value={fields.name}
                onChange={(e) => setFields({ ...fields, name: e.target.value })}
                placeholder="Core-SW-01"
              />
            </label>
            <label style={labelStyle}>
              Host / IP
              <input
                data-testid="snmp-host"
                style={inputStyle}
                value={fields.hostname}
                onChange={(e) => setFields({ ...fields, hostname: e.target.value })}
                placeholder="10.0.0.1"
              />
            </label>
            <label style={labelStyle}>
              Port
              <input
                data-testid="snmp-port"
                style={inputStyle}
                inputMode="numeric"
                value={fields.port}
                onChange={(e) => setFields({ ...fields, port: e.target.value })}
              />
            </label>
            <label style={labelStyle}>
              SNMP version
              <select
                data-testid="snmp-version"
                style={inputStyle}
                value={fields.snmp_version}
                onChange={(e) => setFields({ ...fields, snmp_version: e.target.value })}
              >
                {SNMP_VERSIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {fields.snmp_version === "v3" && (
            <label style={labelStyle}>
              SNMPv3 security name
              <input
                data-testid="snmp-username"
                style={inputStyle}
                value={fields.username}
                onChange={(e) => setFields({ ...fields, username: e.target.value })}
                placeholder="snmpuser"
              />
            </label>
          )}

          <div>
            <label style={labelStyle}>
              {fields.snmp_version === "v3" ? "SNMPv3 authentication key" : "Community (v1/v2c)"}
              {credentialRequired ? (
                <input
                  data-testid="snmp-credential"
                  type="password"
                  autoComplete="new-password"
                  style={inputStyle}
                  value={credential}
                  onChange={(e) => setCredential(e.target.value)}
                  placeholder={editing ? "replacement credential" : "stored encrypted"}
                />
              ) : (
                <span
                  data-testid="snmp-credential-stored"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 12,
                    color: "var(--muted)",
                    border: "1px solid var(--line)",
                    borderRadius: 4,
                    padding: "6px 8px",
                  }}
                >
                  <KeyRound size={12} /> •••••••••••• Configured
                </span>
              )}
            </label>
            {credentialConfigured && !replaceCredential && (
              <button
                type="button"
                data-testid="snmp-replace-credential"
                onClick={() => setReplaceCredential(true)}
                style={{ ...buttonStyle("var(--muted)"), marginTop: 6 }}
              >
                Replace credential
              </button>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button
              type="button"
              data-testid="snmp-save"
              onClick={onSave}
              disabled={busy !== null}
              style={buttonStyle("var(--green)", busy !== null)}
            >
              <Save size={13} /> {busy === "save" ? "Saving…" : "Save & test readiness"}
            </button>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              Saving encrypts the credential, then probes the saved source.
            </span>
          </div>
        </div>
      )}

      {result && (
        <p
          data-testid="snmp-readiness"
          style={{
            marginTop: 8,
            fontSize: 12,
            display: "flex",
            alignItems: "center",
            gap: 6,
            color: result.status === "ready" ? "var(--green)" : "var(--yellow, #f0b429)",
          }}
        >
          {result.status === "ready" ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
          Readiness: {result.status} (stage {result.stage}) — {result.message}
        </p>
      )}
      {error && (
        <p
          data-testid="snmp-error"
          style={{ marginTop: 8, fontSize: 12, color: "var(--red, #f87171)" }}
        >
          <AlertTriangle size={13} /> {error}
        </p>
      )}
    </div>
  );
}
