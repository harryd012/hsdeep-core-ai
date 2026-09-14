"use client";

import { useEffect, useState } from "react";
import { Key, Plus, Trash2, Copy, X } from "lucide-react";
import {
  listApiKeys,
  createApiKey,
  deleteApiKey,
  listPermissions,
  ApiKey,
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  PermissionOut,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import SettingsBackButton from "@/components/settings/SettingsBackButton";

interface ApiKeyItem extends ApiKey {
  created_at: string;
}

export default function ApiKeysSettingsPage() {
  const { tenantId } = useAuth();
  const [items, setItems] = useState<ApiKeyItem[]>([]);
  const [permissions, setPermissions] = useState<PermissionOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newExpires, setNewExpires] = useState("");
  const [newScopes, setNewScopes] = useState<string[]>([]);

  const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<ApiKeyItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [data, perms] = await Promise.all([
          listApiKeys(tenantId),
          listPermissions(tenantId).catch(() => [] as PermissionOut[]),
        ]);
        if (!cancelled) {
          setItems(data);
          setPermissions(perms);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load API keys");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  function toggleScope(code: string) {
    setNewScopes((prev) =>
      prev.includes(code) ? prev.filter((s) => s !== code) : [...prev, code]
    );
  }

  async function handleCreate() {
    if (!newName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const payload: ApiKeyCreateRequest = {
        name: newName.trim(),
        scopes: newScopes,
        expires_at: newExpires || null,
      };
      const response = await createApiKey(tenantId, payload);
      const created: ApiKeyItem = {
        id: response.id,
        tenant_id: tenantId,
        name: response.name,
        key_prefix: response.key_prefix,
        scopes: response.scopes ?? [],
        created_at: response.created_at,
        last_used_at: null,
        expires_at: response.expires_at,
        is_active: true,
      };
      setItems((prev) => [created, ...prev]);
      setCreatedKey(response);
      setNewName("");
      setNewExpires("");
      setNewScopes([]);
      setShowForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create API key");
    } finally {
      setSaving(false);
    }
  }

  function handleDismissCreated() {
    setCreatedKey(null);
    setCopied(false);
  }

  async function handleCopy() {
    if (createdKey) {
      await navigator.clipboard.writeText(createdKey.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteApiKey(tenantId, deleteTarget.id);
      setItems((prev) => prev.filter((k) => k.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete API key");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <SettingsBackButton />
            <h2>API KEYS</h2>
          </div>

          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}

          {!loaded && (
            <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 32, textAlign: "center", color: "var(--muted)" }}>
              <Key size={32} style={{ margin: "0 auto 12px", color: "var(--cyan)" }} />
              <p>Loading API keys…</p>
            </div>
          )}

          {loaded && (
            <>
              <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
                <button
                  onClick={() => setShowForm((v) => !v)}
                  style={{ background: "var(--cyan)", color: "#001a1f", border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
                >
                  <Plus size={14} style={{ marginRight: 6 }} />
                  New API Key
                </button>
              </div>

              {showForm && (
                <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16, marginBottom: 12, display: "grid", gap: 10, maxWidth: 640 }}>
                  <input
                    placeholder="Key Name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    style={{ background: "rgba(0,0,0,0.35)", border: "1px solid var(--line)", borderRadius: 6, color: "#e8f8ff", padding: "10px 12px", fontSize: 14, outline: "none" }}
                  />
                  <input
                    type="date"
                    value={newExpires}
                    onChange={(e) => setNewExpires(e.target.value)}
                    style={{ background: "rgba(0,0,0,0.35)", border: "1px solid var(--line)", borderRadius: 6, color: "#e8f8ff", padding: "10px 12px", fontSize: 14, outline: "none" }}
                  />
                  <div>
                    <label style={{ color: "#9dc5d4", fontSize: 12, fontWeight: 500, display: "block", marginBottom: 6 }}>Scopes</label>
                    {permissions.length === 0 && (
                      <p style={{ color: "var(--muted)", fontSize: 11 }}>No permission scopes available.</p>
                    )}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 4 }}>
                      {permissions.map((p) => {
                        const checked = newScopes.includes(p.code);
                        return (
                          <label key={p.code} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#c5f3ff", cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleScope(p.code)}
                              style={{ accentColor: "var(--cyan)", width: 14, height: 14 }}
                            />
                            <span>{p.code}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                  <button
                    onClick={handleCreate}
                    disabled={saving || !newName.trim()}
                    style={{ background: saving ? "rgba(0,255,255,0.4)" : "var(--cyan)", color: "#001a1f", border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 12, fontWeight: 600, cursor: "pointer", opacity: saving || !newName.trim() ? 0.6 : 1 }}
                  >
                    {saving ? "Creating…" : "Create"}
                  </button>
                </div>
              )}

              {items.length === 0 && (
                <p style={{ color: "var(--muted)", fontSize: 12 }}>No API keys configured.</p>
              )}

              {items.length > 0 && (
                <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ background: "rgba(1,10,17,0.8)" }}>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Name</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Prefix</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Scopes</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Created</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Expires</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Last Used</th>
                        <th style={{ textAlign: "left", padding: 10, fontSize: 11, color: "var(--muted)" }}>Status</th>
                        <th style={{ textAlign: "right", padding: 10, fontSize: 11, color: "var(--muted)" }}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((k) => (
                        <tr key={k.id} style={{ borderTop: "1px solid var(--line)" }}>
                          <td style={{ padding: 10, fontSize: 13, color: "#c5f3ff" }}>{k.name}</td>
                          <td style={{ padding: 10, fontSize: 12, color: "var(--muted)" }}>
                            <code>{k.key_prefix}...</code>
                          </td>
                          <td style={{ padding: 10, fontSize: 11, color: "var(--muted)" }}>
                            {Array.isArray(k.scopes) && k.scopes.length > 0
                              ? k.scopes.slice(0, 3).join(", ") + (k.scopes.length > 3 ? ` +${k.scopes.length - 3}` : "")
                              : "—"}
                          </td>
                          <td style={{ padding: 10, fontSize: 12, color: "var(--muted)" }}>
                            {new Date(k.created_at).toLocaleDateString()}
                          </td>
                          <td style={{ padding: 10, fontSize: 12, color: "var(--muted)" }}>
                            {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}
                          </td>
                          <td style={{ padding: 10, fontSize: 12, color: "var(--muted)" }}>
                            {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}
                          </td>
                          <td style={{ padding: 10, fontSize: 12, color: k.is_active ? "var(--cyan)" : "var(--red)" }}>
                            {k.is_active ? "Active" : "Inactive"}
                          </td>
                          <td style={{ padding: 10, textAlign: "right" }}>
                            <button
                              type="button"
                              onClick={() => setDeleteTarget(k)}
                              style={{ padding: 6, borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--red)", cursor: "pointer" }}
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {createdKey && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: "var(--panel)", border: "1px solid var(--cyan)", borderRadius: 8, padding: 24, maxWidth: 520, width: "90%", boxShadow: "0 0 20px rgba(0,255,204,0.2)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, color: "var(--cyan)", margin: 0 }}>New API Key Created</h3>
              <button onClick={handleDismissCreated} style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer" }}>
                <X size={20} />
              </button>
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
              This key will only be shown once. Store it securely.
            </p>
            <div style={{ background: "rgba(0,10,17,0.8)", border: "1px solid var(--line)", borderRadius: 4, padding: 12, marginBottom: 16, wordBreak: "break-all", fontSize: 13, color: "#c5f3ff", fontFamily: "monospace" }}>
              {createdKey.api_key}
            </div>
            <button
              onClick={handleCopy}
              style={{ width: "100%", padding: "10px 12px", borderRadius: 4, border: "none", background: copied ? "var(--cyan)" : "rgba(0,255,204,0.15)", color: copied ? "#001014" : "var(--cyan)", fontWeight: 600, cursor: "pointer" }}
            >
              <Copy size={16} style={{ verticalAlign: "middle", marginRight: 8 }} />
              {copied ? "Copied!" : "Copy to Clipboard"}
            </button>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: "var(--panel)", border: "1px solid var(--red)", borderRadius: 8, padding: 24, maxWidth: 420, width: "90%", boxShadow: "0 0 20px rgba(255,0,0,0.2)" }}>
            <h3 style={{ fontSize: 16, color: "var(--red)", margin: "0 0 16px" }}>Delete API Key?</h3>
            <div style={{ background: "rgba(0,10,17,0.8)", border: "1px solid var(--line)", borderRadius: 4, padding: 12, marginBottom: 16 }}>
              <p style={{ margin: "0 0 8px", fontSize: 13, color: "#c5f3ff" }}>
                <strong>Name:</strong> {deleteTarget.name}
              </p>
              <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
                <strong>Created:</strong> {new Date(deleteTarget.created_at).toLocaleString()}
              </p>
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
              This action cannot be undone. The key will be permanently removed.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                style={{ padding: "10px 16px", borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--muted)", cursor: "pointer" }}
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                disabled={deleting}
                style={{ padding: "10px 16px", borderRadius: 4, border: "none", background: "var(--red)", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}