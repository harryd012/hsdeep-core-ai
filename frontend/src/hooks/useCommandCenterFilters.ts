"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { IncidentSeverity } from "@/lib/api";
import {
  DEFAULT_RANGE,
  parseRange,
  parseSeverity,
  rangeDays,
  type CommandCenterRange,
} from "@/lib/commandCenter/format";

export interface CommandCenterFilters {
  range: CommandCenterRange;
  days: number;
  siteId: string | null;
  severity: IncidentSeverity | null;
}

/**
 * useCommandCenterFilters — the Executive Command Center's global filters,
 * held in the URL (`?range=7d&site=<uuid>&severity=critical`) so a filtered
 * view survives reload and can be shared. Invalid values fall back to the
 * defaults instead of reaching the API.
 */
export function useCommandCenterFilters() {
  // Both can be null outside the App Router tree (e.g. while prerendering);
  // treat that as "no filters" on the current page.
  const params = useSearchParams() ?? new URLSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/dashboard";

  const filters = useMemo<CommandCenterFilters>(() => {
    const range = parseRange(params.get("range"));
    return {
      range,
      days: rangeDays(range),
      siteId: params.get("site") || null,
      severity: parseSeverity(params.get("severity")),
    };
  }, [params]);

  const update = useCallback(
    (patch: Partial<{ range: CommandCenterRange; siteId: string | null; severity: IncidentSeverity | null }>) => {
      const next = new URLSearchParams(params.toString());
      const set = (key: string, value: string | null | undefined, fallback?: string) => {
        if (!value || value === fallback) next.delete(key);
        else next.set(key, value);
      };
      if ("range" in patch) set("range", patch.range, DEFAULT_RANGE);
      if ("siteId" in patch) set("site", patch.siteId);
      if ("severity" in patch) set("severity", patch.severity);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return { filters, update };
}
