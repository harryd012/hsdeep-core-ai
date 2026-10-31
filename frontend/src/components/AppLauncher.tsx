"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, X } from "lucide-react";
import { NAV_SECTIONS, launcherEntries, searchLauncher, type NavGroup } from "@/lib/navigation";

/**
 * Apps launcher — every page in the product, grouped by section, searchable,
 * keyboard-driven (↑/↓/Enter/Esc). Opens from the sidebar search button or
 * Ctrl+K / ⌘K anywhere in the app.
 */
export default function AppLauncher({
  open,
  onClose,
  groups,
}: {
  open: boolean;
  onClose: () => void;
  groups: NavGroup[];
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const all = useMemo(() => launcherEntries(groups), [groups]);
  const results = useMemo(() => searchLauncher(all, query), [all, query]);
  // Sections in nav order; each item carries its position in keyboard order.
  const { bySection, flat } = useMemo(() => {
    const sections = NAV_SECTIONS.map((s) => ({ section: s, items: results.filter((r) => r.section === s) })).filter((s) => s.items.length);
    const ordered = sections.flatMap((s) => s.items);
    const position = new Map(ordered.map((item, i) => [item.key, i]));
    return {
      bySection: sections.map((s) => ({ ...s, items: s.items.map((item) => ({ ...item, index: position.get(item.key)! })) })),
      flat: ordered,
    };
  }, [results]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (!open) return null;

  function go(route: string) {
    onClose();
    setQuery("");
    setCursor(0);
    router.push(route);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    else if (e.key === "Enter" && flat[cursor]) { e.preventDefault(); go(flat[cursor].route); }
  }

  return (
    <div className="lnc-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="lnc" role="dialog" aria-modal="true" aria-label="Apps" onKeyDown={onKeyDown}>
        <div className="lnc__search">
          <Search size={16} aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setCursor(0); }}
            placeholder="Search apps and pages"
            aria-label="Search apps and pages"
            aria-controls="lnc-results"
            aria-activedescendant={flat[cursor] ? `lnc-${flat[cursor].key}` : undefined}
          />
          <kbd>Esc</kbd>
          <button type="button" className="lnc__close" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="lnc__body" id="lnc-results" ref={listRef} role="listbox" aria-label="Apps">
          {bySection.length === 0 && <p className="lnc__empty">Nothing matches “{query}”.</p>}
          {bySection.map(({ section, items }) => (
            <section key={section} className="lnc__section" aria-label={section}>
              <h3>{section}</h3>
              <div className="lnc__grid">
                {items.map((item) => {
                  const i = item.index;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.key}
                      id={`lnc-${item.key}`}
                      type="button"
                      role="option"
                      aria-selected={i === cursor}
                      data-index={i}
                      className={`lnc__item${i === cursor ? " lnc__item--on" : ""}`}
                      onMouseEnter={() => setCursor(i)}
                      onClick={() => go(item.route)}
                    >
                      <span className="lnc__icon" aria-hidden="true"><Icon size={16} /></span>
                      <span className="lnc__text">
                        <span className="lnc__label">{item.label}</span>
                        <span className="lnc__desc">{item.parent ? item.parent : item.description}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
        <footer className="lnc__foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Ctrl</kbd>+<kbd>K</kbd> toggle</span></footer>
      </div>
    </div>
  );
}
