"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import {
  getOperationalIntelligence,
} from "@/lib/api";
import type {
  OperationalIntelligence,
} from "@/lib/api";

/**
 * useOperationalIntelligence — PHASE 6A.
 *
 * ONE tenant-scoped read (GET /api/dashboard/operational-intelligence) for
 * the whole KPI payload. Joins the existing 60s panel cadence: no extra
 * timer, no per-card polling, no fetching inside presentational components.
 *
 * Transport goes through the shared `apiFetch` client (via
 * `getOperationalIntelligence` in `@/lib/api`: Bearer token,
 * tenant-override header, 401 refresh, timeout) — a raw fetch here would
 * silently drop auth and the panel would never load.
 *
 * Tenant safety: the server resolves the tenant from the authenticated
 * principal; the client's `tenantId` argument is the same project-wide
 * convenience every other query uses, overridden server-side so a caller
 * cannot select a tenant.
 *
 * Zero-data semantics: a RATE with no denominator arrives as `null` (never
 * 0% and never 100%). The panel renders those as N/A against the card's own
 * definition — see the model for the truth rules.
 */
export function useOperationalIntelligence() {
  const { isAuthenticated } = useAuth();

  return useQuery<OperationalIntelligence>({
    queryKey: ["dashboard", "operational-intelligence"],
    queryFn: () => getOperationalIntelligence(),
    enabled: isAuthenticated,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
