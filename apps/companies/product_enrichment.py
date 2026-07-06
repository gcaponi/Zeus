"""Deterministic enrichment for the ZEUS DNA Specialista (product DNA).

The DNA Generale uses cognitive layers (identita, confini, tono, ...) and its
guards reason about posture, convictions, anti-patterns — judgment signals.

The DNA Specialista uses technical layers (identita_tecnica, specifiche,
vincoli, ...) and needs guards that reason about technical quality: numeric
density, boundary precision, configuration awareness. This module provides a
dedicated validator + scorer + evidence check, parallel to dna_validator.py
and dna_scoring.py but adapted to the 6 technical layers.

Guard severities
----------------
- CRITICAL → activates safe_mode, score capped at 39.
  layer_completeness (a technical layer is empty)
- HIGH (-15): numeric_density, boundary_precision
- MEDIUM (-8, non-blocking): evidence_grounding, config_awareness, application_depth
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS

# Reuse editorial leak detection from the Generale validator.
from apps.companies.dna_validator import validate_no_editorial_leakage

# Score deltas per severity (mirrors dna_validator.py).
_PENALTY = {"CRITICAL": 100, "HIGH": 15, "MEDIUM": 8}

# Evidence markers — same format as Generale.
_SRC_RE = re.compile(r"\[src:([a-z]+)", re.IGNORECASE)
# A number with optional unit: detects dimensions, tolerances, specs.
_NUMBER_RE = re.compile(r"\b\d+[.,]?\d*\s*(?:mm|cm|m|kg|g|°|bar|pa|kw|w|v|a|hz|mph|rpm|l|cl|ml|inch|in\b)?", re.IGNORECASE)
# Generic filler phrases that signal a weak boundary section.
_GENERIC_BOUNDARY = ("varie", "diversi", "alcuni", "ecc.", "etc", "limiti generici", "")
# Generic config phrases that signal no real variant awareness.
_GENERIC_CONFIG = ("standard", "vari", "diverse", "personalizzabile", "configurabile", "")


@dataclass
class ProductValidationFlag:
    """A single guard finding for a Specialist DNA."""
    guard: str
    severity: str          # CRITICAL | HIGH | MEDIUM
    message: str
    layer: str             # which layer the problem is in (or "global")
    suggestion: str        # actionable fix


@dataclass
class ProductValidationResult:
    """Outcome of validating one Specialist DNA."""
    valid: bool
    score: int                     # 0-100, deterministic
    guards_passed: int
    guards_total: int
    flags: list[ProductValidationFlag] = field(default_factory=list)
    safe_mode: bool = False        # True if any CRITICAL flag


# ---------------------------------------------------------------------------
# Layer introspection helpers
# ---------------------------------------------------------------------------

def _layer_text(content: dict, key: str) -> str:
    """Flatten a layer value (str or dict or list) into one lowercase string."""
    val = content.get(key, "")
    if isinstance(val, dict):
        parts = []
        for v in val.values():
            parts.append(_flatten_value(v))
        return " ".join(p for p in parts if p).lower()
    if isinstance(val, list):
        parts = [_flatten_value(v) for v in val]
        return " ".join(p for p in parts if p).lower()
    return str(val).lower() if val else ""


def _flatten_value(v: Any) -> str:
    if isinstance(v, dict):
        return " ".join(_flatten_value(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(_flatten_value(x) for x in v)
    return str(v) if v is not None else ""


def _layer_is_empty(content: dict, key: str) -> bool:
    val = content.get(key)
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    if isinstance(val, (list, dict)):
        text = _layer_text(content, key)
        return not text.strip()
    return False


# ---------------------------------------------------------------------------
# Guards — each returns a ProductValidationFlag or None
# ---------------------------------------------------------------------------

def _guard_layer_completeness(content: dict) -> ProductValidationFlag | None:
    """Guard 1 — every technical layer must be populated."""
    for key in PRODUCT_LAYER_KEYS:
        if _layer_is_empty(content, key):
            return ProductValidationFlag(
                guard="layer_completeness",
                severity="CRITICAL",
                layer=key,
                message=f"Lo strato tecnico '{key}' è completamente vuoto.",
                suggestion=f"Popola lo strato '{key}' prima di approvare il DNA Specialista.",
            )
    return None


def _guard_numeric_density(content: dict) -> ProductValidationFlag | None:
    """Guard 2 — 'specifiche' must contain real numbers (dimensions, tolerances).

    A 'specifiche' section without any numeric value is a red flag: it means the
    LLM described the product qualitatively instead of extracting parameters.
    """
    specifiche = _layer_text(content, "specifiche")
    if not specifiche:
        return None  # layer_completeness already caught this
    numbers = _NUMBER_RE.findall(specifiche)
    if len(numbers) >= 2:
        return None
    return ProductValidationFlag(
        guard="numeric_density",
        severity="HIGH",
        layer="specifiche",
        message="La sezione 'specifiche' contiene pochi o nessun dato numerico.",
        suggestion="Aggiungi dimensioni, tolleranze, parametri tecnici con valori numerici precisi.",
    )


def _guard_evidence_grounding(content: dict) -> ProductValidationFlag | None:
    """Guard 3 — claims should trace back to a source via [SRC:...] markers."""
    blob = " ".join(_layer_text(content, k) for k in PRODUCT_LAYER_KEYS)
    if _SRC_RE.search(blob):
        return None
    return ProductValidationFlag(
        guard="evidence_grounding",
        severity="MEDIUM",
        layer="global",
        message="Nessun claim traccia una fonte tramite marcatori [SRC:...].",
        suggestion="Marca i claim con [SRC:scrape], [SRC:file:nome] o [SRC:answer].",
    )


def _guard_boundary_precision(content: dict) -> ProductValidationFlag | None:
    """Guard 4 — 'vincoli' must state specific limits, not generic filler.

    A vincoli section with only generic phrases ("vari limiti", "alcune
    restrizioni") is useless for a technical specialist. Real constraints name
    dimensions, materials, conditions.
    """
    vincoli = _layer_text(content, "vincoli")
    if not vincoli:
        return None
    # Check for at least one number or specific material reference.
    has_number = bool(_NUMBER_RE.search(vincoli))
    has_specific = any(w in vincoli for w in ("non modificabi", "fisso", "obbligatori", "incompatib", "massimo", "minimo", "range", "soglia"))
    if has_number or has_specific:
        return None
    return ProductValidationFlag(
        guard="boundary_precision",
        severity="HIGH",
        layer="vincoli",
        message="La sezione 'vincoli' è generica: mancano limiti numerici o condizioni specifiche.",
        suggestion="Specifica limiti dimensionali, materiali incompatibili, range operativi concreti.",
    )


def _guard_config_awareness(content: dict) -> ProductValidationFlag | None:
    """Guard 5 — 'configurazione' should mention real variants, not just 'standard'.

    A product specialist that only says 'standard' or 'personalizzabile' in its
    configuration section hasn't identified the actual decision variables.
    """
    config = _layer_text(content, "configurazione")
    if not config:
        return None
    # Check for variant indicators: tags, sizes, types, or explicit custom logic.
    has_variant = bool(_NUMBER_RE.search(config)) or any(
        w in config for w in ("taglia", "variante", "modello", "codice", "serie", "tipo", "opzione")
    )
    if has_variant:
        return None
    return ProductValidationFlag(
        guard="config_awareness",
        severity="MEDIUM",
        layer="configurazione",
        message="La sezione 'configurazione' non identifica varianti o decisioni concrete.",
        suggestion="Elenca taglie, modelli, codici o le variabili di scelta per il custom.",
    )


def _guard_application_depth(content: dict) -> ProductValidationFlag | None:
    """Guard 6 — 'applicazione' should describe a workflow, not just a use case.

    A shallow application section says "used for X". A good one describes HOW:
    installation steps, prerequisites, maintenance, operational sequence.
    """
    applicazione = _layer_text(content, "applicazione")
    if not applicazione:
        return None
    depth_signals = any(
        w in applicazione
        for w in ("installa", "posa", "montagg", "assembl", "fase", "step", "procedu",
                  "manutenz", "verifica", "protocollo", "sequenza", "prerequis")
    )
    if depth_signals:
        return None
    return ProductValidationFlag(
        guard="application_depth",
        severity="MEDIUM",
        layer="applicazione",
        message="La sezione 'applicazione' descrive l'uso ma non il workflow operativo.",
        suggestion="Aggiungi fasi di installazione, prerequisiti, procedure o manutenzione.",
    )


def _guard_editorial_leakage(content: dict) -> ProductValidationFlag | None:
    """Guard 7 — no editorial fragments in published Specialist DNA.

    Reuses the same leak detector as the Generale (A1) — it already
    scans PRODUCT_LAYER_KEYS content.  CRITICAL if any found.
    """
    matches = validate_no_editorial_leakage(content)
    if matches:
        return ProductValidationFlag(
            guard="editorial_leakage",
            severity="CRITICAL",
            layer="global",
            message=f"Trovati {len(matches)} frammenti editoriali nel DNA: {matches[0][:120]}",
            suggestion="Rigenera il DNA con una pipeline che filtri i marker interni.",
        )
    return None


_GUARDS = [
    _guard_layer_completeness,
    _guard_numeric_density,
    _guard_evidence_grounding,
    _guard_boundary_precision,
    _guard_config_awareness,
    _guard_application_depth,
    _guard_editorial_leakage,
]

_GUARD_NAMES = tuple(g.__name__.replace("_guard_", "") for g in _GUARDS)


# ---------------------------------------------------------------------------
# Validation API
# ---------------------------------------------------------------------------

def validate_product_dna(content: dict) -> ProductValidationResult:
    """Run all 6 technical guards against a Specialist DNA content dict."""
    if not isinstance(content, dict):
        return ProductValidationResult(
            valid=False, score=0, guards_passed=0, guards_total=len(_GUARDS),
            flags=[ProductValidationFlag(
                guard="layer_completeness", severity="CRITICAL", layer="global",
                message="Input non valido: atteso un dict con i 6 layer tecnici.",
                suggestion="Passa un dict valido con le chiavi dei layer tecnici.",
            )],
            safe_mode=True,
        )

    flags: list[ProductValidationFlag] = []
    for guard in _GUARDS:
        flag = guard(content)
        if flag is not None:
            flags.append(flag)

    has_critical = any(f.severity == "CRITICAL" for f in flags)
    guards_passed = len(_GUARDS) - len(flags)

    score = 100
    for f in flags:
        score -= _PENALTY.get(f.severity, 0)
    score = max(score, 0)
    if has_critical:
        score = min(score, 39)

    return ProductValidationResult(
        valid=(len(flags) == 0),
        score=score,
        guards_passed=guards_passed,
        guards_total=len(_GUARDS),
        flags=flags,
        safe_mode=has_critical,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Layer weights for the specialist — specifiche and vincoli weigh most.
_LAYER_WEIGHTS = {
    "identita_tecnica": 1.0,
    "architettura": 1.2,
    "specifiche": 1.5,      # technical heart
    "applicazione": 1.0,
    "vincoli": 1.3,         # sign of technical maturity
    "configurazione": 1.0,
}

_OVERALL_WEIGHTS = {
    "completeness": 0.30,
    "technical_density": 0.30,
    "evidence_density": 0.25,
    "source_diversity": 0.15,
}

_SOURCE_TYPES = {
    "scrape": "scrape",
    "file": "file",
    "note": "note",
    "answer": "answer",
}


def _is_filled(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, 0, False)


def _completeness(content: dict) -> int:
    """% of populated layers, weighted by technical importance."""
    total_weight = 0.0
    earned_weight = 0.0
    for layer_key, weight in _LAYER_WEIGHTS.items():
        if _layer_is_empty(content, layer_key):
            total_weight += weight
            continue
        total_weight += weight
        earned_weight += weight
    if total_weight == 0:
        return 0
    return int(round(earned_weight / total_weight * 100))


def _technical_density(content: dict) -> int:
    """How rich the DNA is in technical signals (numbers + evidence).

    +25 per each (max 100):
    - specifiche has >= 3 numeric values
    - vincoli has numbers or specific limits
    - architettura mentions materials (AISI, INOX, acciaio, alluminio, PVC, etc.)
    - configurazione has variant codes or tags
    """
    score = 0

    specifiche = _layer_text(content, "specifiche")
    if len(_NUMBER_RE.findall(specifiche)) >= 3:
        score += 25

    vincoli = _layer_text(content, "vincoli")
    if _NUMBER_RE.search(vincoli) or any(
        w in vincoli for w in ("non modificabi", "incompatib", "massimo", "minimo", "soglia")
    ):
        score += 25

    architettura = _layer_text(content, "architettura")
    material_tokens = ("aisi", "inox", "acciaio", "allumin", "pvc", "gres", "hpl",
                       "corian", "ottone", "rame", "bronzo", "plastica", "polimer")
    if any(tok in architettura for tok in material_tokens):
        score += 25

    config = _layer_text(content, "configurazione")
    if _NUMBER_RE.search(config) or any(
        w in config for w in ("taglia", "variante", "modello", "codice", "serie")
    ):
        score += 25

    return score


def _evidence_density(content: dict) -> int:
    """Ratio of [SRC:] markers to layers, as a percentage."""
    blob = " ".join(_layer_text(content, k) for k in PRODUCT_LAYER_KEYS)
    source_markers = len(_SRC_RE.findall(blob))
    populated = sum(1 for k in PRODUCT_LAYER_KEYS if not _layer_is_empty(content, k))
    if populated == 0:
        return 0
    ratio = source_markers / populated
    return min(100, int(round(ratio * 33.3)))  # ~3 markers per layer = 100


def _source_diversity(content: dict) -> int:
    """How many distinct source types are cited, scaled to 0-100."""
    blob = " ".join(_layer_text(content, k) for k in PRODUCT_LAYER_KEYS)
    found = set()
    for match in _SRC_RE.finditer(blob):
        stype = match.group(1).lower()
        if stype in _SOURCE_TYPES:
            found.add(_SOURCE_TYPES[stype])
    n = len(found)
    return int(round(n / len(_SOURCE_TYPES) * 100))


def _reproducibility_hash(content: dict) -> str:
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _overall(completeness, technical_density, evidence_density, source_diversity) -> int:
    total = (
        completeness * _OVERALL_WEIGHTS["completeness"]
        + technical_density * _OVERALL_WEIGHTS["technical_density"]
        + evidence_density * _OVERALL_WEIGHTS["evidence_density"]
        + source_diversity * _OVERALL_WEIGHTS["source_diversity"]
    )
    return int(round(total))


def score_product_dna(content: dict) -> dict:
    """Compute the deterministic metrics for a Specialist DNA."""
    if not isinstance(content, dict):
        return {
            "completeness": 0, "technical_density": 0, "evidence_density": 0,
            "source_diversity": 0, "overall": 0,
        }
    completeness = _completeness(content)
    tech_density = _technical_density(content)
    evidence = _evidence_density(content)
    diversity = _source_diversity(content)
    return {
        "completeness": completeness,
        "technical_density": tech_density,
        "evidence_density": evidence,
        "source_diversity": diversity,
        "overall": _overall(completeness, tech_density, evidence, diversity),
    }


# ---------------------------------------------------------------------------
# Full enrichment bundle (mirrors build_enrichment from dna_enrichment.py)
# ---------------------------------------------------------------------------

def build_product_enrichment(content: dict, available_sources: dict | None = None) -> dict:
    """Compute the full cognitive enrichment bundle for a Specialist DNA."""
    val = validate_product_dna(content)
    validation = {
        "valid": val.valid,
        "score": val.score,
        "guards_passed": val.guards_passed,
        "guards_total": val.guards_total,
        "safe_mode": val.safe_mode,
        "flags": [
            {
                "guard": f.guard,
                "severity": f.severity,
                "layer": f.layer,
                "message": f.message,
                "suggestion": f.suggestion,
            }
            for f in val.flags
        ],
    }

    scoring = score_product_dna(content)
    scoring["reproducibility_hash"] = _reproducibility_hash(content)

    # Evidence: reuse the same check_source_consistency from the Generale,
    # since SRC markers work identically across both DNA types.
    from apps.companies.evidence import check_source_consistency
    available = available_sources or {}
    ev = check_source_consistency(content, available)
    evidence = {
        "has_mismatch": ev.has_mismatch,
        "mismatches": [
            {"kind": m.kind, "detail": m.detail, "marker": m.marker}
            for m in ev.mismatches
        ],
        "source_count": len(ev.sources),
        "categories": sorted({s.category for s in ev.sources}),
    }

    return {
        "validation": validation,
        "scoring": scoring,
        "evidence": evidence,
    }
