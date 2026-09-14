"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/lib/auth-context";

/**
 * Application-wide React Query provider (P0-5).
 *
 * One shared QueryClient instance backs the whole app, so the shared AI data
 * layer (`/ai-copilot` + the dashboard) reads from a single cache. Defaults
 * mirror the pre-existing hand-rolled poll behavior in this repo:
 *   - `refetchOnWindowFocus: false`  (the old useAutoRefresh never refetched on focus)
 *   - `retry: false`                 (the old poll fetched once per interval, no auto-retry)
 */
export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}