"""Deterministic cognitive scoring for the ZEUS DNA Generale.

Six metrics computed in pure Python — no LLM. Where the validator
(dna_validator.py) asks "is this DNA clean of problems?", the scorer asks
"how cognitively rich is this DNA?". The two are orthogonal: a DNA can be
structurally valid but cognitively shallow, or rich but with a critical gap.

All metrics are 0-100 ints for easy comparison and rendering. The scoring
rewards the signals that distinguish a "founder's DNA" (judgment, trade-offs,
boundaries, evidence) from a "brochure DNA" (all positive, no friction).

Inspired by CouncilIA's lib/scoring.ts — Math Layer separate from Narrative.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from apps.companies.dna_schemas import DNAGeneraleSchema, coerce_dna_generale_content

# Completeness weights — not all layers matter equally for a manufacturing DNA.
# nucleo_tecnico (the technical core) and confini (sign of maturity) weigh most.
LAYER_WEIGHTS = {
    "identita": 1.2,            # foundation
    "modelli_mentali": 1.0,
    "nucleo_tecnico": 1.5,      # technical heart
    "confini": 1.3,             # sign of maturity
    "tono": 0.8,
    "logica_decisionale": 1.2,
}

# Overall is a weighted blend. Cognitive tension and evidence carry the most
# weight: they separate a brochure from a founder's DNA.
_OVERALL_WEIGHTS = {
    "completeness": 0.25,
    "cognitive_tension": 0.35,
    "evidence_density": 0.25,
    "source_diversity": 0.15,
}

# Confidence thresholds — deterministic combination, no LLM judgment.
_CONF_HIGH = (0.6, 40, 80)   # evidence_density, cognitive_tension, completeness
_CONF_LOW = (0.2, 15)        # below either → LOW

# Source types we recognize in [SRC:type:...] markers and their category.
_SOURCE_TYPES = {
    "scrape": "scrape",
    "file": "file",
    "note": "note",
    "answer": "answer",
}

# Generic conviction tokens — "qualità" alone is not a real conviction.
_GENERIC_CONVICTION_TOKENS = ("qualita", "qualità", "eccellenza", "competenza", "professionalità")
# A real trade-off reads like a tension between two values.
_TRADEOFF_TOKENS = ("vs", " versus ", "più", "piu", "rinunci", "preferendo", "scambiamo", "sacrific")

# Evidence markers, case-insensitive (content may be lowercased by _flatten).
_SRC_RE = re.compile(r"\[src:([a-z]+)", re.IGNORECASE)


@dataclass
class DNAScore:
    """The six deterministic metrics for one DNA, plus the blended overall."""
    completeness: int          # % of weighted fields populated across 6 layers
    cognitive_tension: int     # 0-100, internal friction (higher = richer)
    evidence_density: int      # grounded sources / total populated fields (%)
    source_diversity: int      # how many distinct source types are cited (%)
    confidence: str            # LOW | MEDIUM | HIGH
    reproducibility_hash: str  # sha256 of the normalized payload
    overall: int               # 0-100, weighted combination of the above


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_schema(dna) -> DNAGeneraleSchema | None:
    """Accept a schema instance or a dict; return None if it can't be coerced."""
    if isinstance(dna, DNAGeneraleSchema):
        return dna
    if isinstance(dna, dict):
        try:
            return coerce_dna_generale_content(dna)
        except ValidationError:
            return None
    return None


def _flatten(obj: Any) -> str:
    """Recursively flatten a schema/dict/list into one lowercase string."""
    parts: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            parts.append(_flatten(v))
    elif isinstance(obj, list):
        for item in obj:
            parts.append(_flatten(item))
    elif obj is not None:
        parts.append(str(obj).lower())
    return " ".join(p for p in parts if p)


def _layer_dict(dna: DNAGeneraleSchema, key: str) -> dict:
    layer = getattr(dna, key)
    return layer.model_dump() if hasattr(layer, "model_dump") else dict(layer)


def _is_filled(value: Any) -> bool:
    """A field counts as populated if it has non-blank content."""
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, 0, False)


# ---------------------------------------------------------------------------
# Metric: completeness (weighted)
# ---------------------------------------------------------------------------

def _completeness(dna: DNAGeneraleSchema) -> int:
    """% of fields populated, weighted by LAYER_WEIGHTS.

    Each layer contributes proportionally to its weight; within a layer every
    field counts equally. Empty fields inside an otherwise-populated layer
    reduce that layer's contribution.
    """
    total_weight = 0.0
    earned_weight = 0.0
    for layer_key, weight in LAYER_WEIGHTS.items():
        layer = _layer_dict(dna, layer_key)
        fields = list(layer.values())
        if not fields:
            continue
        filled = sum(1 for v in fields if _is_filled(v))
        ratio = filled / len(fields)
        total_weight += weight
        earned_weight += weight * ratio
    if total_weight == 0:
        return 0
    return int(round(earned_weight / total_weight * 100))


# ---------------------------------------------------------------------------
# Metric: cognitive tension (0-100, higher = richer)
# ---------------------------------------------------------------------------

