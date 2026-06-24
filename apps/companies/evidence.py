"""Evidence grounding — parse [SRC:...] markers and verify source consistency.

The prompt (Task 3) instructs the LLM to tag every grounded claim with a source
marker. This module is the receiving side: it extracts those markers from a
generated DNA and checks them against the sources that were actually available
at generation time. A marker referencing a file that was never uploaded, or a
note category when no client note was provided, is a mismatch — a signal that
the LLM may have fabricated its grounding.

Pure Python, no LLM. Works on DNAGeneraleSchema instances or plain dicts
(as stored in CompanyDNA.content).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from apps.companies.dna_schemas import DNAGeneraleSchema

# Matches [SRC:type] or [SRC:type:ref]. Case-insensitive.
# Groups: 1=category, 2=optional ref (e.g. file name).
_SRC_RE = re.compile(r"\[src:([a-z]+)(?::([^\]]+))?\]", re.IGNORECASE)

# Categories recognized as valid source types.
_VALID_CATEGORIES = {"scrape", "file", "note", "answer"}


@dataclass
class SourceRef:
    """A single source marker extracted from the DNA."""
    category: str           # scrape | file | note | answer
    ref: str | None = None  # for file: the filename/identifier; else None
    raw: str = ""           # the original marker text, e.g. "[SRC:file:brochure.pdf]"


@dataclass
class SourceMismatch:
    """A grounding claim that does not match an available source."""
    kind: str               # unknown_category | missing_file | category_unavailable
    detail: str             # human-readable explanation
    marker: str = ""        # the offending marker


@dataclass
class SourceConsistencyResult:
    """Outcome of checking a DNA's markers against available sources."""
    has_mismatch: bool = False
    mismatches: list[SourceMismatch] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Marker extraction
# ---------------------------------------------------------------------------

def _flatten(obj: Any) -> str:
    """Recursively flatten a schema/dict/list into one string (preserve case)."""
    parts: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            parts.append(_flatten(v))
    elif isinstance(obj, list):
        for item in obj:
            parts.append(_flatten(item))
    elif obj is not None:
        parts.append(str(obj))
    return " ".join(p for p in parts if p)


def _to_payload(dna) -> str:
    """Return the flattened text of a DNA, whether schema or dict."""
    if isinstance(dna, DNAGeneraleSchema):
        return _flatten(dna.model_dump())
    if isinstance(dna, dict):
        return _flatten(dna)
    return ""


def extract_sources(dna) -> list[SourceRef]:
    """Extract all [SRC:...] markers from a DNA, de-duplicated, preserving order.

    Accepts a DNAGeneraleSchema or a dict. Returns SourceRef objects with the
    category and (for file markers) the referenced identifier.
    """
    blob = _to_payload(dna)
    seen: set[tuple[str, str | None]] = set()
    refs: list[SourceRef] = []
    for match in _SRC_RE.finditer(blob):
        category = match.group(1).lower()
        ref = match.group(2)
        ref = ref.strip() if ref else None
        key = (category, ref)
        if key in seen:
            continue
        seen.add(key)
        refs.append(SourceRef(category=category, ref=ref, raw=match.group(0)))
    return refs


# ---------------------------------------------------------------------------
# Source consistency check
# ---------------------------------------------------------------------------

def check_source_consistency(dna, available: dict) -> SourceConsistencyResult:
    """Verify that every [SRC:...] marker in the DNA is backed by a real source.

    `available` is a dict describing what was actually provided to the LLM:
        {
            "scrape": bool,       # was the website scraped?
            "note":   bool,       # was a client note provided?
            "files":  list[str],  # filenames of uploaded documents
            "answer": bool,       # (optional) were interview answers provided?
        }

    A mismatch is raised when:
    - a marker category is unknown (e.g. [SRC:wiki])
    - a [SRC:file:name] references a file not in `available["files"]`
    - a [SRC:note]/[SRC:scrape]/[SRC:answer] is present but that source was
      not provided (category_unavailable)
    """
    refs = extract_sources(dna)
    available_files = {str(f) for f in available.get("files", [])}
    mismatches: list[SourceMismatch] = []

    for ref in refs:
        # Unknown category entirely.
        if ref.category not in _VALID_CATEGORIES:
            mismatches.append(SourceMismatch(
                kind="unknown_category",
                detail=f"Categoria fonte sconosciuta: '{ref.category}' nel marcatore {ref.raw}",
                marker=ref.raw,
            ))
            continue

        # File markers must reference a real available file.
        if ref.category == "file":
            if ref.ref and ref.ref not in available_files:
                mismatches.append(SourceMismatch(
                    kind="missing_file",
                    detail=(
                        f"Fonte file '{ref.ref}' citata nel DNA ma non tra i documenti "
                        f"disponibili ({ref.raw})"
                    ),
                    marker=ref.raw,
                ))
            continue

        # Boolean categories: presence must match availability.
        if available.get(ref.category, False) is False:
            mismatches.append(SourceMismatch(
                kind="category_unavailable",
                detail=(
                    f"Categoria '{ref.category}' citata nel DNA ({ref.raw}) ma "
                    f"questa fonte non era disponibile al momento della generazione"
                ),
                marker=ref.raw,
            ))

    return SourceConsistencyResult(
        has_mismatch=bool(mismatches),
        mismatches=mismatches,
        sources=refs,
    )
