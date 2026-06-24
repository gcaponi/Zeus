"""Pydantic schemas for the ZEUS DNA Generale — the single source of truth
for the 6-layer cognitive structure. Used by Instructor to validate LLM output.

All layer names use the cliente-aligned vocabulary (Italian). The schema itself
is written in English (code rule), field values hold Italian content.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

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
