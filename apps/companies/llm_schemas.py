"""Pydantic v2 schemas for structured LLM outputs (Instructor-validated).

These schemas replace the hand-made JSON parsing previously done with
``_parse_llm_json`` + custom ``parse`` callables. Semantic validations that
lived in those callables (exactly 10 questions, gap status enum, distinct
starter suggested answers, section_key filtering) are ported here as
Pydantic validators, preserving the original behaviour.

Field values hold Italian content; code and field names stay English
except the canonical DNA layer keys, which are part of the stored format.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from apps.companies.dna_schemas import (
    DNAGeneraleSchema,
    PRODUCT_LAYER_KEYS,
    _schema_text,
)

T = TypeVar("T")


def _none_to_list(value: Any) -> Any:
    """Tolerate explicit ``null`` for list fields (old parsers did ``or []``)."""
    return [] if value is None else value


LenientList = Annotated[list[T], BeforeValidator(_none_to_list)]


def _layer_text(value: Any) -> str:
    """Coerce a narrative DNA layer to text.

    The prompts ask for narrative strings, but models occasionally nest
    dicts/lists; the old flow merged them as-is and normalized downstream.
    Here we normalize at validation time with the same text-flattening
    rules used by the canonical schema bridge.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return _schema_text(value)


# ---------------------------------------------------------------------------
# Gap Engine — answer sufficiency evaluation (company + specialist)
# ---------------------------------------------------------------------------

GAP_STATUSES = ("sufficiente", "insufficiente", "contradicts")


class GapEvaluationItem(BaseModel):
    question_code: str = ""
    status: Literal["sufficiente", "insufficiente", "contradicts"]
    rationale: str = ""


class GapFollowUpItem(BaseModel):
    target_question_code: str = ""
    section_key: str = ""
    principle: str = ""
    question: str = ""
    answer_depth: str = ""
    answer_guidance: str = ""


