"""Self-critique loop — Delphi-inspired 2-pass refinement of the DNA Generale.

A single LLM reviews its own output. NOT multi-agent: the 6 layers are
dimensions of one company, not agents that compete. The loop applies the
Conflict Matrix — 5 cross-layer coherence checks — and reformulates any
layer flagged as TENSION or CONFLICT.

Protocol (simplified from CouncilIA Round 2+3):
  Pass 1 — Cross-layer challenge: the LLM checks coherence between layers.
  Pass 2 — Refinement: the LLM reformulates layers with TENSION/CONFLICT.

Cost: 2 extra LLM calls on the complete DNA only. ~+15-30s, within the
300s Celery timeout. The pre-DNA is NOT critiqued (too early, no answers).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from apps.companies.dna_schemas import DNAGeneraleSchema


# ---------------------------------------------------------------------------
# Conflict Matrix — the 5 cross-layer coherence checks
# ---------------------------------------------------------------------------

# (checker, target, description) — checker reviews target for the described tension.
CONFLICT_MATRIX = [
    ("confini", "nucleo_tecnico", "Stai promettendo oltre i limiti dichiarati?"),
    ("logica_decisionale", "identita", "Le decisioni sono coerenti con la postura?"),
    ("tono", "confini", "Il tono riflette i limiti reali?"),
    ("nucleo_tecnico", "modelli_mentali", "I pilastri guidano davvero l'approccio tecnico?"),
    ("identita", "tono", "La postura corrisponde al registro?"),
]


# ---------------------------------------------------------------------------
# Structured output schemas (validated by Pydantic / Instructor)
# ---------------------------------------------------------------------------

class CrossLayerCheckItem(BaseModel):
    """One cross-layer coherence check result."""
    checker: str = Field(description="The layer doing the checking")
    target: str = Field(description="The layer being checked")
    status: str = Field(description="OK | TENSION | CONFLICT")
    note: str = Field(default="", description="Explanation of the finding")


class CrossLayerCheckResult(BaseModel):
    """The structured output of the cross-layer challenge pass."""
    checks: list[CrossLayerCheckItem] = Field(
        default_factory=list,
        description="One item per Conflict Matrix pair",
    )


# Alias kept for clarity at call sites.
CrossLayerCheck = CrossLayerCheckItem


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CritiqueReport:
    """Audit record of a self-critique run."""
    checks: list[CrossLayerCheckItem] = field(default_factory=list)
    refined: bool = False
    tensions: list[str] = field(default_factory=list)   # human-readable tension notes


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_challenge_prompt(dna: DNAGeneraleSchema) -> str:
    """Pass 1 prompt — asks the LLM to run the Conflict Matrix on the DNA."""
    pairs = "\n".join(
        f"- {checker} -> {target}: {desc}"
        for checker, target, desc in CONFLICT_MATRIX
    )
    dna_json = json.dumps(dna.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return f"""Sei ZEUS. Hai generato questo DNA Generale. Esegui la Conflict Matrix:
verifica la coerenza tra gli strati.

DNA GENERALE:
{dna_json}

CONFLICT MATRIX (5 check):
{pairs}

Per ogni check, restituisci uno stato:
- OK: gli strati sono coerenti
- TENSION: attrito gestibile, da rivedere
- CONFLICT: contraddizione diretta che va risolta

Output: un JSON con una lista "checks", ogni elemento ha checker, target,
status, note. Esattamente 5 check, uno per coppia della Conflict Matrix.
""".strip()


def _build_refine_prompt(dna: DNAGeneraleSchema, checks: list[CrossLayerCheckItem]) -> str:
    """Pass 2 prompt — asks the LLM to reformulate layers flagged as problematic."""
    problems = [c for c in checks if c.status != "OK"]
    problem_lines = "\n".join(
        f"- [{c.status}] {c.checker} -> {c.target}: {c.note}"
        for c in problems
    )
    dna_json = json.dumps(dna.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return f"""Sei ZEUS. Ecco il DNA Generale e i risultati del cross-layer check.

DNA GENERALE:
{dna_json}

PROBLEMI RILEVATI:
{problem_lines}

Per ogni TENSION o CONFLICT, riformula lo strato problematico per
risolvere l'incoerenza. Mantieni la struttura a 6 strati e lo stesso
schema. Non inventare informazioni: se un dato manca, scrivi
"Da chiarire in intervista".

Output: il DNA Generale completo riformulato come JSON a 6 strati
(identita, modelli_mentali, nucleo_tecnico, confini, tono, logica_decisionale).
Lingua: italiano tecnico.
""".strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_cross_layer_check(dna: DNAGeneraleSchema, client) -> list[CrossLayerCheckItem]:
    """Pass 1 — run the Conflict Matrix via the LLM. Returns exactly 5 checks.

    If the LLM returns fewer/more, we still return what we got; the caller
    inspects statuses, not count. The test expects 5 because the prompt asks
    for one check per Conflict Matrix pair.
    """
    prompt = _build_challenge_prompt(dna)
    result = client.generate_structured(
        prompt=prompt,
        response_model=CrossLayerCheckResult,
        temperature=0.2,
    )
    # Instructor returns a CrossLayerCheckResult instance.
    if isinstance(result, CrossLayerCheckResult):
        return result.checks
    # Some clients may return a bare list (defensive).
    if isinstance(result, list):
        return result
    return getattr(result, "checks", [])


def self_critique_dna(dna: DNAGeneraleSchema, client) -> tuple[DNAGeneraleSchema, CritiqueReport]:
    """Run the full 2-pass self-critique loop on a DNA.

    Returns (refined_dna, report). If all cross-layer checks are OK, the DNA
    is returned unchanged with report.refined=False. Otherwise Pass 2
    reformulates and report.refined=True.
    """
    checks = run_cross_layer_check(dna, client)
    tensions = [c.note for c in checks if c.status != "OK" and c.note]

    if not any(c.status != "OK" for c in checks):
        return dna, CritiqueReport(checks=checks, refined=False, tensions=[])

    # Pass 2 — refine.
    refine_prompt = _build_refine_prompt(dna, checks)
    refined = client.generate_structured(
        prompt=refine_prompt,
        response_model=DNAGeneraleSchema,
        temperature=0.2,
    )
    # Ensure the refined output is a proper schema instance.
    if not isinstance(refined, DNAGeneraleSchema):
        refined = DNAGeneraleSchema.model_validate(refined)

    return refined, CritiqueReport(checks=checks, refined=True, tensions=tensions)
