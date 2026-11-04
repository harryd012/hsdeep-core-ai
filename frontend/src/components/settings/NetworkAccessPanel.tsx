"use client";

/**
 * Network access (enterprise security programme S3): the networks this
 * organization's users and API keys may connect from. No rules = open to every
 * network. The backend refuses any change that would block the admin's own
 * address, so this panel cannot lock the organization out.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Plus, Trash2 } from "lucide-react";
import { Banner, Panel, Pill, ago } from "@/components/ui/PageKit";
import { addIpRule, getMyIp, listIpRules, removeIpRule } from "@/lib/security";

export default function NetworkAccessPanel() {
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["settings", "ip-rules"], queryFn: listIpRules });
  const me = useQuery({ queryKey: ["settings", "my-ip"], queryFn: getMyIp, staleTime: 60_000 });
  const [cidr, setCidr] = useState("");
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["settings", "ip-rules"] });
  const fail = (e: unknown) => setMessage({ tone: "bad", text: e instanceof Error ? e.message : "The request failed." });

  const add = useMutation({
    mutationFn: () => addIpRule(cidr.trim(), label.trim()),
    onSuccess: (r) => {
      setMessage({ tone: "good", text: `${r.cidr} is allowed.` });
      setCidr("");
      setLabel("");
      refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => removeIpRule(id),
    onSuccess: () => { setMessage(null); refresh(); },
    onError: fail,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (cidr.trim()) add.mutate();
  }

  const list = rules.data ?? [];
  const myIp = me.data?.ip_address;
  const busy = add.isPending || remove.isPending;

  return (
    <Panel
      title="Network access"
      info="Limit sign-in, the app and API keys to your offices and VPN. Applies to your organization's users, not to HSDEEP support under a break-glass grant."
      state={rules.data ? "ready" : rules.isError ? "error" : "loading"}
      onRetry={() => rules.refetch()}
      skeletonHeight={160}
      aside={rules.data && (list.length ? <Pill tone="info">Restricted</Pill> : <Pill tone="neutral">Any network</Pill>)}
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      <p className="sec-help">
        {list.length
          ? "Only the networks below can be used. Everyone else is refused, even with the right password."
          : "Your organization can be used from any network. Add your office or VPN ranges to restrict it."}
        {myIp && <> You are connecting from <code>{myIp}</code>.</>}
      </p>
      {list.length > 0 && (
        <ul className="sec-sessions">
          {list.map((r) => (
            <li key={r.id}>
              <span className="sec-sessions__icon" aria-hidden="true"><Globe size={16} /></span>
              <span className="sec-sessions__main">
                <b><code>{r.cidr}</code>{r.label && <span className="pk-muted" style={{ fontWeight: 400 }}>{r.label}</span>}</b>
                <small>added {ago(r.created_at)}</small>
              </span>
              <button type="button" className="pk-button" disabled={busy} aria-label={`Remove ${r.cidr}`}
                onClick={() => { if (window.confirm(`Stop allowing ${r.cidr}?`)) remove.mutate(r.id); }}>
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form className="net-form" onSubmit={submit}>
        <label className="ecc-sr-only" htmlFor="net-cidr">Address or range</label>
        <input id="net-cidr" className="pk-input" placeholder={myIp ? `e.g. ${myIp} or 10.0.0.0/8` : "e.g. 203.0.113.0/24"}
          value={cidr} onChange={(e) => setCidr(e.target.value)} maxLength={64} disabled={busy} />
        <label className="ecc-sr-only" htmlFor="net-label">Label</label>
        <input id="net-label" className="pk-input" placeholder="Label (Office, VPN …)" value={label}
          onChange={(e) => setLabel(e.target.value)} maxLength={100} disabled={busy} />
        <button type="submit" className="pk-button pk-button--primary" disabled={busy || !cidr.trim()}>
          <Plus size={14} aria-hidden="true" /> Allow
        </button>
      </form>
      {!list.length && myIp && (
        <button type="button" className="ecc-link sec-more" disabled={busy} onClick={() => { setCidr(myIp); setLabel("My current network"); }}>
          Use my current address
        </button>
      )}
    </Panel>
  );
}
