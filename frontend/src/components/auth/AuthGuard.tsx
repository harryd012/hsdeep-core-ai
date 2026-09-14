"use client";

/**
 * AuthGuard — protects the (dashboard) route group.
 *
 * Renders children only once a real session is confirmed via AuthProvider.
 * While the initial check is in flight it shows a minimal loading state
 * (never the dashboard shell with fabricated/empty data flashing first).
 * If no session exists once loading finishes, it redirects to /login with
 * the current path preserved as ?next=... so the user lands back where
 * they were headed after signing in.
 */
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, sessionError } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      const next = pathname && pathname !== "/login" ? pathname : "/dashboard";
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [isLoading, isAuthenticated, router, pathname]);

  if (isLoading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-white/60">
        <p className="text-sm uppercase tracking-wide">Checking session…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    // A redirect is already in flight (see effect above). Rendering nothing
    // here avoids a flash of protected content before navigation completes.
    return sessionError ? (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-red-400">
        <p className="text-sm">{sessionError}</p>
      </div>
    ) : null;
  }

  return <>{children}</>;
}
