"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchPaRunbooks } from "@/lib/paloalto/client";

/** React-Query hook for the runbook catalogue (empty/error states handled by callers). */
export function usePaRunbooks(q?: string) {
  return useQuery({
    queryKey: ["paloalto", "runbooks", q ?? ""],
    queryFn: () => fetchPaRunbooks(q),
    staleTime: 60_000,
    retry: 1,
  });
}
