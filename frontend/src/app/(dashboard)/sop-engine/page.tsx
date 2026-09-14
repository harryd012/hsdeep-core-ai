"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BookOpen, ChevronDown, ChevronRight, Clock, FileText,
  Loader2, RefreshCw, Search,
} from "lucide-react";
import { getSop, listSops, type SOP, type SOPVersion } from "@/lib/api";
import { SOP_CATEGORY_COLORS, SOP_SEED } from "@/lib/mock/sop-seed";

export default function SopEnginePage() {
  return <SopEngine />;
}


function SopEngine() {
  const [sops, setSops] = useState<SOP[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usingSeed, setUsingSeed] = useState(false);
  const [search, setSearch] = useState("");
  const [activeCat, setActiveCat] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [detailSteps, setDetailSteps] = useState<Record<string, string[]>>({});

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await listSops({ limit: 200 });
      if (data.length > 0) {
        setSops(data);
        setUsingSeed(false);
      } else {
        // Tenant has no SOPs seeded yet — fall back to the sample library so
        // the grid stays demonstrable instead of rendering a blank page.
        setSops(SOP_SEED);
        setUsingSeed(true);
      }
    } catch {
      setError("Could not reach the SOP API — showing the built-in sample library.");
      setSops(SOP_SEED);
      setUsingSeed(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const s of sops) map[s.category] = (map[s.category] ?? 0) + 1;
    return map;
  }, [sops]);

  const CATEGORIES = ["availability", "performance", "capacity", "configuration", "security", "connectivity", "other"];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sops.filter((s) => {
      if (activeCat !== "all" && s.category !== activeCat) return false;
      if (!q) return true;
      return (
        s.name.toLowerCase().includes(q) ||
        s.sop_number.toLowerCase().includes(q) ||
        s.tags.some((t) => t.toLowerCase().includes(q))
      );
    });
  }, [sops, search, activeCat]);

  async function toggleView(sop: SOP) {
    if (expandedId === sop.id) { setExpandedId(null); return; }
    setExpandedId(sop.id);
    if (detailSteps[sop.id]) return;
    setDetailLoading(sop.id);
    try {
      const detail = await getSop(sop.id);
      const v: SOPVersion | undefined = detail.versions?.[detail.versions.length - 1];
      const steps = (v?.steps ?? []).map((st) => st.action ?? `Step ${st.order ?? ""}`).filter(Boolean);
      setDetailSteps((prev) => ({ ...prev, [sop.id]: steps }));
    } catch {
      setDetailSteps((prev) => ({ ...prev, [sop.id]: [] }));
    } finally {
      setDetailLoading(null);
    }
  }

  function fmtMinutes(m: number | null): string {
    if (!m) return "~—";
    return m < 60 ? `~${m}m` : `~${Math.floor(m / 60)}h ${m % 60}m`;
  }

  const total = sops.length;

  if (loading && sops.length === 0) {
    return (
      <div className="page-shell flex items-center justify-center min-h-screen bg-gray-50">
        <div className="flex items-center gap-3 text-gray-500">
          <Loader2 size={22} className="animate-spin" /> Loading SOPs…
        </div>
      </div>
    );
  }

  return (
    <div className="page-shell min-h-screen bg-gray-50" style={{ paddingBottom: "var(--copilot-bar-height)" }}>
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between gap-4 py-6">
          <div>
            <h1 className="text-2xl font-bold tracking-wide text-gray-900">SOP ENGINE</h1>
            <p className="mt-1 text-sm text-gray-500">
              Standard Operating Procedures — {total} total{usingSeed ? " (sample library)" : ""}
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border border-cyan-600 bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-700 disabled:opacity-60"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            Refresh
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-5 py-6">
        {error && (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">{error}</div>
        )}

        {/* Search */}
        <div className="relative max-w-xl">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search SOPs by name, tag, or number…"
            className="w-full rounded-md border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm text-gray-900 placeholder:text-gray-400 focus:border-cyan-500 focus:outline-none"
          />
        </div>

        {/* Category filter pills */}
        <div className="flex flex-wrap items-center gap-2">
          <FilterPill label="All" count={total} active={activeCat === "all"} onClick={() => setActiveCat("all")} />
          {CATEGORIES.map((c) => (
            <FilterPill
              key={c}
              label={c.charAt(0).toUpperCase() + c.slice(1)}
              count={counts[c] ?? 0}
              color={SOP_CATEGORY_COLORS[c]}
              active={activeCat === c}
              onClick={() => setActiveCat(c)}
            />
          ))}
        </div>

        {/* Grid / empty state */}
        {filtered.length === 0 ? (
          <div className="rounded-lg border border-gray-200 bg-white p-10 text-center">
            <BookOpen size={32} className="mx-auto mb-3 text-gray-300" />
            <p className="text-sm text-gray-500">No SOPs match your search or filter.</p>
          </div>
        ) : (
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
            {filtered.map((sop) => {
              const open = expandedId === sop.id;
              const steps = detailSteps[sop.id];
              return (
                <div key={sop.id} className="flex flex-col rounded-lg border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-cyan-400">
                  <div className="flex items-start justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-600">
                      <FileText size={14} style={{ color: SOP_CATEGORY_COLORS[sop.category] ?? "#94a3b8" }} />
                      {sop.sop_number}
                    </span>
                    <span
                      className="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                      style={{
                        color: SOP_CATEGORY_COLORS[sop.category] ?? "#94a3b8",
                        background: `${SOP_CATEGORY_COLORS[sop.category] ?? "#94a3b8"}1a`,
                      }}
                    >
                      {sop.category}
                    </span>
                  </div>

                  <h3 className="mt-2 text-sm font-bold leading-snug text-gray-900">{sop.name}</h3>
                  <p className="mt-1 line-clamp-2 text-xs text-gray-500">{sop.description}</p>

                  <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-gray-500">
                    <span className="inline-flex items-center gap-1">
                      <FileText size={12} />
                      {(sop.versions?.[0]?.steps.length ?? 0)} steps
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Clock size={12} />
                      {fmtMinutes(sop.estimated_resolution_minutes)}
                    </span>
                    {sop.requires_approval && (
                      <span className="rounded border border-purple-300 bg-purple-50 px-1.5 py-0.5 text-[10px] font-semibold text-purple-700">
                        Approval
                      </span>
                    )}
                  </div>

                  {open && (
                    <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-3">
                      {detailLoading === sop.id ? (
                        <p className="flex items-center gap-2 text-xs text-gray-500"><Loader2 size={12} className="animate-spin" /> Loading steps…</p>
                      ) : steps === undefined || steps.length === 0 ? (
                        <p className="text-xs text-gray-400">Step details unavailable{(steps !== undefined) ? " for this SOP." : " right now."}</p>
                      ) : (
                        <ol className="list-decimal space-y-1 pl-4 text-xs text-gray-700">
                          {steps.map((s, i) => <li key={i}>{s}</li>)}
                        </ol>
                      )}
                    </div>
                  )}

                  <div className="mt-auto flex gap-2 pt-3">
                    <button
                      onClick={() => toggleView(sop)}
                      className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:border-cyan-500 hover:text-cyan-700"
                    >
                      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      View
                    </button>
                    <button
                      disabled
                      title="SOP editing is not wired up yet"
                      className="inline-flex cursor-not-allowed items-center gap-1 rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-400"
                    >
                      Edit
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function FilterPill({ label, count, active, onClick, color }: {
  label: string; count: number; active: boolean; onClick: () => void; color?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
        active
          ? "border-cyan-600 bg-cyan-600 text-white"
          : "border-gray-300 bg-white text-gray-600 hover:border-cyan-400 hover:text-cyan-700"
      }`}
    >
      {label}{" "}
      <span className={active ? "text-cyan-100" : "text-gray-400"}>({count})</span>
      {color && !active && <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full align-middle" style={{ background: color }} />}
    </button>
  );
}
