"use client";

/** AssetsHealth — compact per-asset-class health table. */
import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import type { SocAssetHealth } from "@/lib/soc/socModels";

export default function AssetsHealth({ assets }: { assets: SocAssetHealth[] }) {
  return (
    <div className="soc-panel soc-assets">
      <div className="soc-panel__head"><h2>Assets Health</h2></div>
      <table className="soc-table">
        <thead>
          <tr><th>Asset</th><th></th><th>Status</th><th>Availability</th></tr>
        </thead>
        <tbody>
          {assets.map((a) => (
            <tr key={a.name}>
              <td className="soc-table__title">{a.name}</td>
              <td>
                {a.status === "healthy" && <CheckCircle2 size={14} className="soc-asset-ok" />}
                {a.status === "warning" && <AlertTriangle size={14} className="soc-asset-warn" />}
                {a.status === "critical" && <XCircle size={14} className="soc-asset-crit" />}
              </td>
              <td className={`soc-asset-status soc-asset-status--${a.status}`}>
                {a.status === "unknown" ? "no data" : a.status}
              </td>
              <td className="soc-table__time">{a.availability}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
