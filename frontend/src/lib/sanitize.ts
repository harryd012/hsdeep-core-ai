/**
 * stripHtml — remove markup from backend-provided text before it reaches the
 * UI. Some collectors (e.g. PRTG) embed rich HTML in event/alert messages;
 * rendering that raw leaks tags into the dashboard. This strips tags and
 * decodes the basic entities so only readable text remains.
 */
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

function decodeEntities(s: string): string {
  return s
    .replace(/&#(\d+);/g, (_, code: string) => {
      const n = Number(code);
      return Number.isFinite(n) && n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : " ";
    })
    .replace(/&([a-z]+);/gi, (_m, name: string) => NAMED_ENTITIES[name.toLowerCase()] ?? " ");
}

export function stripHtml(input: string): string {
  if (!input) return "";
  // Decode twice: some collectors double-encode (`&amp;lt;div&amp;gt;`).
  let text = decodeEntities(decodeEntities(input));
  // Now that encoded markup is real markup, strip every tag.
  text = text.replace(/<[^>]*>/g, " ");
  // Decode whatever legit entities remain in the visible text.
  text = decodeEntities(text);
  return text.replace(/\s+/g, " ").trim();
}
