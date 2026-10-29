/**
 * Appearance (theme, accent colour, density) — pure model + the pre-paint
 * boot script. Plain module on purpose: the server root layout imports
 * THEME_BOOT_SCRIPT, and a constant exported from a "use client" file would
 * reach it as a client reference instead of the string.
 *
 * Applied as attributes / custom properties on <html>:
 *   data-theme    light | dark | midnight | graphite | contrast
 *   data-density  comfortable | compact
 *   --ecc-accent / --cyan   the accent colour (only when the user picked one)
 *
 * Stored per browser under APPEARANCE_STORAGE_KEY. There is no per-user
 * preference API yet, so the choice does not follow the user to another device.
 */

export const APPEARANCE_STORAGE_KEY = "hsdeep-appearance";
/** Written by the first theme switch; still read so existing choices survive. */
export const LEGACY_THEME_KEY = "hsdeep-theme";

export type ThemeId = "system" | "light" | "dark" | "midnight" | "graphite" | "contrast";
export type ResolvedTheme = Exclude<ThemeId, "system">;
export type Density = "comfortable" | "compact";

export interface Appearance {
  theme: ThemeId;
  /** Hex colour, or null for the theme's own accent. */
  accent: string | null;
  density: Density;
}

export const DEFAULT_APPEARANCE: Appearance = { theme: "light", accent: null, density: "comfortable" };

export const THEMES: { id: ThemeId; label: string; hint: string; swatch: [string, string, string] }[] = [
  { id: "light", label: "Light", hint: "White cards on a soft grey canvas", swatch: ["#f3f5f9", "#ffffff", "#2563eb"] },
  { id: "dark", label: "Dark", hint: "The original HSDEEP control-room look", swatch: ["#020a12", "#071622", "#3b82f6"] },
  { id: "midnight", label: "Midnight", hint: "Deep navy for NOC walls", swatch: ["#0b1220", "#111a2e", "#60a5fa"] },
  { id: "graphite", label: "Graphite", hint: "Neutral dark grey, low glare", swatch: ["#121417", "#1b1e22", "#38bdf8"] },
  { id: "contrast", label: "High contrast", hint: "Maximum legibility", swatch: ["#ffffff", "#ffffff", "#0b3d91"] },
  { id: "system", label: "Match system", hint: "Light or dark, following the OS", swatch: ["#f3f5f9", "#071622", "#2563eb"] },
];

export const ACCENTS: { label: string; value: string | null }[] = [
  { label: "Theme default", value: null },
  { label: "Blue", value: "#2563eb" },
  { label: "Sky", value: "#0ea5e9" },
  { label: "Teal", value: "#0d9488" },
  { label: "Emerald", value: "#059669" },
  { label: "Violet", value: "#7c3aed" },
  { label: "Magenta", value: "#c026d3" },
  { label: "Orange", value: "#ea580c" },
];

const THEME_IDS: readonly ThemeId[] = ["system", "light", "dark", "midnight", "graphite", "contrast"];
const HEX = /^#[0-9a-f]{6}$/i;

/** Anything read from storage goes through here: unknown values fall back to defaults. */
export function normalizeAppearance(raw: unknown): Appearance {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const theme = THEME_IDS.includes(value.theme as ThemeId) ? (value.theme as ThemeId) : DEFAULT_APPEARANCE.theme;
  const accent = typeof value.accent === "string" && HEX.test(value.accent) ? value.accent.toLowerCase() : null;
  const density: Density = value.density === "compact" ? "compact" : "comfortable";
  return { theme, accent, density };
}

/** Stored JSON (or the legacy "light"/"dark" string) -> Appearance. */
export function parseStoredAppearance(stored: string | null, legacy: string | null = null): Appearance {
  if (stored) {
    try {
      return normalizeAppearance(JSON.parse(stored));
    } catch {
      return DEFAULT_APPEARANCE;
    }
  }
  if (legacy === "dark" || legacy === "light") return { ...DEFAULT_APPEARANCE, theme: legacy };
  return DEFAULT_APPEARANCE;
}

export function resolveTheme(theme: ThemeId, prefersDark: boolean): ResolvedTheme {
  return theme === "system" ? (prefersDark ? "dark" : "light") : theme;
}

/**
 * Inline, pre-hydration: applies the saved appearance so the first paint is
 * already correct. Mirrors normalizeAppearance/resolveTheme; kept tiny and
 * dependency-free because it runs before any bundle loads.
 */
export const THEME_BOOT_SCRIPT = `(function(){var d=document.documentElement,a={theme:"light",accent:null,density:"comfortable"};try{var s=localStorage.getItem("${APPEARANCE_STORAGE_KEY}");if(s){var p=JSON.parse(s)||{};if(["system","light","dark","midnight","graphite","contrast"].indexOf(p.theme)>-1)a.theme=p.theme;if(typeof p.accent==="string"&&/^#[0-9a-f]{6}$/i.test(p.accent))a.accent=p.accent;if(p.density==="compact")a.density="compact";}else{var l=localStorage.getItem("${LEGACY_THEME_KEY}");if(l==="dark")a.theme="dark";}}catch(e){}var t=a.theme==="system"?(window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):a.theme;d.setAttribute("data-theme",t);d.setAttribute("data-density",a.density);if(a.accent){d.style.setProperty("--ecc-accent",a.accent);d.style.setProperty("--cyan",a.accent);}})();`;
