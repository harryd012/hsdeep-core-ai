"use client";

import { motion } from "framer-motion";

const metrics = [
  ["Active Tasks", "24"],
  ["Critical Alerts", "7"],
  ["Resolved Today", "42"],
  ["System Health", "98.7%"],
];

export default function MetricsCards() {
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-4">
      {metrics.map(([label, value]) => (
        <motion.div
          key={label}
          whileHover={{ scale: 1.04 }}
          className="glass p-5"
        >
          <p className="text-cyan-500 text-sm">{label}</p>
          <h2 className="text-3xl font-bold mt-3">{value}</h2>
        </motion.div>
      ))}
    </div>
  );
}