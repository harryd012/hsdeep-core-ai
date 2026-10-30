"use client";

import Sidebar from "@/components/Sidebar";
import CopilotBar from "@/components/CopilotBar";
import AuthGuard from "@/components/auth/AuthGuard";
import "@/app/styles/dashboard.css";
import "@/app/styles/dashboard-command-center.css";
import "@/app/styles/soc.css";
import "@/app/styles/dashboard-space-fix.css";

/**
 * (dashboard) route-group shell — the ONLY place the app shell mounts.
 * One AuthGuard, one Sidebar, one fluid main column, one CopilotBar.
 * Root layout (src/app/layout.tsx) only provides <html>/<body>, global
 * styles and the shared Providers.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div className="hs-app">
        <Sidebar />
        <main className="hs-main">{children}</main>
        <CopilotBar />
      </div>
    </AuthGuard>
  );
}
