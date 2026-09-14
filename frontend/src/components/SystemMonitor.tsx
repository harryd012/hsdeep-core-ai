"use client";

export default function SystemMonitor() {
  const stats = [
    ["CPU", "37%"],
    ["RAM", "21 / 32 GB"],
    ["Disk", "580 GB Free"],
    ["Temp", "61°C"],
    ["Network", "42 Mbps"],
  ];

  return (
    <div className="glass p-6">
      <h2 className="font-bold mb-4">LOCAL SYSTEM</h2>

      <div className="space-y-3">
        {stats.map(([label, value]) => (
          <div key={label} className="flex justify-between">
            <span className="text-cyan-400">{label}</span>
            <span>{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}