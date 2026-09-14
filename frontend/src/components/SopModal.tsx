"use client";

import { useState } from "react";
import {
  X, Save, AlertTriangle, Clock, ShieldCheck, ListChecks, Wrench, Tag,
} from "lucide-react";
import { SOP, SOPCategory, SOPUpdatePayload, updateSop } from "@/lib/api";

const CATEGORY_COLORS: Record<string, string> = {
  availability: "var(--red)",
  performance: "var(--amber)",
  capacity: "#119be1",
  configuration: "var(--cyan)",
  security: "#a855f7",
  connectivity: "var(--green)",
  other: "var(--muted)",
};

const CATEGORY_LABELS: Record<string, string> = {
  availability: "Availability",
  performance: "Performance",
  capacity: "Capacity",
  configuration: "Configuration",
  security: "Security",
  connectivity: "Connectivity",
  other: "Other",
};

interface SopModalProps {
  sop: SOP | null;
  onClose: () => void;
  onSaved?: (updated: SOP) => void;
  canEdit?: boolean;
}

export default function SopModal({ sop, onClose, onSaved, canEdit = false }: SopModalProps) {
  // NOTE: parents must render <SopModal key={sop.id} ... /> so this component
  // remounts (and re-initializes form state) when a different SOP is opened.
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<SOPUpdatePayload>(() =>
    sop
      ? {
          name: sop.name,
          description: sop.description ?? "",
          category: sop.category as SOPCategory,
          tags: sop.tags,
          vendor: sop.vendor ?? "",
          platform: sop.platform ?? "",
          device_type: sop.device_type ?? "",
          min_priority: (sop.min_priority as "P1" | "P2" | "P3" | "P4") ?? undefined,
          requires_approval: sop.requires_approval,
          estimated_resolution_minutes: sop.estimated_resolution_minutes ?? undefined,
          rollback_procedure: sop.rollback_procedure ?? "",
          is_active: sop.is_active,
        }
      : {},
  );

  if (!sop) return null;

  const currentVersion = sop.versions?.find((v) => v.id === sop.current_version_id) ?? sop.versions?.[0];
  const steps = currentVersion?.steps ?? [];
  const possibleCauses = currentVersion?.possible_causes ?? [];
  const categoryColor = CATEGORY_COLORS[sop.category] ?? "var(--muted)";

  const handleSave = async () => {
    if (!sop) return;
    setSaving(true);
    setError(null);
    try {
      const payload: SOPUpdatePayload = {
        name: form.name || undefined,
        description: form.description || null,
        category: form.category,
        tags: form.tags,
        vendor: form.vendor || null,
        platform: form.platform || null,
        device_type: form.device_type || null,
        min_priority: form.min_priority || null,
        requires_approval: form.requires_approval,
        estimated_resolution_minutes: form.estimated_resolution_minutes || null,
        rollback_procedure: form.rollback_procedure || null,
        is_active: form.is_active,
      };
      const updated = await updateSop(sop.id, payload);
      onSaved?.(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save SOP");
    } finally {
      setSaving(false);
    }
  };

  const set = <K extends keyof SOPUpdatePayload>(key: K, value: SOPUpdatePayload[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(2, 12, 27, 0.75)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
      onClick={onClose}
    >
      <div
        className="hud-panel"
        style={{
          width: "min(760px, 100%)", maxHeight: "88vh", overflowY: "auto",
          border: "1px solid var(--line)", borderRadius: 12,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "20px 24px", borderBottom: "1px solid var(--line)" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12, color: "var(--muted)", fontFamily: "monospace" }}>{sop.sop_number}</span>
              <span style={{ fontSize: 11, padding: "2px 10px", borderRadius: 999, background: `${categoryColor}22`, color: categoryColor, border: `1px solid ${categoryColor}55`, textTransform: "uppercase", letterSpacing: 0.5 }}>
                {CATEGORY_LABELS[sop.category] ?? sop.category}
              </span>
              {!sop.is_active && (
                <span style={{ fontSize: 11, padding: "2px 10px", borderRadius: 999, background: "var(--red)22", color: "var(--red)", border: "1px solid var(--red)55", textTransform: "uppercase" }}>
                  Inactive
                </span>
              )}
            </div>
            <h3 style={{ margin: "8px 0 4px", fontSize: 20, color: "var(--text)" }}>{sop.name}</h3>
            {sop.description && !editing && (
              <p style={{ margin: 0, fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{sop.description}</p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", padding: 4 }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "20px 24px" }}>
          {error && (
            <div style={{ marginBottom: 16, padding: "10px 14px", borderRadius: 8, background: "var(--red)18", border: "1px solid var(--red)44", color: "var(--red)", fontSize: 13 }}>
              {error}
            </div>
          )}

          {editing ? (
            /* ---- Edit form ---- */
            <div style={{ display: "grid", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Name *</label>
                <input
                  value={form.name ?? ""}
                  onChange={(e) => set("name", e.target.value)}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Description</label>
                <textarea
                  value={form.description ?? ""}
                  onChange={(e) => set("description", e.target.value)}
                  rows={3}
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }}
                />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Category</label>
                  <select
                    value={form.category ?? "other"}
                    onChange={(e) => set("category", e.target.value as SOPCategory)}
                    style={inputStyle}
                  >
                    {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Min Priority</label>
                  <select
                    value={form.min_priority ?? ""}
                    onChange={(e) => set("min_priority", (e.target.value || null) as "P1" | "P2" | "P3" | "P4" | null)}
                    style={inputStyle}
                  >
                    <option value="">Any</option>
                    <option value="P1">P1</option>
                    <option value="P2">P2</option>
                    <option value="P3">P3</option>
                    <option value="P4">P4</option>
                  </select>
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Vendor</label>
                  <input value={form.vendor ?? ""} onChange={(e) => set("vendor", e.target.value)} style={inputStyle} />
                </div>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Platform</label>
                  <input value={form.platform ?? ""} onChange={(e) => set("platform", e.target.value)} style={inputStyle} />
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Device Type</label>
                  <input value={form.device_type ?? ""} onChange={(e) => set("device_type", e.target.value)} style={inputStyle} />
                </div>
                <div>
                  <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Est. Resolution (min)</label>
                  <input
                    type="number"
                    value={form.estimated_resolution_minutes ?? ""}
                    onChange={(e) => set("estimated_resolution_minutes", e.target.value ? Number(e.target.value) : null)}
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Tags (comma separated)</label>
                <input
                  value={(form.tags ?? []).join(", ")}
                  onChange={(e) => set("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--muted)", display: "block", marginBottom: 4 }}>Rollback Procedure</label>
                <textarea
                  value={form.rollback_procedure ?? ""}
                  onChange={(e) => set("rollback_procedure", e.target.value)}
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }}
                />
              </div>
              <div style={{ display: "flex", gap: 24 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text)", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={form.requires_approval ?? true}
                    onChange={(e) => set("requires_approval", e.target.checked)}
                  />
                  Requires approval
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text)", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={form.is_active ?? true}
                    onChange={(e) => set("is_active", e.target.checked)}
                  />
                  Active
                </label>
              </div>
            </div>
          ) : (
            /* ---- View mode ---- */
            <div style={{ display: "grid", gap: 20 }}>
              {/* Metadata chips */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {sop.vendor && <Chip icon={<Tag size={12} />} label={`Vendor: ${sop.vendor}`} />}
                {sop.platform && <Chip icon={<Tag size={12} />} label={`Platform: ${sop.platform}`} />}
                {sop.device_type && <Chip icon={<Tag size={12} />} label={`Device: ${sop.device_type}`} />}
                {sop.min_priority && <Chip icon={<AlertTriangle size={12} />} label={`Min: ${sop.min_priority}`} />}
                {sop.estimated_resolution_minutes != null && (
                  <Chip icon={<Clock size={12} />} label={`~${sop.estimated_resolution_minutes} min`} />
                )}
                {sop.requires_approval && <Chip icon={<ShieldCheck size={12} />} label="Approval required" />}
              </div>

              {/* Steps */}
              {steps.length > 0 && (
                <div>
                  <h4 style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 10px", fontSize: 13, color: "var(--cyan)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    <ListChecks size={16} /> Procedure Steps
                  </h4>
                  <ol style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 8 }}>
                    {steps.map((step, i) => (
                      <li key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                        <span style={{
                          minWidth: 24, height: 24, borderRadius: 999, background: "var(--cyan)22",
                          color: "var(--cyan)", border: "1px solid var(--cyan)55",
                          display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: 12, fontWeight: 600, flexShrink: 0,
                        }}>
                          {step.order ?? i + 1}
                        </span>
                        <span style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, paddingTop: 3 }}>
                          {step.action ?? JSON.stringify(step)}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {/* Possible causes */}
              {possibleCauses.length > 0 && (
                <div>
                  <h4 style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 10px", fontSize: 13, color: "var(--amber)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    <Wrench size={16} /> Possible Causes
                  </h4>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {possibleCauses.map((cause, i) => (
                      <span key={i} style={{ fontSize: 12, padding: "4px 12px", borderRadius: 999, background: "var(--amber)14", border: "1px solid var(--amber)44", color: "var(--amber)" }}>
                        {cause}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Rollback */}
              {sop.rollback_procedure && (
                <div>
                  <h4 style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 6px", fontSize: 13, color: "var(--red)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    <AlertTriangle size={16} /> Rollback
                  </h4>
                  <p style={{ margin: 0, fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{sop.rollback_procedure}</p>
                </div>
              )}

              {/* Skills */}
              {sop.required_skills.length > 0 && (
                <div>
                  <h4 style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 8px", fontSize: 13, color: "var(--muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    <ShieldCheck size={16} /> Required Skills
                  </h4>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {sop.required_skills.map((skill, i) => (
                      <span key={i} style={{ fontSize: 12, padding: "4px 12px", borderRadius: 999, background: "var(--line)", color: "var(--text)" }}>
                        {skill}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Version info */}
              {currentVersion && (
                <div style={{ fontSize: 12, color: "var(--muted)", borderTop: "1px solid var(--line)", paddingTop: 14 }}>
                  Version {currentVersion.version_number} · {currentVersion.status}
                  {currentVersion.approved_at && ` · approved ${new Date(currentVersion.approved_at).toLocaleDateString()}`}
                  {" · "}Updated {new Date(sop.updated_at).toLocaleDateString()}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "14px 24px", borderTop: "1px solid var(--line)" }}>
          {canEdit && !editing && (
            <button
              onClick={() => setEditing(true)}
              style={buttonStyle.secondary}
            >
              Edit SOP
            </button>
          )}
          {editing && (
            <>
              <button
                onClick={() => { setEditing(false); setError(null); }}
                disabled={saving}
                style={buttonStyle.secondary}
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                style={{ ...buttonStyle.primary, opacity: saving ? 0.6 : 1 }}
              >
                <Save size={14} /> {saving ? "Saving…" : "Save Changes"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  borderRadius: 8,
  border: "1px solid var(--line)",
  background: "rgba(2, 12, 27, 0.6)",
  color: "var(--text)",
  fontSize: 13,
  outline: "none",
};

const buttonStyle = {
  primary: {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "8px 16px", borderRadius: 8, border: "none",
    background: "var(--cyan)", color: "#02121b", fontWeight: 600, fontSize: 13,
    cursor: "pointer",
  },
  secondary: {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "8px 16px", borderRadius: 8,
    border: "1px solid var(--line)", background: "transparent",
    color: "var(--text)", fontSize: 13, cursor: "pointer",
  },
};

function Chip({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, padding: "4px 12px", borderRadius: 999, background: "var(--line)", color: "var(--text)" }}>
      {icon} {label}
    </span>
  );
}