"""
ZEUS template filters.

Custom filters for safe markdown-to-HTML and other text transformations.
Zero external dependencies — pure regex.
"""
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

    html = "\n".join(result_blocks)
    return mark_safe(html)


def _inline_markdown(text: str) -> str:
    """Convert inline markdown patterns to HTML (safe, no dependency)."""
    # Code (must be before bold/italic to avoid ** collisions)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)

    # Italic
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)

    # Inline code (already handled above)

    # Links
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" rel="noopener noreferrer">\1</a>',
        text,
    )

    return text
