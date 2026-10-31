"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { Check, Palette, RotateCcw, X } from "lucide-react";
import "@/app/styles/theme-light.css";
import "@/app/styles/themes.css";
import "@/app/styles/page-kit.css";
import {
  ACCENTS,
  APPEARANCE_STORAGE_KEY,
  DEFAULT_APPEARANCE,
  LEGACY_THEME_KEY,
  THEMES,
  parseStoredAppearance,
  resolveTheme,
  type Appearance,
  type ThemeId,
} from "@/lib/theme";

/**
 * Appearance picker — theme preset, accent colour and density, applied live
 * to <html> and remembered per browser. Opens from the sidebar and from the
 * Executive Command Center header.
 */

// ---------------------------------------------------------------------------
// store
// ---------------------------------------------------------------------------

const EVENT = "hsdeep-appearance";
let cached: Appearance | null = null;

function load(): Appearance {
  if (cached) return cached;
  try {
    cached = parseStoredAppearance(localStorage.getItem(APPEARANCE_STORAGE_KEY), localStorage.getItem(LEGACY_THEME_KEY));
  } catch {
    cached = DEFAULT_APPEARANCE;
  }
  return cached;
}

function prefersDark(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

function apply(a: Appearance) {
  const root = document.documentElement;
  root.setAttribute("data-theme", resolveTheme(a.theme, prefersDark()));
  root.setAttribute("data-density", a.density);
  if (a.accent) {
    root.style.setProperty("--ecc-accent", a.accent);
    root.style.setProperty("--cyan", a.accent);
  } else {
    root.style.removeProperty("--ecc-accent");
    root.style.removeProperty("--cyan");
  }
}

export function setAppearance(patch: Partial<Appearance>) {
  const next = { ...load(), ...patch };
  cached = next;
  apply(next);
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(next));
    localStorage.removeItem(LEGACY_THEME_KEY);
  } catch {
    // Storage unavailable (private mode): the change still applies for this visit.
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  // "Match system" follows OS changes while the page is open.
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  const onMedia = () => {
    if (load().theme === "system") apply(load());
  };
  media?.addEventListener?.("change", onMedia);
  return () => {
    window.removeEventListener(EVENT, onChange);
    media?.removeEventListener?.("change", onMedia);
  };
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribe, load, () => DEFAULT_APPEARANCE);
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

function ThemeCard({ id, label, hint, swatch, selected }: (typeof THEMES)[number] & { selected: boolean }) {
  const [bg, card, accent] = swatch;
  return (
    <button
      type="button"
      className={`apx-theme${selected ? " apx-theme--on" : ""}`}
      aria-pressed={selected}
      onClick={() => setAppearance({ theme: id as ThemeId })}
    >
      <span className="apx-theme__preview" style={{ background: id === "system" ? `linear-gradient(135deg, ${bg} 50%, ${card} 50%)` : bg }} aria-hidden="true">
        {id !== "system" && (
          <>
            <span style={{ background: card }} />
            <span style={{ background: card }} />
            <i style={{ background: accent }} />
          </>
        )}
      </span>
      <span className="apx-theme__label">
        {label}
        {selected && <Check size={13} aria-hidden="true" />}
      </span>
      <span className="apx-theme__hint">{hint}</span>
    </button>
  );
}

export default function AppearanceMenu({ variant = "sidebar" }: { variant?: "sidebar" | "header" }) {
  const [open, setOpen] = useState(false);
  const appearance = useAppearance();
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (!panelRef.current?.contains(e.target as Node) && !buttonRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    panelRef.current?.querySelector<HTMLElement>("button")?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const custom = appearance.accent && !ACCENTS.some((a) => a.value === appearance.accent);
  const current = THEMES.find((t) => t.id === appearance.theme)?.label ?? "Light";

  return (
    <div className={`apx apx--${variant}`}>
      <button
        ref={buttonRef}
        type="button"
        className={variant === "header" ? "ecc-button" : "theme-toggle"}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Appearance"
      >
        <Palette size={15} aria-hidden="true" />
        <span>{variant === "header" ? "Appearance" : `Theme: ${current}`}</span>
      </button>

      {open && (
        <div ref={panelRef} className="apx-panel" role="dialog" aria-labelledby={titleId}>
          <header className="apx-panel__head">
            <h2 id={titleId}>Appearance</h2>
            <button type="button" className="apx-icon" aria-label="Close" onClick={() => setOpen(false)}>
              <X size={15} />
            </button>
          </header>

          <section aria-label="Theme">
            <h3>Theme</h3>
            <div className="apx-themes">
              {THEMES.map((t) => (
                <ThemeCard key={t.id} {...t} selected={appearance.theme === t.id} />
              ))}
            </div>
          </section>

          <section aria-label="Accent colour">
            <h3>Accent colour</h3>
            <div className="apx-accents">
              {ACCENTS.map((a) => {
                const on = appearance.accent === a.value;
                return (
                  <button
                    key={a.label}
                    type="button"
                    className={`apx-swatch${on ? " apx-swatch--on" : ""}${a.value ? "" : " apx-swatch--default"}`}
                    style={a.value ? { background: a.value } : undefined}
                    aria-pressed={on}
                    aria-label={a.label}
                    title={a.label}
                    onClick={() => setAppearance({ accent: a.value })}
                  >
                    {on && <Check size={13} aria-hidden="true" />}
                  </button>
                );
              })}
              <label className={`apx-swatch apx-swatch--custom${custom ? " apx-swatch--on" : ""}`} title="Custom colour" style={custom ? { background: appearance.accent! } : undefined}>
                <input
                  type="color"
                  aria-label="Custom accent colour"
                  value={appearance.accent ?? "#2563eb"}
                  onChange={(e) => setAppearance({ accent: e.target.value })}
                />
                {!custom && <span aria-hidden="true">+</span>}
              </label>
            </div>
          </section>

          <section aria-label="Density">
            <h3>Density</h3>
            <div className="pk-tabs" role="radiogroup" aria-label="Density">
              {(["comfortable", "compact"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  className="pk-tab"
                  aria-checked={appearance.density === d}
                  onClick={() => setAppearance({ density: d })}
                >
                  {d === "comfortable" ? "Comfortable" : "Compact"}
                </button>
              ))}
            </div>
          </section>

          <footer className="apx-panel__foot">
            <span>Saved in this browser.</span>
            <button type="button" className="ecc-button ecc-button--small" onClick={() => setAppearance(DEFAULT_APPEARANCE)}>
              <RotateCcw size={13} aria-hidden="true" /> Reset
            </button>
          </footer>
        </div>
      )}
    </div>
  );
}
