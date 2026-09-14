"use client";

const managers = [
  "NOC Manager",
  "SOC Manager",
  "Cloud Manager",
  "DevOps Manager",
  "Automation Manager",
  "Reporting Manager",
];

export default function ManagersGrid() {
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
      {managers.map((manager) => (
        <div key={manager} className="glass p-5">
          <h3 className="font-bold">{manager}</h3>
          <p className="text-sm text-cyan-500 mt-2">ACTIVE</p>
        </div>
      ))}
    </div>
  );
}