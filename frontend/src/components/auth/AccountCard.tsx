"use client";

/** Shared frame for the public account pages (same look as /signup). */
import type { FormEvent, ReactNode } from "react";

export const INPUT_CLASS =
  "mb-4 w-full rounded border border-white/15 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/40";
export const LABEL_CLASS = "mb-1 block text-xs uppercase tracking-wide text-white/60";
export const BUTTON_CLASS =
  "w-full rounded bg-white/90 px-3 py-2 text-sm font-medium text-black transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50";

export function AccountCard({
  subtitle,
  error,
  notice,
  onSubmit,
  children,
}: {
  subtitle: string;
  error?: string | null;
  notice?: string | null;
  onSubmit?: (e: FormEvent<HTMLFormElement>) => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-black px-4 py-12">
      <form
        onSubmit={onSubmit ?? ((e) => e.preventDefault())}
        className="w-full max-w-sm rounded-lg border border-white/10 bg-black/60 p-8 shadow-xl"
        noValidate
      >
        <div className="mb-6 text-center">
          <h1 className="text-lg font-semibold tracking-wide text-white">HSDEEP CORE AI</h1>
          <p className="mt-1 text-xs uppercase tracking-wide text-white/50">{subtitle}</p>
        </div>
        {error && (
          <div role="alert" className="mb-4 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="mb-4 rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
            {notice}
          </div>
        )}
        {children}
        <p className="mt-4 text-center text-xs text-white/50">
          <a href="/login" className="text-white/80 underline hover:text-white">
            Back to sign in
          </a>
        </p>
      </form>
    </div>
  );
}
