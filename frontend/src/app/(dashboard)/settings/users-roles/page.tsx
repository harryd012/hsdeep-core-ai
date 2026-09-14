"use client";

import { useEffect, useState } from "react";
import { Users, Shield, Plus, Trash2, Edit3, Check, X } from "lucide-react";
import {
  listUsers,
  listRoles,
  updateUser,
  deleteUser,
  assignUserRoles,
  UserOut,
  RoleOut,
  apiFetch,
  ApiError,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import SettingsBackButton from "@/components/settings/SettingsBackButton";

type ModalMode = "invite" | "edit" | "delete" | null;

export default function UsersRolesPage() {
  const { tenantId } = useAuth();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [roles, setRoles] = useState<RoleOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  // Modal state
  const [modal, setModal] = useState<ModalMode>(null);
  const [targetUser, setTargetUser] = useState<UserOut | null>(null);
  const [formEmail, setFormEmail] = useState("");
  const [formName, setFormName] = useState("");
  const [formPassword, setFormPassword] = useState("");
  const [formRoleIds, setFormRoleIds] = useState<string[]>([]);
  const [formActive, setFormActive] = useState(true);

  async function load() {
    setError(null);
    try {
      const [usersData, rolesData] = await Promise.all([
        listUsers(tenantId),
        listRoles(tenantId),
      ]);
      setUsers(usersData);
      setRoles(rolesData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users and roles");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      load().catch(() => {
        if (!cancelled) setLoaded(true);
      });
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  function openInvite() {
    setTargetUser(null);
    setFormEmail("");
    setFormName("");
    setFormPassword("");
    setFormRoleIds([]);
    setFormActive(true);
    setModal("invite");
  }

  function openEdit(user: UserOut) {
    setTargetUser(user);
    setFormName(user.full_name || "");
    setFormActive(user.is_active);
    setFormRoleIds([]);
    setFormPassword("");
    setModal("edit");
  }

  function openDelete(user: UserOut) {
    setTargetUser(user);
    setModal("delete");
  }

  async function handleInvite() {
    if (!formEmail.trim()) return;
    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(formEmail.trim())) {
      setError("The email address is invalid.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Use apiFetch which adds the Authorization Bearer header from the
      // persisted JWT token. A raw fetch() here was the root cause of the
      // "Not authorized" response — the create_user endpoint requires
      // require_permission("users.create") which depends on get_current_user.
      const res = await apiFetch<UserOut>(
        `/api/auth/users?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          body: JSON.stringify({
            email: formEmail.trim(),
            password: formPassword || undefined,
            full_name: formName.trim() || undefined,
            is_superuser: false,
            role_ids: formRoleIds,
          }),
        },
      );
      setModal(null);
      await load();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to invite user";
      // Distinguish 401/403 from other failures for clearer UX.
      if (msg.includes("Not authenticated") || msg.includes("Invalid or expired token")) {
        setError("Your session has expired. Please sign in again.");
      } else if (msg.includes("Missing required permission") || msg.includes("Not authorized")) {
        setError("You do not have permission to invite users.");
      } else {
        setError(msg);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleEditSave() {
    if (!targetUser) return;
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {};
      if (formName !== (targetUser.full_name || "")) payload.full_name = formName || null;
      if (formActive !== targetUser.is_active) payload.is_active = formActive;
      if (Object.keys(payload).length > 0) {
        await updateUser(tenantId, targetUser.id, payload);
      }
      if (formRoleIds.length > 0) {
        await assignUserRoles(tenantId, targetUser.id, formRoleIds);
      }
      setModal(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteConfirm() {
    if (!targetUser) return;
    setSaving(true);
    setError(null);
    try {
      await deleteUser(tenantId, targetUser.id);
      setModal(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete user");
    } finally {
      setSaving(false);
    }
  }

  function toggleRole(code: string) {
    setFormRoleIds((prev) =>
      prev.includes(code) ? prev.filter((s) => s !== code) : [...prev, code]
    );
  }

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <SettingsBackButton />
            <h2>USERS & ROLES</h2>
          </div>

          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}

          <div style={{ display: "grid", gap: 12, marginBottom: 12 }}>
            <button
              onClick={openInvite}
              style={{ background: "var(--cyan)", color: "#001a1f", border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 12, fontWeight: 600, cursor: "pointer", width: "fit-content" }}
            >
              <Plus size={14} style={{ marginRight: 6 }} />
              Invite User
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
            <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16 }}>
              <h3 style={{ display: "flex", alignItems: "center", gap: 8, color: "#c5f3ff", fontSize: 14, marginBottom: 12 }}>
                <Users size={16} /> Users ({users.length})
              </h3>
              {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading users…</p>}
              {loaded && users.length === 0 && (
                <p style={{ color: "var(--muted)", fontSize: 12 }}>No users found.</p>
              )}
              <div style={{ display: "grid", gap: 8 }}>
                {users.map((u) => (
                  <div key={u.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                    <div style={{ width: 32, height: 32, borderRadius: "50%", background: "rgba(1,10,17,0.8)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--cyan)", fontSize: 12 }}>
                      {u.full_name ? u.full_name[0].toUpperCase() : u.email[0].toUpperCase()}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ color: "#c5f3ff", fontSize: 13 }}>{u.full_name || u.email}</div>
                      <div style={{ color: "var(--muted)", fontSize: 11 }}>{u.email} {u.is_superuser ? "(superuser)" : ""} {u.is_active ? "" : "(inactive)"}</div>
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button onClick={() => openEdit(u)} style={{ padding: 4, borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--cyan)", cursor: "pointer" }}><Edit3 size={14} /></button>
                      <button onClick={() => openDelete(u)} style={{ padding: 4, borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--red)", cursor: "pointer" }}><Trash2 size={14} /></button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16 }}>
              <h3 style={{ display: "flex", alignItems: "center", gap: 8, color: "#c5f3ff", fontSize: 14, marginBottom: 12 }}>
                <Shield size={16} /> Roles ({roles.length})
              </h3>
              {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading roles…</p>}
              {loaded && roles.length === 0 && (
                <p style={{ color: "var(--muted)", fontSize: 12 }}>No roles configured.</p>
              )}
              <div style={{ display: "grid", gap: 8 }}>
                {roles.map((r) => (
                  <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                    <div style={{ color: "#c5f3ff", fontSize: 13 }}>{r.name}</div>
                    <div style={{ color: "var(--muted)", fontSize: 11 }}>{r.description}</div>
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                      {r.permission_codes.slice(0, 6).map((p: string) => (
                        <span key={p} style={{ fontSize: 10, padding: "2px 6px", border: "1px solid var(--line)", borderRadius: 4, color: "var(--cyan)" }}>{p}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* Invite / Edit modal */}
      {modal === "invite" || modal === "edit" ? (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: "var(--panel)", border: "1px solid var(--cyan)", borderRadius: 8, padding: 24, maxWidth: 520, width: "90%" }}>
            <h3 style={{ margin: "0 0 16px", color: "var(--cyan)" }}>{modal === "invite" ? "Invite User" : "Edit User"}</h3>
            <div style={{ display: "grid", gap: 12 }}>
              {modal === "invite" && (
                <>
                  <input placeholder="Email" value={formEmail} onChange={(e) => setFormEmail(e.target.value)} style={{ background: "rgba(0,0,0,0.35)", border: "1px solid var(--line)", borderRadius: 6, color: "#e8f8ff", padding: "10px 12px", fontSize: 14, outline: "none" }} />
                  <input type="password" placeholder="Initial password" value={formPassword} onChange={(e) => setFormPassword(e.target.value)} style={{ background: "rgba(0,0,0,0.35)", border: "1px solid var(--line)", borderRadius: 6, color: "#e8f8ff", padding: "10px 12px", fontSize: 14, outline: "none" }} />
                </>
              )}
              <input placeholder="Full name" value={formName} onChange={(e) => setFormName(e.target.value)} style={{ background: "rgba(0,0,0,0.35)", border: "1px solid var(--line)", borderRadius: 6, color: "#e8f8ff", padding: "10px 12px", fontSize: 14, outline: "none" }} />
              <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#c5f3ff", fontSize: 12, cursor: "pointer" }}>
                <input type="checkbox" checked={formActive} onChange={(e) => setFormActive(e.target.checked)} style={{ accentColor: "var(--cyan)" }} />
                Active
              </label>
              <div>
                <label style={{ color: "#9dc5d4", fontSize: 12, fontWeight: 500, display: "block", marginBottom: 6 }}>Roles</label>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 4 }}>
                  {roles.map((r) => {
                    const checked = formRoleIds.includes(r.id);
                    return (
                      <label key={r.id} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#c5f3ff", cursor: "pointer" }}>
                        <input type="checkbox" checked={checked} onChange={() => toggleRole(r.id)} style={{ accentColor: "var(--cyan)", width: 14, height: 14 }} />
                        <span>{r.name}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end", marginTop: 20 }}>
              <button onClick={() => setModal(null)} disabled={saving} style={{ padding: "10px 16px", borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--muted)", cursor: "pointer" }}>Cancel</button>
              <button onClick={modal === "invite" ? handleInvite : handleEditSave} disabled={saving || (modal === "invite" && !formEmail.trim())} style={{ padding: "10px 16px", borderRadius: 4, border: "none", background: saving ? "rgba(0,255,255,0.4)" : "var(--cyan)", color: "#001a1f", fontWeight: 600, cursor: "pointer", opacity: saving ? 0.6 : 1 }}>
                {saving ? "Saving…" : modal === "invite" ? "Invite" : "Save"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Delete confirm modal */}
      {modal === "delete" && targetUser ? (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: "var(--panel)", border: "1px solid var(--red)", borderRadius: 8, padding: 24, maxWidth: 420, width: "90%" }}>
            <h3 style={{ margin: "0 0 16px", color: "var(--red)" }}>Remove User?</h3>
            <p style={{ fontSize: 13, color: "#c5f3ff", marginBottom: 8 }}>
              <strong>Email:</strong> {targetUser.email}
            </p>
            <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
              This deactivates the user and revokes all sessions. This cannot be undone.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button onClick={() => setModal(null)} disabled={saving} style={{ padding: "10px 16px", borderRadius: 4, border: "1px solid var(--line)", background: "transparent", color: "var(--muted)", cursor: "pointer" }}>Cancel</button>
              <button onClick={handleDeleteConfirm} disabled={saving} style={{ padding: "10px 16px", borderRadius: 4, border: "none", background: "var(--red)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>
                {saving ? "Removing…" : "Remove"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}