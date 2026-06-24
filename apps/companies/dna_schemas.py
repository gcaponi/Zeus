"""Pydantic schemas for the ZEUS DNA Generale — the single source of truth
for the 6-layer cognitive structure. Used by Instructor to validate LLM output.

All layer names use the cliente-aligned vocabulary (Italian). The schema itself
is written in English (code rule), field values hold Italian content.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

LAYER_KEYS = [
    "identita",
    "modelli_mentali",
    "nucleo_tecnico",
    "confini",
    "tono",
    "logica_decisionale",
]

# Consultant-style titles shown to the imprenditore (Sintesi Cognitiva renderer).
LAYER_TITLES = {
    "identita": "Chi siamo e come ci poniamo",
    "modelli_mentali": "Come ragioniamo",
    "nucleo_tecnico": "Cosa ci rende unici",
    "confini": "I nostri confini",
    "tono": "Il nostro tono",
    "logica_decisionale": "Come prendiamo decisioni",
}


class Identita(BaseModel):
    """Layer 1 — WHO the agent is and HOW it positions itself."""
    postura: str = Field(description="How the agent positions itself: side-by-side or leading")
    convinzioni: list[str] = Field(
        default_factory=list,
        description="3 non-negotiable convictions of the company",
    )


class ModelliMentali(BaseModel):
    """Layer 2 — HOW the agent thinks (principles + reading sequence)."""
    pilastri: list[str] = Field(
        default_factory=list,
        description="3-5 principles that guide every technical decision",
    )
    sequenza_di_lettura: str = Field(
        description="The thought sequence when facing a problem",
    )


class NucleoTecnico(BaseModel):
    """Layer 3 — WHAT makes the technical approach unique + product families."""
    approccio_distintivo: str = Field(description="What makes the approach technically unique")
    trade_off_scelti: str = Field(description="The trade-off chosen deliberately")
    famiglie_prodotto: list[str] = Field(
        default_factory=list,
        description="High-level product family names (from scraping, 90% focus)",
    )


class Confini(BaseModel):
    """Layer 4 — What the agent does NOT do / does NOT promise."""
    anti_pattern: list[str] = Field(
        default_factory=list,
        description="What is NOT done / NOT promised",
    )
    richieste_rifiutate: str = Field(
        description="Which requests are refused/discouraged and why",
    )


class Tono(BaseModel):
    """Layer 5 — HOW the agent speaks (register + wrong-vs-right examples)."""
    registro: str = Field(description="Speaking register, e.g. technical-accessible")
    esempi: list[dict] = Field(
        default_factory=list,
        description="Wrong-vs-right phrase examples: [{sbagliato, giusto}]",
    )


class LogicaDecisionale(BaseModel):
    """Layer 6 — HOW decisions are made (custom philosophy + escalation)."""
    filosofia_custom: str = Field(description="Philosophy on custom / out-of-standard work")
    escalation: str = Field(description="When a problem exceeds competence")


class DNAGeneraleSchema(BaseModel):
    """The full DNA Generale — 6 cognitive layers. Single source of truth."""
    identita: Identita
    modelli_mentali: ModelliMentali
    nucleo_tecnico: NucleoTecnico
    confini: Confini
    tono: Tono
    logica_decisionale: LogicaDecisionale


def _schema_text(value: Any) -> str:
    if isinstance(value, list):
        parts = [_schema_text(item).strip() for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        preferred_keys = (
            "descrizione",
            "description",
            "testo",
            "text",
            "contenuto",
            "content",
            "value",
        )
        for key in preferred_keys:
            if key in value:
                return _schema_text(value.get(key))
        if len(value) == 1:
            return _schema_text(next(iter(value.values())))
        parts = [_schema_text(item).strip() for item in value.values()]
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _schema_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := _schema_text(item).strip())]
    text = _schema_text(value).strip()
    return [text] if text else []


def _layer_list(layer: Any, field: str) -> list[str]:
    if isinstance(layer, dict) and field in layer:
        return _schema_list(layer.get(field))
    return _schema_list(layer)


def _schema_examples(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _narrative_content_to_schema_payload(content: dict[str, Any]) -> dict[str, Any]:
    """Convert stored narrative layer text into the canonical 6-layer shape.

    The generation pipeline rewrites the complete DNA as client-reviewable
    narrative paragraphs. Validation/scoring still need the canonical schema;
    this bridge keeps the deterministic math layer compatible with stored DNA.
    """
    identita = content.get("identita")
    modelli = content.get("modelli_mentali")
    nucleo = content.get("nucleo_tecnico")
    confini = content.get("confini")
    tono = content.get("tono")
    decisionale = content.get("logica_decisionale")

    identita_text = _schema_text(identita).strip()
    modelli_text = _schema_text(modelli).strip()
    nucleo_text = _schema_text(nucleo).strip()
    confini_text = _schema_text(confini).strip()
    tono_text = _schema_text(tono).strip()
    decisionale_text = _schema_text(decisionale).strip()

    return {
        "identita": {
            "postura": identita_text,
            "convinzioni": _layer_list(identita, "convinzioni"),
        },
        "modelli_mentali": {
            "pilastri": _layer_list(modelli, "pilastri"),
            "sequenza_di_lettura": modelli_text,
        },
        "nucleo_tecnico": {
            "approccio_distintivo": nucleo_text,
            "trade_off_scelti": nucleo_text,
            "famiglie_prodotto": _layer_list(nucleo, "famiglie_prodotto"),
        },
        "confini": {
            "anti_pattern": _layer_list(confini, "anti_pattern"),
            "richieste_rifiutate": confini_text,
        },
        "tono": {
            "registro": tono_text,
            "esempi": _schema_examples(tono.get("esempi")) if isinstance(tono, dict) else [],
        },
        "logica_decisionale": {
            "filosofia_custom": decisionale_text,
            "escalation": decisionale_text,
        },
    }


def coerce_dna_generale_content(content: Any) -> DNAGeneraleSchema:
    """Return a DNAGeneraleSchema from canonical or narrative stored content.

    Raises pydantic.ValidationError for payloads that are not ZEUS 6-layer DNA
    at all. Dicts with at least one known layer key are normalized so empty
    layers become layer-specific validation flags instead of a global schema
    mismatch.
    """
    if isinstance(content, DNAGeneraleSchema):
        return content
    if not isinstance(content, dict):
        return DNAGeneraleSchema.model_validate(content)
    try:
        return DNAGeneraleSchema.model_validate(content)
    except ValidationError:
        if not any(key in content for key in LAYER_KEYS):
            raise
        return DNAGeneraleSchema.model_validate(_narrative_content_to_schema_payload(content))
