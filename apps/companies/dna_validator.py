"""Deterministic validation layer for the ZEUS DNA Generale.

7 consistency guards evaluate a generated DNA without calling any LLM —
pure Python, fully reproducible. Inspired by CouncilIA's separation of the
Math Layer from the Narrative Layer: the LLM produces text, this module
judges it.

Input may be a DNAGeneraleSchema instance (from generate_structured) or a
plain dict (as stored in CompanyDNA.content). A malformed dict is caught and
reported as a CRITICAL safe-mode result instead of raising.

Guard severities
----------------
- CRITICAL → activates safe_mode, score capped at 39.
  layer_completeness (a whole cognitive layer is empty)
- HIGH (-15): cognitive_tension, boundary_realism, identity_coherence
- MEDIUM (-8, non-blocking): evidence_grounding, tone_anchoring, decisional_depth
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from apps.companies.dna_schemas import DNAGeneraleSchema, coerce_dna_generale_content

# Score deltas per severity.
_PENALTY = {"CRITICAL": 100, "HIGH": 15, "MEDIUM": 8}

# Registers considered generic / unanchored — tone is not specific.
_GENERIC_REGISTERS = {
    "professionale", "professional", "formale", "formal",
    "cordiale", "friendly", "amichevole", "corporativo", "corporate", "",
}
# Escalation phrases that signal a shallow decision logic.
_GENERIC_ESCALATION = {
    "chiedere al superiore", "domandare al superiore", "chiedere al capo",
    "passare al responsabile", "chiedere al responsabile", "chiedere al capoufficio",
}
# Posture keywords used by the identity_coherence guard.
_LEADING_TOKENS = ("guidiamo", "guida", "guidare", "leading", "lead", "visione", "comand", "diretti", "decidiamo per", "imponiamo")
_DEFERENT_TOKENS = ("deferente", "ossequiente", "sottomess", "deferential", "umile", "remissivo")

# Case-insensitive: _text() lowercases values before matching.
_SRC_RE = re.compile(r"\[src:[^\]]+\]", re.IGNORECASE)


@dataclass
class DNAValidationFlag:
    """A single guard finding."""
    guard: str
    severity: str          # CRITICAL | HIGH | MEDIUM
    message: str
    layer: str             # which layer the problem is in (or "global")
    suggestion: str        # actionable fix


@dataclass
class DNAValidationResult:
    """Outcome of validating one DNA."""
    valid: bool
    score: int                     # 0-100, deterministic
    guards_passed: int
    guards_total: int
    flags: list[DNAValidationFlag] = field(default_factory=list)
    safe_mode: bool = False        # True if any CRITICAL flag


# ---------------------------------------------------------------------------
# Layer introspection helpers
# ---------------------------------------------------------------------------

def _layer_is_empty(layer: Any) -> bool:
    """A layer is empty when every field is blank or an empty collection."""
    if layer is None:
        return True
    if isinstance(layer, BaseModel := type(layer)) and hasattr(layer, "model_dump"):
        values = layer.model_dump().values()
    elif isinstance(layer, dict):
        values = layer.values()
    else:
        return False
    for v in values:
        if isinstance(v, (list, dict)):
            if v:
                return False
        elif isinstance(v, str):
            if v.strip():
                return False
        elif v not in (None, 0, False):
            return False
    return True


def _field(layer: Any, name: str, default: Any = "") -> Any:
    if hasattr(layer, name):
        return getattr(layer, name)
    if isinstance(layer, dict):
        return layer.get(name, default)
    return default


def _text(*values: Any) -> str:
    """Flatten arbitrary field values into a single lowercase string."""
    parts: list[str] = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    parts.extend(str(x) for x in item.values())
                else:
                    parts.append(str(item))
        elif isinstance(v, dict):
            parts.extend(str(x) for x in v.values())
        else:
            parts.append(str(v))
    return " ".join(parts).lower()


# ---------------------------------------------------------------------------
# Guards — each returns a DNAValidationFlag or None
# ---------------------------------------------------------------------------

def _guard_layer_completeness(dna) -> DNAValidationFlag | None:
    """Guard 1 — every layer must have at least one populated field."""
    for key in ("identita", "modelli_mentali", "nucleo_tecnico", "confini", "tono", "logica_decisionale"):
        layer = getattr(dna, key)
        if _layer_is_empty(layer):
            return DNAValidationFlag(
                guard="layer_completeness",
                severity="CRITICAL",
                layer=key,
                message=f"Lo strato '{key}' e completamente vuoto.",
                suggestion=f"Popola almeno un campo dello strato '{key}' prima di approvare il DNA.",
            )
    return None


def _guard_cognitive_tension(dna) -> DNAValidationFlag | None:
    """Guard 2 — an echo-chamber guard. A rich DNA carries internal friction.

    The canonical signal is confini.anti_pattern: "what we do NOT do" is the
    clearest proof the company has drawn a boundary. A DNA that only states
    what it does is a brochure, not a cognitive model.
    """
    has_anti_pattern = bool(_field(dna.confini, "anti_pattern", []))
    if has_anti_pattern:
        return None
    return DNAValidationFlag(
        guard="cognitive_tension",
        severity="HIGH",
        layer="confini",
        message="DNA senza tensione interna: nessun anti-pattern, trade-off o richiesta rifiutata.",
        suggestion="Aggiungi almeno un anti_pattern, un trade_off_scelti o una richiesta rifiutata.",
    )


def _guard_evidence_grounding(dna) -> DNAValidationFlag | None:
    """Guard 3 — claims should trace back to a source via [SRC:...] markers."""
    blob = _text(dna.model_dump())
    if _SRC_RE.search(blob):
        return None
    return DNAValidationFlag(
        guard="evidence_grounding",
        severity="MEDIUM",
        layer="global",
        message="Nessun claim traccia una fonte tramite marcatori [SRC:...].",
        suggestion="Marca i claim con [SRC:scrape], [SRC:file:nome] o [SRC:note].",
    )


def _guard_tone_anchoring(dna) -> DNAValidationFlag | None:
    """Guard 4 — a generic register without examples is not anchored."""
    registro = _text(_field(dna.tono, "registro", "")).strip()
    esempi = _field(dna.tono, "esempi", [])
    if registro in _GENERIC_REGISTERS and not esempi:
        return DNAValidationFlag(
            guard="tone_anchoring",
            severity="MEDIUM",
            layer="tono",
            message=f"Registro generico ('{registro}') senza esempi wrong-vs-right.",
            suggestion="Specifica il registro (es. 'tecnico-accessibile') e aggiungi almeno un esempio.",
        )
    return None


def _guard_boundary_realism(dna) -> DNAValidationFlag | None:
    """Guard 5 — many product families but no anti-patterns = unbounded promise."""
    families = _field(dna.nucleo_tecnico, "famiglie_prodotto", [])
    anti = _field(dna.confini, "anti_pattern", [])
    if len(families) >= 2 and not anti:
        return DNAValidationFlag(
            guard="boundary_realism",
            severity="HIGH",
            layer="confini",
            message="DNA sbilanciato: molte famiglie prodotto ma nessun anti_pattern.",
            suggestion="Dichiara cosa l'azienda NON fa o NON promette (confini.anti_pattern).",
        )
    return None


def _guard_decisional_depth(dna) -> DNAValidationFlag | None:
    """Guard 6 — a shallow custom philosophy + generic escalation is surface-level."""
    filosofia = _field(dna.logica_decisionale, "filosofia_custom", "")
    escalation = _text(_field(dna.logica_decisionale, "escalation", ""))
    if len(filosofia.strip()) < 20 and escalation.strip() in _GENERIC_ESCALATION:
        return DNAValidationFlag(
            guard="decisional_depth",
            severity="MEDIUM",
            layer="logica_decisionale",
            message="Logica decisionale superficiale: filosofia custom breve ed escalation generica.",
            suggestion="Approfondisci filosofia_custom (>20 char) e specifica l'escalation tecnica.",
        )
    return None


def _guard_identity_coherence(dna) -> DNAValidationFlag | None:
    """Guard 7 — posture must not contradict the tone register."""
    postura = _text(_field(dna.identita, "postura", ""))
    registro = _text(_field(dna.tono, "registro", ""))
    is_leading = any(tok in postura for tok in _LEADING_TOKENS)
    is_deferent = any(tok in registro for tok in _DEFERENT_TOKENS)
    is_guiding_postura = any(tok in postura for tok in ("affianca", "affianchiamo", "side", "affianco"))
    if is_leading and is_deferent:
        return DNAValidationFlag(
            guard="identity_coherence",
            severity="HIGH",
            layer="identita",
            message="Incoerenza identita-tono: postura 'guidante' con registro deferente.",
            suggestion="Allinea il registro del tono alla postura dichiarata in identita.",
        )
    return None


_GUARDS = [
    _guard_layer_completeness,
    _guard_cognitive_tension,
    _guard_evidence_grounding,
    _guard_tone_anchoring,
    _guard_boundary_realism,
    _guard_decisional_depth,
    _guard_identity_coherence,
]

_GUARD_NAMES = tuple(g.__name__.replace("_guard_", "") for g in _GUARDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_dna(dna) -> DNAValidationResult:
    """Run all 7 guards against a DNA.

    Accepts a DNAGeneraleSchema or a dict. A dict that cannot be coerced into
    the schema yields a CRITICAL safe-mode result with score 0.
    """
    if isinstance(dna, dict):
        try:
            dna = coerce_dna_generale_content(dna)
        except ValidationError:
            return DNAValidationResult(
                valid=False,
                score=0,
                guards_passed=0,
                guards_total=len(_GUARDS),
                flags=[DNAValidationFlag(
                    guard="layer_completeness",
                    severity="CRITICAL",
                    layer="global",
                    message="Il contenuto non rispetta lo schema DNA a 6 strati.",
                    suggestion="Rigenera il DNA: il payload non e strutturato correttamente.",
                )],
                safe_mode=True,
            )
    elif not isinstance(dna, DNAGeneraleSchema):
        return DNAValidationResult(
            valid=False, score=0, guards_passed=0, guards_total=len(_GUARDS),
            flags=[DNAValidationFlag(
                guard="layer_completeness", severity="CRITICAL", layer="global",
                message="Input non valido: atteso DNAGeneraleSchema o dict.",
                suggestion="Passa uno schema DNAGeneraleSchema o un dict valido.",
            )],
            safe_mode=True,
        )

    flags: list[DNAValidationFlag] = []
    for guard in _GUARDS:
        flag = guard(dna)
        if flag is not None:
            flags.append(flag)

    has_critical = any(f.severity == "CRITICAL" for f in flags)
    guards_passed = len(_GUARDS) - len(flags)

    # Deterministic scoring: start at 100, subtract penalties, floor at 0.
    score = 100
    for f in flags:
        score -= _PENALTY.get(f.severity, 0)
    score = max(score, 0)
    if has_critical:
        score = min(score, 39)

    return DNAValidationResult(
        valid=(len(flags) == 0),
        score=score,
        guards_passed=guards_passed,
        guards_total=len(_GUARDS),
        flags=flags,
        safe_mode=has_critical,
    )
