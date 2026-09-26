"""
Provider-text normalization: turn untrusted monitoring payloads into plain text.

WHY
---
PRTG (and other collectors) embed HTML in the values they return. Verified in
the live development database: **177 of 1102 alerts** carry markup in
`Alert.description`, all of the shape
``<div class="logmessage">Requested by PRTG System Administrator<div class="moreicon"></div></div>``.
Passing that to the UI means markup is either shown to the operator as literal
tags or - if a future component interpolates it as HTML - executed.

RULE (Phase 6 / Phase 16)
-------------------------
Alert `message` is plain text. The raw provider payload stays available
verbatim for "Details -> Raw Event", but it is never the thing rendered.

Deliberately dependency-free: the backend pins no HTML sanitizer (no bleach,
no nh3), and adding a parsing dependency for this would be disproportionate.
`html.unescape` + an explicit tag-stripping pass is sufficient because the
output is *plain text*, never re-emitted as markup - so there is no parser to
fool.

THE ANGLE-BRACKET GUARANTEE
---------------------------
Whenever markup was detected and stripped (`had_markup is True`), the result
contains **no `<` or `>` characters at all** - any that survive (e.g. from
`&lt;script&gt;`) are re-escaped. That makes the output safe by construction:
even if a future consumer wrongly interpolates `message` as HTML, there is no
tag token in it to execute.

The guarantee is deliberately scoped to the markup path. A string with no
tag-like content at all (e.g. `"a < b > c"`, where `<` is followed by a space)
is returned byte-for-byte unchanged - it is not markup, it must not be
reformatted, and the renderer escapes it as text. Clean values can never be
corrupted by this pass.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

#: A tag-like token: <div>, </div>, <br/>, <!DOCTYPE ...>
_TAG_RE = re.compile(r"</?[a-zA-Z!][^<>]*>")
#: Blocks whose *contents* are dropped, not just their tags.
_DROP_BLOCK_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
#: Tags that imply a line/paragraph break in the rendered text.
_BREAK_TAG_RE = re.compile(
    r"<\s*(br\s*/?|/p|/div|/tr|/li|/h[1-6]|/table)\s*>", re.IGNORECASE
)
#: Any markup at all (used to decide whether to touch the string).
_MARKUP_RE = re.compile(r"<[a-zA-Z!/][^<>]*>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class NormalizedText:
    """Result of normalizing one provider string."""

    #: Plain-text projection. `None` in, `None` out.
    text: str | None
    #: True when markup was actually present and removed.
    had_markup: bool
    #: True when script/style content was discarded (worth auditing).
    dropped_active_content: bool = False


def normalize_provider_text(raw: str | None) -> NormalizedText:
    """Convert a provider string to safe plain text.

    No-op for text that contains no markup, so ordinary messages pass through
    unchanged.
    """
    if raw is None:
        return NormalizedText(text=None, had_markup=False)
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw.strip():
        return NormalizedText(text="", had_markup=False)

    if not _MARKUP_RE.search(raw):
        # Nothing tag-like: leave it completely alone (no entity decoding,
        # no whitespace rewriting) so clean values are never altered.
        return NormalizedText(text=raw, had_markup=False)

    dropped = bool(_DROP_BLOCK_RE.search(raw))
    working = _DROP_BLOCK_RE.sub(" ", raw)
    working = _BREAK_TAG_RE.sub("\n", working)
    working = _TAG_RE.sub("", working)
    # Entities are decoded only after tags are gone, so `&lt;script&gt;` does
    # not become a tag as a result of this step.
    working = html.unescape(working)
    # DEFENCE IN DEPTH: the plain-text projection can never contain an angle
    # bracket, so it cannot be a tag even if a consumer later interpolates it
    # as markup. `&lt;script&gt;` therefore stays escaped text rather than
    # being "helpfully" turned into something that looks executable.
    working = working.replace("<", "&lt;").replace(">", "&gt;")
    # Non-breaking / zero-width characters would defeat whitespace collapsing
    # and render as stray gaps in the UI.
    working = (
        working.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )

    lines = [
        _WHITESPACE_RE.sub(" ", line).strip()
        for line in working.splitlines()
    ]
    text = _BLANK_LINES_RE.sub("\n", "\n".join(lines)).strip()
    return NormalizedText(
        text=text, had_markup=True, dropped_active_content=dropped
    )


def plain_text(raw: str | None) -> str | None:
    """Convenience wrapper returning just the normalized string."""
    return normalize_provider_text(raw).text


def contains_markup(raw: str | None) -> bool:
    """True when `raw` holds tag-like content (drives the Raw Event toggle)."""
    if not raw or not isinstance(raw, str):
        return False
    return bool(_MARKUP_RE.search(raw))
