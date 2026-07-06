"""
A3 — Dati numerici separati dalla prosa.

Deterministic extraction of structured technical specifications from
prose text in DNA content fields.  Used at render time to display numeric
and technical data as a clean spec table, separate from narrative prose.

Design principles
-----------------
- Zero LLM: pure regex extraction, no AI calls.
- Conservative: missed spec → missing data, not hallucination.
- Layered: first tries labeled pairs (Key: Value), then standalone patterns.
- Deduplicated: same spec extracted multiple times yields one row.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns — ordered from most specific to most general
# ---------------------------------------------------------------------------

# Labeled spec: "Label: value unit"  or  "Label: description with specs"
_LABELED_SPEC = re.compile(
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,50}?):\s*"
    r"(\d+[.,]?\d*\s*[–\-×xXa-zA-Z°%\/°C\s\d,.]{1,60})",
)

# Dimension patterns:  "100x50mm", "150 × 75 mm", "200x100x30mm"
_DIMENSION = re.compile(
    r"(\d+[.,]?\d*)\s*[×xX]\s*(\d+[.,]?\d*)"
    r"(?:\s*[×xX]\s*(\d+[.,]?\d*))?\s*(mm|cm|m)\b"
)

# Numeric + unit (standalone)
_VALUE_UNIT = re.compile(
    r"(\d+[.,]?\d*)\s*(mm|cm|m|kg|g|°C|bar|MPa|Pa|kN|N|Nm|"
    r"kW|W|V|A|Hz|l|ml|inch|in|%|t|kg/m²|kg/m³|N/mm²)\b"
)

# Material / standard codes
_MATERIAL = re.compile(
    r"\b(AISI\s*\d+[A-Za-z]?|INOX|UNI\s*EN\s*\d+|"
    r"DIN\s*\d+|EN\s*\d+-\d+|ISO\s*\d+)\b"
)

# Range pattern:  "da X unit a Y unit"  or  "X – Y unit"
_RANGE = re.compile(
    r"(?:da|tra|compreso)\s+(-?\d+[.,]?\d*)\s*(mm|cm|m|°C|kg|bar)\s*"
    r"(?:a|e|–|-)\s*(-?\d+[.,]?\d*)\s*(mm|cm|m|°C|kg|bar)",
    re.IGNORECASE,
)

# Top-level keys that might contain technical headings / specs categories
_TECHNICAL_HEADING = re.compile(
    r"^(Dimensioni|Carico|Portata|Peso|Materiale|Temperatura|"
    r"Pressione|Spessore|Lunghezza|Larghezza|Altezza|Diametro|"
    r"Tolleranza|Normativa|Grado|Classe|Colore|Finitura)",
    re.IGNORECASE | re.MULTILINE,
)


def extract_technical_specs(text: str) -> list[dict[str, str]]:
    """Extract structured technical specs from a prose paragraph.

    Returns a list of dicts, each with keys:
      - ``category``: broad group (e.g. "Materiale", "Dimensioni", "Specifica")
      - ``label``: human-readable parameter name
      - ``value``: extracted value string

    Example
    -------
    >>> extract_technical_specs(
    ...     "Dimensioni: 100x50mm, 150x75mm. Peso: 2kg. AISI 304."
    ... )
    [
        {"category": "Specifica", "label": "Dimensioni", "value": "100x50mm, 150x75mm"},
        {"category": "Specifica", "label": "Peso", "value": "2kg"},
        {"category": "Materiale", "label": "Materiale", "value": "AISI 304"},
    ]
    """
    if not text or not text.strip():
        return []

    specs: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(category: str, label: str, value: str) -> None:
        key = f"{category}|{label}|{value}"
        if key not in seen:
            seen.add(key)
            specs.append({"category": category, "label": label, "value": value})

    # 1. Labeled specs (Key: Value) — most informative
    for m in _LABELED_SPEC.finditer(text):
        label = m.group(1).strip().rstrip(":").strip()
        value = m.group(2).strip().rstrip(",").strip()
        if label and value and len(label) > 1:
            # Determine category from label
            cat = _categorize_label(label)
            _add(cat, label, value)

    # 2. Material codes
    for m in _MATERIAL.finditer(text):
        code = m.group(0).strip()
        # Skip if already part of a labeled spec
        if not _already_captured(specs, code):
            _add("Materiale", "Materiale", code)

    # 3. Dimension patterns not already captured
    for m in _DIMENSION.finditer(text):
        dims = m.group(0).strip()
        if not _already_captured(specs, dims):
            label = "Dimensioni"
            # Try to find a more specific label from nearby text
            ctx = text[max(0, m.start() - 40):m.start()]
            ctx_label = _extract_context_label(ctx)
            if ctx_label:
                label = ctx_label
            _add("Dimensioni", label, dims)

    # 4. Range specs
    for m in _RANGE.finditer(text):
        val = m.group(0).strip().rstrip(",")
        if not _already_captured(specs, val):
            label = f"Da {m.group(1)}{m.group(2)} a {m.group(3)}{m.group(4)}"
            _add("Intervallo", label, val)

    # 5. Standalone value+unit (if not already covered)
    for m in _VALUE_UNIT.finditer(text):
        val = m.group(0).strip().rstrip(",")
        if not _already_captured(specs, val):
            ctx_label = _try_headless_label(text, m.start(), m.group(1), m.group(2))
            _add("Specifica tecnica", ctx_label, val)

    return specs


def _categorize_label(label: str) -> str:
    """Map a label to a broad category."""
    lw = label.lower().strip()
    if any(w in lw for w in ("dimensione", "dimensioni", "larghezza", "lunghezza", "altezza", "diametro", "spessore")):
        return "Dimensioni"
    if any(w in lw for w in ("materiale", "acciaio", "leghe", "composizione")):
        return "Materiale"
    if any(w in lw for w in ("carico", "portata", "resistenza", "trazione", "snervamento")):
        return "Carico"
    if any(w in lw for w in ("temperatura", "termico", "termica")):
        return "Temperatura"
    if any(w in lw for w in ("pressione", "pressioni")):
        return "Pressione"
    if any(w in lw for w in ("peso", "massa", "densità")):
        return "Peso"
    if any(w in lw for w in ("tolleranza", "precisione", "accuratezza")):
        return "Tolleranza"
    if any(w in lw for w in ("norma", "normativa", "certificazione", "classe", "grado")):
        return "Normativa"
    return "Specifica"


def _extract_context_label(ctx: str) -> str | None:
    """Look for a heading-like label in the text before a match."""
    ctx = ctx.strip()
    if not ctx:
        return None
    # Try to find a colon-terminated label
    m = re.search(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}):\s*$", ctx)
    if m:
        return m.group(1).strip().rstrip(":").strip()
    return None


def _try_headless_label(text: str, pos: int, num: str, unit: str) -> str:
    """Try to find a meaningful label for a standalone value+unit."""
    # Look backwards for common spec prefixes
    prefix = text[max(0, pos - 60):pos].strip()
    m = re.search(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}):\s*$", prefix)
    if m:
        lbl = m.group(1).strip().rstrip(":").strip()
        if len(lbl) > 1:
            return lbl
    # Specific unit-based defaults
    unit_defaults = {
        "mm": "Dimensione",
        "cm": "Dimensione",
        "m": "Dimensione",
        "kg": "Peso",
        "g": "Peso",
        "°C": "Temperatura",
        "bar": "Pressione",
        "MPa": "Pressione",
        "kN": "Carico",
        "N": "Carico",
    }
    return unit_defaults.get(unit, f"{num} {unit}")


def _already_captured(specs: list[dict[str, str]], value: str) -> bool:
    """Check if a value string is already in the extracted specs."""
    vn = value.lower().strip()
    return any(vn in s["value"].lower() for s in specs)


def content_has_specs(content: dict) -> bool:
    """Check whether any layer in a DNA content dict contains technical specs."""
    return any(isinstance(val, str) and extract_technical_specs(val) for val in content.values())


def content_specs_count(content: dict) -> int:
    """Count total extracted specs across all layers."""
    total = 0
    for val in content.values():
        if isinstance(val, str):
            total += len(extract_technical_specs(val))
    return total


# ---------------------------------------------------------------------------
# Convenience: extract specs for all layers at once
# ---------------------------------------------------------------------------

def extract_all_layer_specs(content: dict, layer_keys: list[str]) -> dict[str, list[dict[str, str]]]:
    """Extract specs per layer, returning ``{ layer_key: [specs...] }``.

    Used by view context builders to attach specs to each section.
    """
    result: dict[str, list[dict[str, str]]] = {}
    for key in layer_keys:
        text = content.get(key, "")
        if isinstance(text, str):
            specs = extract_technical_specs(text)
            if specs:
                result[key] = specs
    return result
