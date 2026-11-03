"use client";

/**
 * Shared frame for the public account pages (forgot / reset password, accept
 * invite, verify email): the glass card on the sign-in scene (AuthScene).
 *
 * The form is method="post" and its controls stay disabled until the page has
 * hydrated, so nothing typed can be submitted natively into the URL.
 */
import type { FormEvent, ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import AuthScene, { useHydrated } from "@/components/auth/AuthScene";

export const INPUT_CLASS = "lx-input lx-input--plain";
export const LABEL_CLASS = "lx-label";
export const BUTTON_CLASS = "lx-submit lx-submit--block";

export function AccountCard({
  subtitle,
  lead,
  error,
  notice,
  onSubmit,
  children,
}: {
  subtitle: string;
  /** One line under the title explaining the step. */
  lead?: string;
  error?: string | null;
  notice?: string | null;
  onSubmit?: (e: FormEvent<HTMLFormElement>) => void;
  children?: ReactNode;
}) {
  const hydrated = useHydrated();
  return (
    <AuthScene title={`HSDEEP CORE AI — ${subtitle}`}>
      <section className="lx-card" aria-labelledby="lx-card-title">
        <p className="lx-card__eyebrow">HSDEEP CORE AI</p>
        <h2 id="lx-card-title" className="lx-card__title lx-card__title--sm">
          {subtitle}
        </h2>
        {lead && <p className="lx-card__lead">{lead}</p>}
        {error && (
          <div role="alert" className="lx-card__error">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="lx-card__notice">
            {notice}
          </div>
        )}
        <form method="post" onSubmit={onSubmit ?? ((e) => e.preventDefault())} className="lx-form" noValidate>
          <fieldset className="lx-fieldset" disabled={!hydrated}>
            {children}
          </fieldset>
        </form>
        <p className="lx-card__foot">
          <a href="/login" className="lx-back">
            <ArrowLeft size={15} aria-hidden="true" /> Back to sign in
          </a>
        </p>
      </section>
    </AuthScene>
  );
}
