"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { titleCaseWords } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import { bulkHostAction, getHostVocabulary, type HostBulkAction, type HostBulkResult } from "@/lib/api";

/**
 * Bulk actions on the selected hosts (POST /api/explorer/hosts/bulk).
 * Classification needs devices.write; pausing or resuming monitoring needs
 * monitoring.update and is confirmed first, because a paused sensor raises no
 * alerts and its open alert is resolved. Pausing here does not pause the check
 * in the vendor's own system (PRTG, Meraki ...).
 */

const ACTIONS: { key: HostBulkAction; label: string; needs: "devices.write" | "monitoring.update" }[] = [
  { key: "set_criticality", label: "Set criticality", needs: "devices.write" },
  { key: "set_environment", label: "Set environment", needs: "devices.write" },
  { key: "add_tags", label: "Add tags", needs: "devices.write" },
  { key: "remove_tags", label: "Remove tags", needs: "devices.write" },
  { key: "pause_monitoring", label: "Pause monitoring", needs: "monitoring.update" },
  { key: "resume_monitoring", label: "Resume monitoring", needs: "monitoring.update" },
];
const CLEAR = "__clear__";

export function useBulkPermissions() {
  const { user } = useAuth();
  const has = (p: string) => !!user && (user.is_superuser || user.permissions.includes(p));
  return ACTIONS.filter((a) => has(a.needs));
}

function summary(r: HostBulkResult): string {
  const hosts = (n: number) => `${n} host${n === 1 ? "" : "s"}`;
  let text =
    r.action === "pause_monitoring" || r.action === "resume_monitoring"
      ? `${r.action === "pause_monitoring" ? "Paused" : "Resumed"} ${r.sensors_changed} sensor${r.sensors_changed === 1 ? "" : "s"} on ${hosts(r.changed)}.`
      : `Changed ${hosts(r.changed)}; ${hosts(r.matched - r.changed)} already had it.`;
  if (r.tag_limit_hit) text += ` ${hosts(r.tag_limit_hit)} skipped: tag limit reached.`;
  if (r.not_found.length) text += ` ${hosts(r.not_found.length)} no longer exist.`;
  return text;
}

export default function HostBulkBar({
  selected,
  onClear,
  onDone,
}: {
  selected: string[];
  onClear: () => void;
  onDone: (message: { tone: "good" | "bad"; text: string }) => void;
}) {
  const actions = useBulkPermissions();
  const [action, setAction] = useState<HostBulkAction | "">("");
  const [value, setValue] = useState("");
  const [tags, setTags] = useState("");
  const [confirming, setConfirming] = useState(false);
  const vocab = useQuery({ queryKey: ["explorer", "hosts", "vocabulary"], queryFn: getHostVocabulary, staleTime: Infinity });

  const run = useMutation({
    mutationFn: () =>
      bulkHostAction({
        device_ids: selected,
        action: action as HostBulkAction,
        ...(action === "set_criticality" || action === "set_environment" ? { value: value === CLEAR ? null : value } : {}),
        ...(action === "add_tags" || action === "remove_tags"
          ? { tags: tags.split(",").map((t) => t.trim()).filter(Boolean) }
          : {}),
      }),
    onSuccess: (r) => {
      onDone({ tone: "good", text: summary(r) });
      setAction("");
      setValue("");
      setTags("");
      setConfirming(false);
    },
    onError: (e) => {
      onDone({ tone: "bad", text: `The action failed: ${e instanceof Error ? e.message : "request failed"}` });
      setConfirming(false);
    },
  });

  const monitoring = action === "pause_monitoring" || action === "resume_monitoring";
  const choices = action === "set_criticality" ? vocab.data?.criticality : action === "set_environment" ? vocab.data?.environment : undefined;
  const ready =
    !!action &&
    (choices ? value !== "" : action === "add_tags" || action === "remove_tags" ? tags.split(",").some((t) => t.trim()) : true);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    if (monitoring && !confirming) setConfirming(true);
    else run.mutate();
  }

  const n = selected.length;
  return (
    <form className="hx-bulk" onSubmit={submit} aria-label="Bulk actions">
      <strong>{n} selected</strong>
      <label className="ecc-sr-only" htmlFor="hx-bulk-action">Action</label>
      <select id="hx-bulk-action" className="pk-input" value={action} disabled={run.isPending}
        onChange={(e) => { setAction(e.target.value as HostBulkAction | ""); setValue(""); setConfirming(false); }}>
        <option value="">Choose an action…</option>
        {actions.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
      </select>
      {choices && (
        <>
          <label className="ecc-sr-only" htmlFor="hx-bulk-value">Value</label>
          <select id="hx-bulk-value" className="pk-input" value={value} onChange={(e) => setValue(e.target.value)} disabled={run.isPending}>
            <option value="">Choose…</option>
            {choices.map((c) => <option key={c} value={c}>{c === "dr" ? "DR" : titleCaseWords(c)}</option>)}
            <option value={CLEAR}>Not set (clear)</option>
          </select>
        </>
      )}
      {(action === "add_tags" || action === "remove_tags") && (
        <>
          <label className="ecc-sr-only" htmlFor="hx-bulk-tags">Tags</label>
          <input id="hx-bulk-tags" className="pk-input" placeholder="core, pci" value={tags} maxLength={500} onChange={(e) => setTags(e.target.value)} disabled={run.isPending} />
        </>
      )}
      {confirming ? (
        <span className="hx-bulk__confirm" role="alert">
          {action === "pause_monitoring"
            ? `Pause every active sensor on ${n} host${n === 1 ? "" : "s"}? They raise no alerts while paused and open alerts resolve. The vendor's own check keeps running.`
            : `Resume every paused sensor on ${n} host${n === 1 ? "" : "s"}? Disabled sensors stay disabled.`}
          <button type="submit" className="pk-button pk-button--primary" style={{ height: 28 }} disabled={run.isPending}>Confirm</button>
          <button type="button" className="pk-button" style={{ height: 28 }} onClick={() => setConfirming(false)}>Cancel</button>
        </span>
      ) : (
        <button type="submit" className="pk-button pk-button--primary" style={{ height: 30 }} disabled={!ready || run.isPending}>
          {run.isPending ? "Applying…" : "Apply"}
        </button>
      )}
      <button type="button" className="hx-bulk__clear" onClick={onClear} aria-label="Clear selection">
        <X size={14} aria-hidden="true" /> Clear
      </button>
    </form>
  );
}
