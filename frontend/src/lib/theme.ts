/**
 * Appearance (theme, accent colour, background, density) — pure model + the
 * pre-paint boot script. Plain module on purpose: the server root layout
 * imports THEME_BOOT_SCRIPT, and a constant exported from a "use client" file
 * would reach it as a client reference instead of the string.
 *
 * Applied as attributes / custom properties on <html>:
 *   data-theme    light | dark | midnight | graphite | contrast  (the base:
 *                 which shell stylesheet applies)
 *   data-palette  paper | mist | sage | lavender | ocean | forest | plum |
 *                 carbon | ember  (a colour preset over the light or dark base;
 *                 absent for the base themes themselves)
 *   data-canvas   tint | glow | grid  (absent for the theme's own background)
 *   data-density  comfortable | compact
 *   --ecc-accent / --cyan   the accent colour (only when the user picked one)
 *
 * Stored per browser under APPEARANCE_STORAGE_KEY. There is no per-user
 * preference API yet, so the choice does not follow the user to another device.
 */

export const APPEARANCE_STORAGE_KEY = "hsdeep-appearance";
/** Written by the first theme switch; still read so existing choices survive. */
export const LEGACY_THEME_KEY = "hsdeep-theme";
/** "collapsed" | "expanded" — the sidebar rail; restored by the boot script too. */
export const NAV_STORAGE_KEY = "hsdeep-nav";

export type BaseTheme = "light" | "dark" | "midnight" | "graphite" | "contrast";
export type Palette = "paper" | "mist" | "sage" | "lavender" | "ocean" | "forest" | "plum" | "carbon" | "ember";
export type ThemeId = "system" | BaseTheme | Palette;
export type Canvas = "theme" | "tint" | "glow" | "grid";
export type Density = "comfortable" | "compact";

export interface Appearance {
  theme: ThemeId;
  /** Hex colour, or null for the theme's own accent. */
  accent: string | null;
  /** The page background behind the cards. */
  canvas: Canvas;
  density: Density;
}

export const DEFAULT_APPEARANCE: Appearance = { theme: "light", accent: null, canvas: "theme", density: "comfortable" };

export interface ThemeOption {
  id: ThemeId;
  label: string;
  hint: string;
  group: "light" | "dark" | "auto";
  /** Canvas, card, accent — drawn as the card's preview. */
  swatch: [string, string, string];
}

export const THEMES: ThemeOption[] = [
  { id: "light", group: "light", label: "Light", hint: "White cards on a soft grey canvas", swatch: ["#f3f5f9", "#ffffff", "#2563eb"] },
  { id: "paper", group: "light", label: "Paper", hint: "Warm ivory, easy on the eyes", swatch: ["#f4f0e8", "#fffdf8", "#b45309"] },
  { id: "mist", group: "light", label: "Mist", hint: "Cool blue-grey", swatch: ["#e9eef5", "#fbfcfe", "#0369a1"] },
  { id: "sage", group: "light", label: "Sage", hint: "Soft green tint", swatch: ["#ebf2ed", "#fcfdfc", "#15803d"] },
  { id: "lavender", group: "light", label: "Lavender", hint: "Soft violet tint", swatch: ["#f0edf8", "#fdfcff", "#6d28d9"] },
  { id: "contrast", group: "light", label: "High contrast", hint: "Maximum legibility", swatch: ["#ffffff", "#ffffff", "#0b3d91"] },
  { id: "dark", group: "dark", label: "Dark", hint: "The original HSDEEP control-room look", swatch: ["#020a12", "#071622", "#3b82f6"] },
  { id: "midnight", group: "dark", label: "Midnight", hint: "Deep navy for NOC walls", swatch: ["#0b1220", "#111a2e", "#60a5fa"] },
  { id: "graphite", group: "dark", label: "Graphite", hint: "Neutral dark grey, low glare", swatch: ["#121417", "#1b1e22", "#38bdf8"] },
  { id: "ocean", group: "dark", label: "Ocean", hint: "Deep teal", swatch: ["#04161a", "#082429", "#2dd4bf"] },
  { id: "forest", group: "dark", label: "Forest", hint: "Dark green, calm", swatch: ["#0b140f", "#111f17", "#4ade80"] },
  { id: "plum", group: "dark", label: "Plum", hint: "Dark violet", swatch: ["#140d1c", "#1d1428", "#c084fc"] },
  { id: "ember", group: "dark", label: "Ember", hint: "Warm dark brown", swatch: ["#160f0c", "#201712", "#fb923c"] },
  { id: "carbon", group: "dark", label: "Carbon", hint: "True black for OLED and wall screens", swatch: ["#000000", "#0d0d0d", "#22d3ee"] },
  { id: "system", group: "auto", label: "Match system", hint: "Light or dark, following the OS", swatch: ["#f3f5f9", "#071622", "#2563eb"] },
];

