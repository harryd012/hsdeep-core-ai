"use client";

import { memo } from "react";
import TileShell from "@/components/viz/TileShell";

/**
 * MarkdownTile — renders a small, SAFE subset of markdown (headings, bold,
 * italic, inline code, bullet lists, links, paragraphs) WITHOUT
 * dangerouslySetInnerHTML. Only a fixed, allowlisted inline syntax is parsed,
 * so an operator note can never inject markup.
 *
 * This is an operational-notes tile, not a general markdown engine; unknown
 * syntax is shown as plain text.
 */

function renderInline(text: string, keyBase: string): React.ReactNode[] {
  // Inline tokens: **bold**, *italic*, `code`, [text](href)
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] !== undefined) {
      nodes.push(<strong key={`${keyBase}-b-${i}`}>{m[2]}</strong>);
    } else if (m[3] !== undefined) {
      nodes.push(<em key={`${keyBase}-i-${i}`}>{m[3]}</em>);
    } else if (m[4] !== undefined) {
      nodes.push(
        <code
          key={`${keyBase}-c-${i}`}
          style={{
            background: "rgba(0,217,255,.08)",
            color: "var(--cyan)",
            padding: "1px 4px",
            borderRadius: 3,
            fontFamily: "monospace",
            fontSize: 11,
          }}
        >
          {m[4]}
        </code>,
      );
    } else if (m[5] !== undefined && m[6] !== undefined) {
      nodes.push(
        <a
          key={`${keyBase}-l-${i}`}
          href={m[6]}
          style={{ color: "var(--cyan)", textDecoration: "none" }}
          target={m[6].startsWith("http") ? "_blank" : undefined}
          rel={m[6].startsWith("http") ? "noopener noreferrer" : undefined}
        >
          {m[5]}
        </a>,
      );
    }
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export interface MarkdownTileProps {
  title: string;
  /** Raw markdown text (operator note). */
  source: string;
  subtitle?: string;
  lastCollectedAt?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
  loading?: boolean;
  error?: unknown;
}

export default memo(function MarkdownTile({
  title,
  source,
  subtitle,
  lastCollectedAt,
  onRefresh,
  refreshing,
  loading,
  error,
}: MarkdownTileProps) {
  const lines = source.split(/\r?\n/);
  const blocks: React.ReactNode[] = [];
  let list: string[] | null = null;

  const flushList = (key: string) => {
    if (!list) return;
    blocks.push(
      <ul key={key} style={{ margin: "4px 0", paddingLeft: 18, fontSize: 12, color: "var(--fg)", lineHeight: 1.6 }}>
        {list.map((li, j) => <li key={j}>{renderInline(li, `${key}-${j}`)}</li>)}
      </ul>,
    );
    list = null;
  };

  lines.forEach((line, idx) => {
    const t = line.trim();
    if (!t) {
      flushList(`ul-${idx}`);
      return;
    }
    if (t.startsWith("- ") || t.startsWith("* ")) {
      (list ??= []).push(t.slice(2));
      return;
    }
    flushList(`ul-${idx}`);
    if (t.startsWith("### ")) {
      blocks.push(<h4 key={idx} style={{ fontSize: 12, fontWeight: 700, margin: "8px 0 2px", color: "var(--fg)" }}>{renderInline(t.slice(4), `h-${idx}`)}</h4>);
    } else if (t.startsWith("## ")) {
      blocks.push(<h3 key={idx} style={{ fontSize: 13, fontWeight: 700, margin: "8px 0 2px", color: "var(--cyan)" }}>{renderInline(t.slice(3), `h-${idx}`)}</h3>);
    } else if (t.startsWith("# ")) {
      blocks.push(<h2 key={idx} style={{ fontSize: 14, fontWeight: 800, margin: "6px 0 2px", color: "var(--cyan)" }}>{renderInline(t.slice(2), `h-${idx}`)}</h2>);
    } else {
      blocks.push(<p key={idx} style={{ margin: "4px 0", fontSize: 12, color: "var(--fg)", lineHeight: 1.6 }}>{renderInline(t, `p-${idx}`)}</p>);
    }
  });
  flushList("ul-end");

  return (
    <TileShell
      title={title}
      subtitle={subtitle}
      onRefresh={onRefresh}
      refreshing={refreshing}
      lastCollectedAt={lastCollectedAt}
      loading={loading}
      error={error}
      data={source.trim() ? source : null}
      minHeight={120}
    >
      <div>{blocks}</div>
    </TileShell>
  );
});
