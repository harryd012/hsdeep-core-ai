"use client";

/** ActiveSecurityIncidents — ID / Title / Severity / Status / Time table. */
import Link from "next/link";
import type { SocIncidentRow } from "@/lib/soc/socModels";
import { sevColor } from "@/lib/soc/socAdapter";

export default function ActiveSecurityIncidents({ incidents, state }: {
  incidents: SocIncidentRow[];
  state: "loading" | "success" | "empty" | "error";
}) {
  return (
    <div className="soc-panel soc-incidents">
      <div className="soc-panel__head"><h2>Active Security Incidents</h2></div>
      {state === "loading" && <div className="soc-empty">Loading incidents…</div>}
      {state === "error" && <div className="soc-empty soc-empty--error">Incidents unavailable — backend error.</div>}
      {state === "empty" && <div className="soc-empty">No open incidents. 🎉</div>}
      {state === "success" && (
        <table className="soc-table">
          <thead>
            <tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Time</th></tr>
          </thead>
          <tbody>
            {incidents.map((i) => (
              <tr key={i.id}>
                <td className="soc-table__ref">{i.ref}</td>
                <td className="soc-table__title">{i.title}</td>
                <td><span className="soc-badge" style={{ color: sevColor(i.severity), borderColor: sevColor(i.severity) }}>{i.severity}</span></td>
                <td className="soc-table__status">{i.status}</td>
                <td className="soc-table__time">{i.time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Link href="/incidents" className="soc-panel__link">View All →</Link>
    </div>
  );
}
