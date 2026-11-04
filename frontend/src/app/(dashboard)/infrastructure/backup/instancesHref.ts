import type { BackupFilters } from "@/lib/api";

/** Link to the Backup Instances inventory with Command Center filters (server-side)
 *  and inventory filters (`rpo`, `result`, `protection` ...) in the query string. */
export function instancesHref(filters: BackupFilters, view: Record<string, string> = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) qs.set(k, v);
  for (const [k, v] of Object.entries(view)) if (v) qs.set(k, v);
  const q = qs.toString();
  return `/infrastructure/backup/instances${q ? `?${q}` : ""}`;
}
