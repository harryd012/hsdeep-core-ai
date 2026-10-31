"use client";

import { useSystemHealth } from "@/hooks/useSystemHealth";
import { systemStatusText, systemTone } from "@/lib/health";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-green-500/20 text-green-400",
  degraded: "bg-amber-500/20 text-amber-400",
  down: "bg-red-500/20 text-red-400",
  pending: "bg-gray-500/20 text-gray-400",
};

export default function TopBar() {
  const { data } = useSystemHealth();
  const tone = systemTone(data?.status);

  return (
    <header className="glass p-5 flex justify-between items-center">
      <div>
        <h1 className="text-2xl font-bold">MISSION CONTROL</h1>
        <p className="text-sm text-cyan-500">Real-time AI orchestration</p>
      </div>

      <div className="flex items-center gap-4">
        <span className={`px-3 py-1 rounded-full ${TONE_CLASS[tone]}`}>
          {systemStatusText(data?.status)}
        </span>
        <span>HARRY / ADMIN</span>
      </div>
    </header>
  );
}