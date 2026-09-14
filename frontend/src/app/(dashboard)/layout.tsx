"use client";

import Sidebar from "@/components/Sidebar";
import CopilotBar from "@/components/CopilotBar";
import AuthGuard from "@/components/auth/AuthGuard";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div className="hs-app">
        {children}
        <Sidebar />
        <CopilotBar />
      </div>
    </AuthGuard>
  );
}
