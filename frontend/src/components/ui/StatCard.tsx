"use client";

import Link from "next/link";
import { ReactNode } from "react";

interface StatCardProps {
  icon: ReactNode;
  count: number | string;
  label: string;
  subtext?: string;
  href: string;
  accentColor: string;
}

function StatCard({
  icon,
  count,
  label,
  subtext,
  href,
  accentColor,
}: StatCardProps) {
  return (
    <Link
      href={href}
      className="block rounded-lg border border-white/10 bg-black/40 p-3 transition-colors hover:border-white/30 hover:bg-black/60 cursor-pointer"
    >
      <div className="flex items-center gap-2">
        <span className={`rounded-full border p-1.5 ${accentColor}`}>{icon}</span>
        <span className="text-3xl font-serif">{count}</span>
      </div>
      <p className="mt-1 text-xs uppercase tracking-wide text-white/60">{label}</p>
      {subtext && <p className="text-[10px] text-white/40">{subtext}</p>}
    </Link>
  );
}

export { StatCard };