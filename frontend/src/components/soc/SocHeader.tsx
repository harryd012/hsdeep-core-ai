"use client";

/** SocHeader — global top bar: brand, global search, scope/time selectors, user. */
import { Search, Bell, ChevronDown, Shield } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth-context";

export default function SocHeader({ openAlerts }: { openAlerts: number | null }) {
  const { user, tenantId } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");

  function submit() {
    if (q.trim()) router.push(`/alerts?q=${encodeURIComponent(q.trim())}`);
  }

  return (
    <header className="soc-topbar">
      <div className="soc-topbar__brand">
        <span className="soc-topbar__logo"><Shield size={22} /></span>
        <div className="soc-topbar__brand-text">
          <b>HSDEEP <span>CORE AI</span></b>
          <small>AI-NATIVE IT OPERATIONS</small>
        </div>
      </div>

      <form
        className="soc-topbar__search"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <Search size={15} />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask HSDEEP AI... Search incidents, devices, users, IPs, domains..."
          aria-label="Global security search"
        />
        <kbd>Ctrl K</kbd>
      </form>

      <div className="soc-topbar__right">
        <button type="button" className="soc-topbar__select" title="Active tenant">
          Scope: {tenantId.slice(0, 8)} <ChevronDown size={13} />
        </button>
        <button type="button" className="soc-topbar__select">Last 24 hours <ChevronDown size={13} /></button>
        <button type="button" className="soc-topbar__bell" aria-label="Notifications">
          <Bell size={17} />
          {openAlerts !== null && openAlerts > 0 && <i>{openAlerts > 99 ? "99+" : openAlerts}</i>}
        </button>
        <div className="soc-topbar__user">
          <span className="soc-topbar__avatar">
            {(user?.full_name || user?.email || "OP").slice(0, 2).toUpperCase()}
          </span>
          <div className="soc-topbar__user-meta">
            <b>{user?.full_name ?? user?.email ?? "Operator"}</b>
            <small>{user?.roles?.[0] ?? "—"}</small>
          </div>
          <ChevronDown size={14} />
        </div>
      </div>
    </header>
  );
}
