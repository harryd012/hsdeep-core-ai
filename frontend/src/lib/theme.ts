/**
 * Theme constants shared by the server root layout (boot script) and the
 * client ThemeToggle. Plain module on purpose: a constant exported from a
 * "use client" file reaches a server component as a client reference, not as
 * the string itself.
 */

export const THEME_STORAGE_KEY = "hsdeep-theme";

/** Inline, pre-hydration: apply the saved theme (light by default) so the page never flashes. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");document.documentElement.setAttribute("data-theme",t==="dark"?"dark":"light");}catch(e){document.documentElement.setAttribute("data-theme","light");}})();`;
