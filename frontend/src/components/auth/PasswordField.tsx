"use client";

/**
 * Password input for the auth pages: show / hide toggle, a Caps Lock warning,
 * and (for new passwords) a strength meter.
 *
 * The meter is guidance only: the server's rule is the minimum length (8),
 * and the score just rewards length and character variety. It never claims a
 * password is safe.
 */

import { useState, type KeyboardEvent } from "react";
import { Eye, EyeOff, Lock } from "lucide-react";

export const MIN_PASSWORD_LENGTH = 8;

const LEVELS = ["Too short", "Weak", "Fair", "Good", "Strong"] as const;

/** 0 = below the minimum length; 1–4 by length and character variety. */
export function passwordScore(pw: string): number {
  if (pw.length < MIN_PASSWORD_LENGTH) return 0;
  const variety = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  let score = 1;
  if (pw.length >= 12) score++;
  if (variety >= 3) score++;
  if (pw.length >= 16 || (pw.length >= 12 && variety === 4)) score++;
  return Math.min(score, 4);
}

export default function PasswordField({
  id,
  name,
  value,
  onChange,
  placeholder,
  autoComplete,
  disabled,
  invalid,
  meter = false,
  describedBy,
}: {
  id: string;
  name?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  autoComplete: "current-password" | "new-password";
  disabled?: boolean;
  invalid?: boolean;
  /** Show the strength meter (new passwords only). */
  meter?: boolean;
  describedBy?: string;
}) {
  const [visible, setVisible] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const checkCaps = (e: KeyboardEvent<HTMLInputElement>) => setCapsLock(e.getModifierState?.("CapsLock") ?? false);
  const score = passwordScore(value);
  const ids = [describedBy, meter && value ? `${id}-strength` : null, capsLock ? `${id}-caps` : null].filter(Boolean).join(" ");

  return (
    <div>
      <div className="lx-field">
        <label htmlFor={id} className="lx-sr-only">
          {placeholder}
        </label>
        <Lock className="lx-field__icon" size={18} aria-hidden="true" />
        <input
          id={id}
          name={name}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={checkCaps}
          onKeyUp={checkCaps}
          onBlur={() => setCapsLock(false)}
          disabled={disabled}
          required
          minLength={autoComplete === "new-password" ? MIN_PASSWORD_LENGTH : undefined}
          aria-invalid={invalid || undefined}
          aria-describedby={ids || undefined}
          className="lx-input"
          placeholder={placeholder}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="lx-field__toggle"
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
        >
          {visible ? <EyeOff size={18} /> : <Eye size={18} />}
        </button>
      </div>
      {capsLock && (
        <p id={`${id}-caps`} className="lx-caps" role="status">
          Caps Lock is on
        </p>
      )}
      {meter && value && (
        <div id={`${id}-strength`} className={`lx-strength lx-strength--${score}`} aria-live="polite">
          <span className="lx-strength__bars" aria-hidden="true">
            {[1, 2, 3, 4].map((n) => (
              <i key={n} className={n <= score ? "on" : undefined} />
            ))}
          </span>
          <span className="lx-strength__label">
            {score === 0 ? `At least ${MIN_PASSWORD_LENGTH} characters` : `${LEVELS[score]} password`}
          </span>
        </div>
      )}
    </div>
  );
}