class GapEvaluationSchema(BaseModel):
    evaluations: LenientList[GapEvaluationItem] = Field(default_factory=list)
    overall_sufficient: bool = False
    follow_ups: LenientList[GapFollowUpItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Question generation (company A1-A10 + specialist D1-D10)
# ---------------------------------------------------------------------------

class QuestionItem(BaseModel):
    code: str = ""
    pool: str = ""
    section_key: str = ""
    principle: str = ""
    question: str = ""
    answer_depth: str = ""
    answer_guidance: str = ""
    suggested_answers: list[str] | None = None

    @field_validator("suggested_answers")
    @classmethod
    def _check_suggested_answers(cls, value):
        if value is None:
            return value
        if len(value) != 3 or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError("must provide exactly 3 non-empty answers")
        cleaned = [item.strip() for item in value]
        if len({item.casefold() for item in cleaned}) != 3:
            raise ValueError("suggested answers must be distinct")
        return cleaned


class QuestionSetSchema(BaseModel):
    questions: list[QuestionItem]

    @model_validator(mode="after")
    def _check_question_count(self):
        if len(self.questions) != 10:
            raise ValueError("LLM must return exactly 10 questions")
        return self


class StarterQuestionSetSchema(QuestionSetSchema):
    """Starter (Foundation) plan: every question ships 3 grounded proposals."""

    @model_validator(mode="after")
    def _check_starter_suggested_answers(self):
        for index, question in enumerate(self.questions):
            if question.suggested_answers is None:
                code = question.code or f"#{index + 1}"
                raise ValueError(
                    f"Foundation question {code} must provide exactly 3 non-empty answers"
                )
        return self


# ---------------------------------------------------------------------------
# Cross-specialist analysis (Motore B)
# ---------------------------------------------------------------------------

class CrossSharedPattern(BaseModel):
    theme: str = ""
    evidence: str = ""
    impact: str = ""
    source_products: LenientList[str] = Field(default_factory=list)


class CrossConflict(BaseModel):
    severity: str = "medium"
    products: LenientList[str] = Field(default_factory=list)
    issue: str = ""
    recommendation: str = ""


class CrossConsolidationProposal(BaseModel):
    target_layer: str = ""
    title: str = ""
    proposed_value: str = ""
    rationale: str = ""
    source_products: LenientList[str] = Field(default_factory=list)


class CrossSpecialistAnalysisSchema(BaseModel):
    summary: str = ""
    shared_patterns: LenientList[CrossSharedPattern] = Field(default_factory=list)
    conflicts: LenientList[CrossConflict] = Field(default_factory=list)
    consolidation_proposals: LenientList[CrossConsolidationProposal] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Specialist DNA self-critique
# ---------------------------------------------------------------------------

class SelfCritiqueProposal(BaseModel):
    section_key: str = ""
    issue: str = ""
    anti_memorization: bool = False
    proposed_text: str = ""


class SelfCritiqueSchema(BaseModel):
    proposals: LenientList[SelfCritiqueProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _filter_valid_proposals(self):
        # Same behaviour as the old parse: silently drop proposals targeting
        # unknown sections or with empty replacement text.
        valid_keys = set(PRODUCT_LAYER_KEYS)
        self.proposals = [
            proposal
            for proposal in self.proposals
            if proposal.section_key in valid_keys and proposal.proposed_text.strip()
        ]
        return self


# ---------------------------------------------------------------------------
# Specialist → Company DNA feedback proposals
# ---------------------------------------------------------------------------

class FeedbackProposalItem(BaseModel):
    target_layer: str = ""
    current_value: str = ""
    proposed_value: str = ""
    rationale: str = ""


class FeedbackProposalsSchema(BaseModel):
    proposals: LenientList[FeedbackProposalItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Consistency audit (Motore C)
# ---------------------------------------------------------------------------

class ConsistencyIssueItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    severity: str = "medium"
    issue_type: str = ""
    title: str = ""
    description: str = ""
    recommendation: str = ""
    company_layer: str = ""
    product_layer: str = ""
    # Free-form in the wild; _normalize_consistency_issues keeps dicts only.
    evidence: Any = None


class ConsistencyAuditSchema(BaseModel):
    summary: str = ""
    issues: LenientList[ConsistencyIssueItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Specialist concept map (pre-DNA planning stage)
# ---------------------------------------------------------------------------

class ConceptEntity(BaseModel):
    name: str = ""
    type: str = ""


class ConceptRelation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(default="", alias="from")
    to: str = ""
    type: str = ""


class ConceptParameter(BaseModel):
    name: str = ""
    value: str = ""
    unit: str = ""
    source: str = ""


class ConceptGap(BaseModel):
    what: str = ""
    why_missing: str = ""
    can_ask: bool = True


class ConceptMapSchema(BaseModel):
    entities: LenientList[ConceptEntity] = Field(default_factory=list)
    relations: LenientList[ConceptRelation] = Field(default_factory=list)
    parameters: LenientList[ConceptParameter] = Field(default_factory=list)
    gaps: LenientList[ConceptGap] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Narrative DNA documents (7 / 6 layer keys as narrative text)
# ---------------------------------------------------------------------------

class NarrativeDNASchema(BaseModel):
    """DNA Generale as client-reviewable narrative (6 layers + sintesi).

    Extra keys are allowed so legacy layer aliases survive validation and
    keep flowing into ``_safe_merge_synthesis`` / ``_normalize_synthesis_layers``
    exactly as before.
    """

    model_config = ConfigDict(extra="allow")

    identita: str = ""
    modelli_mentali: str = ""
    nucleo_tecnico: str = ""
    confini: str = ""
    tono: str = ""
    logica_decisionale: str = ""
    sintesi_cognitiva: str = ""

    _coerce_layers = field_validator(
        "identita",
        "modelli_mentali",
        "nucleo_tecnico",
        "confini",
        "tono",
        "logica_decisionale",
        "sintesi_cognitiva",
        mode="before",
    )(_layer_text)


class ProductDNASchema(BaseModel):
    """Specialist DNA as narrative text (6 technical layers)."""

    model_config = ConfigDict(extra="allow")

    identita_tecnica: str = ""
    architettura: str = ""
    specifiche: str = ""
    applicazione: str = ""
    vincoli: str = ""
    configurazione: str = ""

    _coerce_layers = field_validator(
        "identita_tecnica",
        "architettura",
        "specifiche",
        "applicazione",
        "vincoli",
        "configurazione",
        mode="before",
    )(_layer_text)


class PreDNAGeneraleSchema(DNAGeneraleSchema):
    """Pre-DNA output: canonical 6 structured layers + client-visible sintesi.

    The dna_generale_v1 prompt mandates ``sintesi_cognitiva`` as a top-level
    key alongside the 6 structured layers, so the plain DNAGeneraleSchema
    (which ignores extras) is not enough here.
    """

    sintesi_cognitiva: str = ""
