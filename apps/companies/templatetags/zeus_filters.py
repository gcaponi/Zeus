"""
ZEUS template filters.

Custom filters for safe markdown-to-HTML and other text transformations.
Zero external dependencies — pure regex.
"""
import html
import re
from django import template
from django.utils.safestring import mark_safe
from django.template.defaultfilters import stringfilter

register = template.Library()


@register.filter
@stringfilter
def markdownify(text: str) -> str:
    """
    Convert a restricted subset of markdown to safe HTML.

    Handles the patterns that the LLM produces in DNA fields:
      - **bold**       → <strong>
      - *italic*        → <em>
      - `code`          → <code>
      - [text](url)     → <a href="url" rel="noopener noreferrer">
      - ## / ### headers → <h2>/<h3>
      - - item lists    → <ul><li>
      - 1. item lists   → <ol><li>
      - \n\n            → paragraph break
    """
    if not text:
        return ""

    # Escape first so unmatched HTML never becomes executable after mark_safe.
    text = html.escape(text, quote=True)

    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # --- Block-level transformations (before inline) ---

    # Split into blocks on double newline
    blocks = re.split(r"\n{2,}", text)
    result_blocks = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Unordered list
        if re.match(r"^(\s*[-*+]\s)", block, re.MULTILINE):
            items = re.findall(r"^\s*[-*+]\s+(.*)", block, re.MULTILINE)
            lis = "\n".join(f"    <li>{_inline_markdown(i)}</li>" for i in items)
            result_blocks.append(f"<ul>\n{lis}\n</ul>")
            continue

        # Ordered list
        if re.match(r"^(\s*\d+[.)]\s)", block, re.MULTILINE):
            items = re.findall(r"^\s*\d+[.)]\s+(.*)", block, re.MULTILINE)
            lis = "\n".join(f"    <li>{_inline_markdown(i)}</li>" for i in items)
            result_blocks.append(f"<ol>\n{lis}\n</ol>")
            continue

        # Headers
        h2 = re.match(r"^##\s+(.*)", block)
        if h2:
            result_blocks.append(f"<h2>{_inline_markdown(h2.group(1))}</h2>")
            continue
        h3 = re.match(r"^###\s+(.*)", block)
        if h3:
            result_blocks.append(f"<h3>{_inline_markdown(h3.group(1))}</h3>")
            continue

        # Regular paragraph
        result_blocks.append(f"<p>{_inline_markdown(block)}</p>")

    return mark_safe("\n".join(result_blocks))


def _inline_markdown(text: str) -> str:
    """Convert inline markdown patterns to HTML (safe, no dependency)."""
    # Code (must be before bold/italic to avoid ** collisions)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)

    # Italic
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)

    # Inline code (already handled above)

    # Links — only safe URL schemes become <a> tags: the input can be LLM
    # output, so javascript:/data: URLs (and attribute break-out via quotes)
    # must be neutralised, not copied verbatim into href.
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _render_link, text)

    return text


_SAFE_LINK_PREFIXES = ("http://", "https://", "mailto:", "/", "#")


def _render_link(match: re.Match) -> str:
    """Render a markdown link, or fall back to the plain label when unsafe."""
    label, raw_url = match.group(1), match.group(2).strip()
    # Source text is already HTML-escaped; unescape the URL for validation so
    # query strings with `&` still link, then re-escape the href.
    url = html.unescape(raw_url)
    if any(char in url for char in ('"', "'", "<", ">")) or any(c.isspace() for c in url):
        return label
    if not url.lower().startswith(_SAFE_LINK_PREFIXES):
        return label
    return (
        f'<a href="{html.escape(url, quote=True)}" '
        f'rel="noopener noreferrer">{label}</a>'
    )