def _cognitive_tension(dna: DNAGeneraleSchema) -> int:
    """Measure the healthy internal friction that separates a DNA from a brochure.

    Five independent signals, +20 each:
    - confini.anti_pattern present → the company states what it does NOT do
    - nucleo_tecnico.trade_off_scelti names a real trade-off
    - logica_decisionale.filosofia_custom is specific (not generic)
    - tono.esempi carries wrong-vs-right contrast
    - identita.convinzioni are specific (not just "qualità")
    """
    score = 0

    anti_pattern = _layer_dict(dna, "confini").get("anti_pattern", [])
    if anti_pattern:
        score += 20

    trade_off = _layer_dict(dna, "nucleo_tecnico").get("trade_off_scelti", "")
    if _flatten(trade_off) and any(tok in _flatten(trade_off) for tok in _TRADEOFF_TOKENS):
        score += 20

    filosofia = _layer_dict(dna, "logica_decisionale").get("filosofia_custom", "")
    filosofia_flat = _flatten(filosofia)
    if filosofia_flat and len(filosofia_flat.strip()) > 20 and not any(
        tok in filosofia_flat for tok in _GENERIC_CONVICTION_TOKENS
    ):
        score += 20

    esempi = _layer_dict(dna, "tono").get("esempi", [])
    if esempi:
        score += 20

    convinzioni = _layer_dict(dna, "identita").get("convinzioni", [])
    # A conviction is "specific" if it is not a bare generic token on its own
    # (e.g. "qualita" alone is marketing fluff, not a non-negotiable conviction).
    specific = [
        c for c in convinzioni
        if _flatten(c).strip() and _flatten(c).strip() not in _GENERIC_CONVICTION_TOKENS
    ]
    if specific:
        score += 20

    return score


# ---------------------------------------------------------------------------
# Metric: evidence density
# ---------------------------------------------------------------------------

def _count_populated_fields(dna: DNAGeneraleSchema) -> int:
    """Count every non-empty scalar field across the 6 layers."""
    count = 0
    for layer_key in LAYER_WEIGHTS:
        layer = _layer_dict(dna, layer_key)
        for value in layer.values():
            if isinstance(value, list):
                count += sum(1 for v in value if _is_filled(v))
            elif _is_filled(value):
                count += 1
    return count


def _evidence_density(dna: DNAGeneraleSchema) -> int:
    """Ratio of [SRC:] markers to populated fields, as a percentage.

    Measures grounding: how many claims trace back to a source. A field with
    multiple sources still counts the field once; the marker just needs to
    exist nearby.
    """
    blob = _flatten(dna.model_dump())
    source_markers = len(_SRC_RE.findall(blob))
    fields = _count_populated_fields(dna)
    if fields == 0:
        return 0
    ratio = source_markers / fields
    return min(100, int(round(ratio * 100)))


# ---------------------------------------------------------------------------
# Metric: source diversity
# ---------------------------------------------------------------------------

def _source_diversity(dna: DNAGeneraleSchema) -> int:
    """How many distinct source types are cited, scaled to 0-100.

    scrape / file / note / answer are the recognized types. 4 types = 100,
    3 = 75, 2 = 50, 1 = 25, 0 = 0.
    """
    blob = _flatten(dna.model_dump())
    found_types = set()
    for match in _SRC_RE.finditer(blob):
        stype = match.group(1).lower()
        if stype in _SOURCE_TYPES:
            found_types.add(_SOURCE_TYPES[stype])
    n = len(found_types)
    return int(round(n / len(_SOURCE_TYPES) * 100))


# ---------------------------------------------------------------------------
# Metric: confidence
# ---------------------------------------------------------------------------

def _confidence(completeness: int, cognitive_tension: int, evidence_density_pct: int) -> str:
    """Deterministic LOW/MEDIUM/HIGH. evidence_density_pct is 0-100."""
    evidence_ratio = evidence_density_pct / 100
    if evidence_ratio > _CONF_HIGH[0] and cognitive_tension > _CONF_HIGH[1] and completeness > _CONF_HIGH[2]:
        return "HIGH"
    if evidence_ratio < _CONF_LOW[0] or cognitive_tension < _CONF_LOW[1]:
        return "LOW"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Metric: reproducibility hash
# ---------------------------------------------------------------------------

def _reproducibility_hash(dna: DNAGeneraleSchema) -> str:
    """SHA256 of the normalized payload — for future determinism tests.

    Two identical inputs must produce the same DNA; this hash makes that
    property checkable without deep-comparing the whole content.
    """
    payload = json.dumps(dna.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Metric: overall (weighted blend)
# ---------------------------------------------------------------------------

def _overall(completeness, cognitive_tension, evidence_density, source_diversity) -> int:
    total = (
        completeness * _OVERALL_WEIGHTS["completeness"]
        + cognitive_tension * _OVERALL_WEIGHTS["cognitive_tension"]
        + evidence_density * _OVERALL_WEIGHTS["evidence_density"]
        + source_diversity * _OVERALL_WEIGHTS["source_diversity"]
    )
    return int(round(total))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_dna(dna) -> DNAScore:
    """Compute the six deterministic metrics for a DNA.

    Accepts a DNAGeneraleSchema or a dict (as stored in CompanyDNA.content).
    A payload that cannot be coerced into the schema scores all-zero.
    """
    schema = _to_schema(dna)
    if schema is None:
        return DNAScore(
            completeness=0, cognitive_tension=0, evidence_density=0, source_diversity=0,
            confidence="LOW", reproducibility_hash="", overall=0,
        )

    completeness = _completeness(schema)
    tension = _cognitive_tension(schema)
    evidence = _evidence_density(schema)
    diversity = _source_diversity(schema)
    confidence = _confidence(completeness, tension, evidence)

    return DNAScore(
        completeness=completeness,
        cognitive_tension=tension,
        evidence_density=evidence,
        source_diversity=diversity,
        confidence=confidence,
        reproducibility_hash=_reproducibility_hash(schema),
        overall=_overall(completeness, tension, evidence, diversity),
    )
