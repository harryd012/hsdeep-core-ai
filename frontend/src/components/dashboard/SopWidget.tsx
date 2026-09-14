"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, ArrowRight, FileText } from "lucide-react";
import Link from "next/link";
import { SOP, listSops } from "@/lib/api";
import SopModal from "@/components/SopModal";

const CATEGORY_COLORS: Record<string, string> = {
  availability: "var(--red)",
  performance: "var(--amber)",
  capacity: "#119be1",
  configuration: "var(--cyan)",
  security: "#a855f7",
  connectivity: "var(--green)",
  other: "var(--muted)",
};

export default function SopWidget() {
  const [sops, setSops] = useState<SOP[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSop, setSelectedSop] = useState<SOP | null>(null);

  const loadData = useCallback(async () => {
    try {
      setError(null);
      const data = await listSops({ limit: 500 });
      // Most recently updated first — recommendation_engine usage counts
      // aren't exposed via the API, so recency is the practical proxy.
      const sorted = [...data].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      setSops(sorted.slice(0, 6));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load SOPs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Fire immediately on mount — previously this effect ONLY registered a
    // 60s interval, so the widget showed shimmer skeletons for up to a full
    // minute before the first fetch ever ran (the "empty rows" bug).
    loadData();
    const timer = window.setInterval(loadData, 60000);
    return () => window.clearInterval(timer);
  }, [loadData]);


  const handleSaved = (updated: SOP) => {
    setSops((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    setSelectedSop(updated);
  };

  return (
    <div className="hud-panel" style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <BookOpen size={16} style={{ color: "var(--cyan)" }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Standard Operating Procedures
          </span>
        </div>
        <Link
          href="/sop-engine"
          style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--cyan)", textDecoration: "none" }}
        >
          View All <ArrowRight size={12} />
        </Link>
      </div>

      {loading ? (
        <div style={{ display: "grid", gap: 8 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} style={{ height: 42, borderRadius: 8, border: "1px solid var(--line)", background: "linear-gradient(90deg, rgba(255,255,255,0.02) 25%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.02) 75%)", backgroundSize: "200% 100%", animation: "shimmer 1.4s infinite" }} />
          ))}
        </div>
      ) : error ? (
        <p style={{ margin: 0, fontSize: 12, color: "var(--red)" }}>{error}</p>
      ) : sops.length === 0 ? (
        <p style={{ margin: "auto", fontSize: 12, color: "var(--muted)", textAlign: "center", padding: "20px 0" }}>
          No SOPs found for this tenant.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {sops.map((sop) => {
            const color = CATEGORY_COLORS[sop.category] ?? "var(--muted)";
            return (
              <button
                key={sop.id}
                onClick={() => setSelectedSop(sop)}
                style={{
                  display: "flex", alignItems: "center", gap: 10, width: "100%",
                  padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line)",
                  background: "rgba(2, 12, 27, 0.4)", color: "var(--text)",
                  cursor: "pointer", textAlign: "left",
                }}
              >
                <div style={{ width: 8, height: 8, borderRadius: 999, background: color, flexShrink: 0 }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {sop.name}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    {sop.sop_number}
                    {sop.estimated_resolution_minutes != null && ` · ~${sop.estimated_resolution_minutes}m`}
                  </div>
                </div>
                <FileText size={14} style={{ color: "var(--muted)", flexShrink: 0 }} />
              </button>
            );
          })}
        </div>
      )}

      {selectedSop && (
        <SopModal
          key={selectedSop.id}
          sop={selectedSop}
          onClose={() => setSelectedSop(null)}
          onSaved={handleSaved}
        />
      )}

      <style jsx global>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}