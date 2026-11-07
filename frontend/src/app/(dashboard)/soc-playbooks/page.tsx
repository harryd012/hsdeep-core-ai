"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BookOpenCheck, Check, Copy, ShieldCheck } from "lucide-react";
import { getSop, listSops, type SOP, type SOPDetail } from "@/lib/api";
import { Banner, PageShell, Panel, Pill, SearchBox, titleCaseWords } from "@/components/ui/PageKit";
import {
  commandsFor,
  currentVersion,
  isSocPlaybook,
  matchesPlaybook,
  normalizeSteps,
  platformLabel,
  platformsOf,
  type PlaybookStep,
} from "@/lib/socPlaybooks";

/**
 * /soc-playbooks — the SOC analyst's standard procedures: the tenant's SOPs
 * tagged `soc` (firewall diagnostic, IPS/IDS triage, compromise, brute force,
 * malicious outbound, FortiGate scenarios). Everything shown comes from the
 * SOP Engine (/api/sops); edit the procedure there, not here.
 *
 * Deep link: /soc-playbooks?sop=SOP-017 opens that playbook.
 */
export default function SocPlaybooksPage() {
  const [sops, setSops] = useState<SOP[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, SOPDetail>>({});
  const [detailErrors, setDetailErrors] = useState<Record<string, string>>({});
  const [platform, setPlatform] = useState("all");

  useEffect(() => {
    let cancelled = false;
    listSops({ limit: 200 })
      .then((all) => {
        if (cancelled) return;
        const soc = all.filter(isSocPlaybook).sort((a, b) => a.sop_number.localeCompare(b.sop_number));
        setSops(soc);
        const wanted = new URLSearchParams(window.location.search).get("sop")?.toUpperCase();
        const first = soc.find((s) => s.sop_number === wanted) ?? soc[0];
        if (first) setSelectedId(first.id);
      })
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load SOPs"));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedId || detail[selectedId] || detailErrors[selectedId]) return;
    let cancelled = false;
    getSop(selectedId)
      .then((d) => !cancelled && setDetail((prev) => ({ ...prev, [selectedId]: d })))
      .catch((e: unknown) => !cancelled && setDetailErrors((prev) => ({ ...prev, [selectedId]: e instanceof Error ? e.message : "Failed to load the playbook" })));
    return () => { cancelled = true; };
  }, [selectedId, detail, detailErrors]);

  const visible = useMemo(() => (sops ?? []).filter((s) => matchesPlaybook(s, search)), [sops, search]);
  const selected = sops?.find((s) => s.id === selectedId) ?? null;
  const sopDetail = selectedId ? detail[selectedId] : undefined;
  const detailError = selectedId ? detailErrors[selectedId] : undefined;
  const version = sopDetail ? currentVersion(sopDetail.current_version_id, sopDetail.versions) : undefined;
  const steps = useMemo(() => normalizeSteps(version?.steps as Array<Record<string, unknown>> | undefined), [version]);
  const platforms = useMemo(() => platformsOf(steps), [steps]);
  const causes = (version?.possible_causes ?? []).filter(Boolean);

  function choose(sop: SOP) {
    // The platform choice carries over; effectivePlatform falls back to "all"
    // when the next playbook has no commands for it.
    setSelectedId(sop.id);
    window.history.replaceState(null, "", `?sop=${encodeURIComponent(sop.sop_number)}`);
  }

  const effectivePlatform = platform === "all" || platforms.includes(platform) ? platform : "all";

  return (
    <PageShell
      title="SOC Playbooks"
      icon={ShieldCheck}
      crumbs={[{ label: "SOC Analysis", href: "/soc-analysis" }, { label: "SOC Playbooks" }]}
      subtitle="Standard steps for firewall diagnostics, IPS/IDS triage and security incidents. Commands are version-dependent templates: validate on your release and model before running."
      actions={<Link className="pk-button" href="/sop-engine"><BookOpenCheck size={14} /> SOP Engine</Link>}
    >
      {error && <Banner tone="warn">SOC playbooks could not be loaded: {error}</Banner>}

      <div className="pk-grid">
        <Panel
          title="Playbooks"
          info="Active SOPs tagged soc in this tenant."
          aside={sops ? `${visible.length} of ${sops.length}` : undefined}
          state={sops ? (sops.length ? "ready" : "empty") : error ? "error" : "loading"}
          emptyText="No SOC playbooks in this tenant yet. Seed or import SOP-017 to SOP-022 in the SOP Engine."
          errorText={error ?? undefined}
          className="pk-col-4"
        >
          <SearchBox value={search} onChange={setSearch} placeholder="Search playbooks, vendors, tags…" label="Search playbooks" />
          {visible.length === 0 ? (
            <p className="pk-empty-note">No playbooks match “{search}”.</p>
          ) : (
            <ul className="pk-list sp-list">
              {visible.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    className="sp-pick"
                    aria-current={s.id === selectedId ? "true" : undefined}
                    onClick={() => choose(s)}
                  >
                    <span className="pk-cell-sub">{s.sop_number}</span>
                    <span className="pk-cell-main">{s.name}</span>
                    <span className="sp-pick__meta">
                      <Pill tone={s.category === "security" ? "bad" : "info"} dot={false}>{titleCaseWords(s.category)}</Pill>
                      {s.vendor && <Pill tone="neutral" dot={false}>{s.vendor}</Pill>}
                      {s.requires_approval && <Pill tone="warn" dot={false}>Approval</Pill>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title={selected ? `${selected.sop_number} · ${selected.name}` : "Playbook"}
          aside={version ? `v${version.version_number} · ${titleCaseWords(version.status)}` : undefined}
          state={!selected ? (sops ? "empty" : error ? "error" : "loading") : detailError ? "error" : sopDetail ? (steps.length ? "ready" : "empty") : "loading"}
          emptyText={selected ? "This SOP has no procedure steps." : "Select a playbook."}
          errorText={detailError ? `The playbook could not be loaded: ${detailError}` : undefined}
          className="pk-col-8"
        >
          {selected && (
            <>
              {selected.description && <p className="pk-help">{selected.description}</p>}
              <div className="pk-toolbar sp-facts">
                <Pill tone={selected.category === "security" ? "bad" : "info"}>{titleCaseWords(selected.category)}</Pill>
                {selected.device_type && <Pill tone="neutral">{titleCaseWords(selected.device_type)}</Pill>}
                {selected.vendor && <Pill tone="neutral">{selected.vendor}</Pill>}
                {selected.requires_approval ? <Pill tone="warn">Approval required</Pill> : <Pill tone="good">Read-only diagnostics</Pill>}
                {selected.estimated_resolution_minutes && <span className="pk-muted">~{selected.estimated_resolution_minutes} min</span>}
              </div>

              {platforms.length > 1 && (
                <label className="pk-field sp-platform">
                  <span>Show commands for</span>
                  <select className="pk-input" value={effectivePlatform} onChange={(e) => setPlatform(e.target.value)}>
                    <option value="all">All platforms</option>
                    {platforms.filter((p) => p !== "any").map((p) => (
                      <option key={p} value={p}>{platformLabel(p)}</option>
                    ))}
                  </select>
                </label>
              )}

              <ol className="sp-steps">
                {steps.map((s) => <StepCard key={s.order} step={s} platform={effectivePlatform} />)}
              </ol>

              {causes.length > 0 && (
                <>
                  <h3 className="pk-subhead">Possible causes</h3>
                  <ul className="sp-causes">{causes.map((c) => <li key={c}>{c}</li>)}</ul>
                </>
              )}
              {selected.rollback_procedure && (
                <>
                  <h3 className="pk-subhead">Rollback and safety</h3>
                  <p className="pk-help">{selected.rollback_procedure}</p>
                </>
              )}
            </>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}

function StepCard({ step, platform }: { step: PlaybookStep; platform: string }) {
  const cmds = commandsFor(step, platform);
  return (
    <li className="sp-step">
      <div className="sp-step__head">
        <span className="sp-step__num" aria-hidden="true">{step.order}</span>
        <p className="sp-step__action">{step.action}</p>
      </div>
      {step.expectedResult && (
        <p className="sp-step__expected"><b>Expected:</b> {step.expectedResult}</p>
      )}
      {step.warnings.map((w) => (
        <p key={w} className="sp-step__warn"><AlertTriangle size={13} aria-hidden="true" /> {w}</p>
      ))}
      {cmds.length > 0 && (
        <ul className="sp-cmds" aria-label={`Step ${step.order} commands`}>
          {cmds.map((c, i) => <CommandLine key={`${c.platform}-${i}`} platform={c.platform} command={c.command} disruptive={c.disruptive} />)}
        </ul>
      )}
      {step.matrix.length > 0 && (
        <details className="sp-matrix">
          <summary>If it fails ({step.matrix.length})</summary>
          <dl>
            {step.matrix.map((m) => (
              <div key={m.condition}>
                <dt>{m.condition}</dt>
                <dd>{m.meaning}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </li>
  );
}

function CommandLine({ platform, command, disruptive }: { platform: string; command: string; disruptive: boolean }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  }
  return (
    <li className={`sp-cmd${disruptive ? " sp-cmd--disruptive" : ""}`}>
      <span className="sp-cmd__platform">{platformLabel(platform)}</span>
      <code className="pk-mono sp-cmd__text">{command}</code>
      {disruptive && <Pill tone="bad" dot={false}>Change · approval</Pill>}
      <button type="button" className="sp-cmd__copy" onClick={copy} aria-label={`Copy command: ${command}`}>
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </li>
  );
}
