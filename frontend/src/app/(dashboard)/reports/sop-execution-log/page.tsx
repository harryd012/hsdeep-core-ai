"use client";

import { useEffect, useState } from "react";
import ReportsBackButton from "@/components/reports/ReportsBackButton";
import { apiFetch, DEFAULT_TENANT_ID } from "@/lib/api";

interface SOPVersion {
  id: string;
  version_number: number;
  status: string;
  approved_at: string | null;
  created_at: string;
}

interface SOP {
  id: string;
  title: string;
  category: string;
  current_version_id: string | null;
  versions: SOPVersion[];
}

export default function SOPExecutionLogPage() {
  const [sops, setSOPs] = useState<SOP[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch<SOP[]>(`/api/sops?tenant_id=${DEFAULT_TENANT_ID}&limit=50`);
        if (!cancelled) {
          setSOPs(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load SOP data");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const totalVersions = sops.reduce((sum, sop) => sum + sop.versions.length, 0);
  const approvedVersions = sops.reduce((sum, sop) => sum + sop.versions.filter((v) => v.status === "approved").length, 0);

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <ReportsBackButton />
            <h2>SOP EXECUTION LOG</h2>
          </div>
          {error && <p style={{ color: "var(--red)", fontSize: 12 }}>{error}</p>}
          {!loaded && <p style={{ color: "var(--muted)", fontSize: 12 }}>Loading…</p>}
          {loaded && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16, marginBottom: 16 }}>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{sops.length}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Total SOPs</div>
                </div>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--cyan)", fontWeight: 700 }}>{totalVersions}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Total Versions</div>
                </div>
                <div style={{ padding: 16, border: "1px solid var(--line)", borderRadius: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 28, color: "var(--green)", fontWeight: 700 }}>{approvedVersions}</div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>Approved Versions</div>
                </div>
              </div>
              {sops.length > 0 && (
                <div>
                  <h3 style={{ color: "#c5f3ff", fontSize: 13, marginBottom: 8 }}>SOP VERSIONS</h3>
                  <div style={{ maxHeight: 400, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
                    {sops.flatMap((sop) =>
                      sop.versions.map((v) => (
                        <div key={v.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
                          <span style={{ color: "#c5f3ff", fontSize: 12 }}>{sop.title}</span>
                          <span style={{ color: "var(--muted)", fontSize: 11 }}>v{v.version_number}</span>
                          <span style={{ color: v.status === "approved" ? "var(--green)" : "var(--amber)", fontSize: 11 }}>{v.status}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}