/** Which base stylesheet each palette sits on. */
const PALETTE_BASE: Record<Palette, "light" | "dark"> = {
  paper: "light", mist: "light", sage: "light", lavender: "light",
  ocean: "dark", forest: "dark", plum: "dark", carbon: "dark", ember: "dark",
};

export const ACCENTS: { label: string; value: string | null }[] = [
  { label: "Theme default", value: null },
  { label: "Blue", value: "#2563eb" },
  { label: "Indigo", value: "#4f46e5" },
  { label: "Sky", value: "#0ea5e9" },
  { label: "Cyan", value: "#0891b2" },
  { label: "Teal", value: "#0d9488" },
  { label: "Emerald", value: "#059669" },
  { label: "Green", value: "#16a34a" },
  { label: "Lime", value: "#65a30d" },
  { label: "Amber", value: "#d97706" },
  { label: "Orange", value: "#ea580c" },
  { label: "Red", value: "#dc2626" },
  { label: "Rose", value: "#e11d48" },
  { label: "Pink", value: "#db2777" },
  { label: "Magenta", value: "#c026d3" },
  { label: "Violet", value: "#7c3aed" },
  { label: "Slate", value: "#475569" },
];

export const CANVASES: { id: Canvas; label: string; hint: string }[] = [
  { id: "theme", label: "Theme", hint: "The theme's own background" },
  { id: "tint", label: "Tinted", hint: "A light wash of the accent colour" },
  { id: "glow", label: "Glow", hint: "Soft accent light from the corners" },
  { id: "grid", label: "Dot grid", hint: "A faint dot pattern" },
];

const THEME_IDS: readonly ThemeId[] = THEMES.map((t) => t.id);
const CANVAS_IDS: readonly Canvas[] = CANVASES.map((c) => c.id);
const HEX = /^#[0-9a-f]{6}$/i;

/** Anything read from storage goes through here: unknown values fall back to defaults. */
export function normalizeAppearance(raw: unknown): Appearance {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const theme = THEME_IDS.includes(value.theme as ThemeId) ? (value.theme as ThemeId) : DEFAULT_APPEARANCE.theme;
  const accent = typeof value.accent === "string" && HEX.test(value.accent) ? value.accent.toLowerCase() : null;
  const canvas = CANVAS_IDS.includes(value.canvas as Canvas) ? (value.canvas as Canvas) : DEFAULT_APPEARANCE.canvas;
  const density: Density = value.density === "compact" ? "compact" : "comfortable";
  return { theme, accent, canvas, density };
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

/** The <html> attributes for a theme: its base stylesheet and, for a palette, the palette. */
export function resolveTheme(theme: ThemeId, prefersDark: boolean): { base: BaseTheme; palette: Palette | null } {
  if (theme === "system") return { base: prefersDark ? "dark" : "light", palette: null };
  if (theme in PALETTE_BASE) return { base: PALETTE_BASE[theme as Palette], palette: theme as Palette };
  return { base: theme as BaseTheme, palette: null };
}

/**
 * Inline, pre-hydration: applies the saved appearance so the first paint is
 * already correct. Mirrors normalizeAppearance/resolveTheme; kept tiny and
 * dependency-free because it runs before any bundle loads.
 */
export const THEME_BOOT_SCRIPT = `(function(){var d=document.documentElement,a={theme:"light",accent:null,canvas:"theme",density:"comfortable"},P=${JSON.stringify(PALETTE_BASE)};try{var s=localStorage.getItem("${APPEARANCE_STORAGE_KEY}");if(s){var p=JSON.parse(s)||{};if(${JSON.stringify(THEME_IDS)}.indexOf(p.theme)>-1)a.theme=p.theme;if(typeof p.accent==="string"&&/^#[0-9a-f]{6}$/i.test(p.accent))a.accent=p.accent;if(${JSON.stringify(CANVAS_IDS)}.indexOf(p.canvas)>-1)a.canvas=p.canvas;if(p.density==="compact")a.density="compact";}else{var l=localStorage.getItem("${LEGACY_THEME_KEY}");if(l==="dark")a.theme="dark";}}catch(e){}var t=a.theme==="system"?(window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):a.theme;if(P[t]){d.setAttribute("data-palette",t);t=P[t];}d.setAttribute("data-theme",t);if(a.canvas!=="theme")d.setAttribute("data-canvas",a.canvas);d.setAttribute("data-density",a.density);if(a.accent){d.style.setProperty("--ecc-accent",a.accent);d.style.setProperty("--cyan",a.accent);}try{if(localStorage.getItem("${NAV_STORAGE_KEY}")==="collapsed")d.setAttribute("data-nav","collapsed");}catch(e){}})();`;
