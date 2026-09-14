"use client";

export default function TopBar() {
  return (
    <header className="glass p-5 flex justify-between items-center">
      <div>
        <h1 className="text-2xl font-bold">MISSION CONTROL</h1>
        <p className="text-sm text-cyan-500">Real-time AI orchestration</p>
      </div>

      <div className="flex items-center gap-4">
        <span className="px-3 py-1 rounded-full bg-green-500/20 text-green-400">
          SYSTEM ONLINE
        </span>
        <span>HARRY / ADMIN</span>
      </div>
    </header>
  );
}