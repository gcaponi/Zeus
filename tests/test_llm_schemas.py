"""Tests for R2 — Instructor/Pydantic structured generation.

Covers:
- the new llm_schemas validators (semantic rules ported from the old
  hand-made parse callables),
- the _generate_structured_tracked helper (LLMResult tracking + retry on
  ValidationError with decreasing temperatures),
- MockLLMClient structured dispatch over the per-marker JSON.
"""
import json

import pytest
from pydantic import ValidationError

from apps.companies.llm_client import (
    LLMResult,
    MockLLMClient,
    _generate_structured_tracked,
)
from apps.companies.llm_schemas import (
    ConceptMapSchema,
    ConsistencyAuditSchema,
    CrossSpecialistAnalysisSchema,
    FeedbackProposalsSchema,
    GapEvaluationSchema,
    NarrativeDNASchema,
    PreDNAGeneraleSchema,
    ProductDNASchema,
    QuestionSetSchema,
    SelfCritiqueSchema,
    StarterQuestionSetSchema,
)


def _questions_payload(count=10, **extra):
    questions = []
    for i in range(count):
        questions.append({
            "code": f"A{i + 1}",
            "pool": "template",
            "section_key": "identita",
            "principle": "P",
            "question": "Q?",
            "answer_depth": "generica",
            "answer_guidance": "G",
            **extra,
        })
    return {"questions": questions}


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------

class TestQuestionSetSchema:
    def test_requires_exactly_10_questions(self):
        with pytest.raises(ValidationError, match="exactly 10"):
            QuestionSetSchema.model_validate(_questions_payload(count=9))
        with pytest.raises(ValidationError, match="exactly 10"):
            QuestionSetSchema.model_validate(_questions_payload(count=11))
        assert len(QuestionSetSchema.model_validate(_questions_payload()).questions) == 10

    def test_suggested_answers_cleaned_and_checked_when_present(self):
        payload = _questions_payload(suggested_answers=[" one ", "two", "three"])
        schema = QuestionSetSchema.model_validate(payload)
        assert schema.questions[0].suggested_answers == ["one", "two", "three"]

        with pytest.raises(ValidationError):
            QuestionSetSchema.model_validate(_questions_payload(suggested_answers=["a", "b"]))
        with pytest.raises(ValidationError):
            QuestionSetSchema.model_validate(
                _questions_payload(suggested_answers=["same", " Same ", "other"])
            )
        with pytest.raises(ValidationError):
            QuestionSetSchema.model_validate(
                _questions_payload(suggested_answers=["ok", "  ", "fine"])
            )

    def test_starter_requires_suggested_answers(self):
        with pytest.raises(ValidationError, match="Foundation question"):
            StarterQuestionSetSchema.model_validate(_questions_payload())
        payload = _questions_payload(suggested_answers=["one", "two", "three"])
        assert len(StarterQuestionSetSchema.model_validate(payload).questions) == 10


class TestGapEvaluationSchema:
    def test_status_enum_enforced(self):
        with pytest.raises(ValidationError):
            GapEvaluationSchema.model_validate({
                "evaluations": [{"question_code": "A1", "status": "boh"}],
            })

    def test_valid_statuses_and_lenient_lists(self):
        schema = GapEvaluationSchema.model_validate({
            "evaluations": [
                {"question_code": "A1", "status": "sufficiente"},
                {"question_code": "A2", "status": "insufficiente"},
                {"question_code": "A3", "status": "contradicts"},
            ],
            "overall_sufficient": True,
            "follow_ups": None,
        })
        assert schema.overall_sufficient is True
        assert schema.follow_ups == []
        assert len(schema.evaluations) == 3


class TestSelfCritiqueSchema:
    def test_filters_invalid_section_keys_and_empty_text(self):
        schema = SelfCritiqueSchema.model_validate({
            "proposals": [
                {"section_key": "specifiche", "issue": "x", "proposed_text": "nuovo"},
                {"section_key": "non_esiste", "issue": "x", "proposed_text": "nuovo"},
                {"section_key": "vincoli", "issue": "x", "proposed_text": "   "},
            ]
        })
        assert [p.section_key for p in schema.proposals] == ["specifiche"]


class TestNarrativeDNASchema:
    def test_coerces_nested_layers_to_text(self):
        schema = NarrativeDNASchema.model_validate({
            "identita": {"postura": "Postura X", "convinzioni": ["a", "b"]},
            "modelli_mentali": ["p1", "p2"],
        })
        assert "Postura X" in schema.identita
        assert "a" in schema.identita
        assert schema.modelli_mentali == "p1, p2"

    def test_alias_keys_survive_for_safe_merge(self):
        schema = NarrativeDNASchema.model_validate({
            "identita": "ok",
            "identita_e_promessa": "Alias content",
        })
        dumped = schema.model_dump()
        assert dumped["identita_e_promessa"] == "Alias content"


class TestProductDNASchema:
    def test_six_layers_as_text(self):
        payload = {key: f"testo {key}" for key in (
            "identita_tecnica", "architettura", "specifiche",
            "applicazione", "vincoli", "configurazione",
        )}
        schema = ProductDNASchema.model_validate(payload)
        assert schema.model_dump() == payload


class TestConceptMapSchema:
    def test_relation_from_alias_roundtrip(self):
        schema = ConceptMapSchema.model_validate({
            "entities": [{"name": "INOX", "type": "materiale"}],
            "relations": [{"from": "a", "to": "b", "type": "determina"}],
            "parameters": None,
            "gaps": [],
        })
        dumped = schema.model_dump(by_alias=True)
        assert dumped["relations"] == [{"from": "a", "to": "b", "type": "determina"}]
        assert dumped["parameters"] == []


# ---------------------------------------------------------------------------
# _generate_structured_tracked helper
# ---------------------------------------------------------------------------

class _FlakyStructuredClient:
    """Fake client: fails validation `failures` times, then succeeds."""

    def __init__(self, failures=0):
        self.failures = failures
        self.temperatures = []

    def generate_structured_result(
        self, prompt, response_model, *, model=None, temperature=None, system_prompt=None,
    ):
        self.temperatures.append(temperature)
        if len(self.temperatures) <= self.failures:
            # Force a ValidationError as instructor would after max_retries.
            response_model.model_validate({"evaluations": [{"status": "boh"}]})
        result = LLMResult(
            text='{"evaluations": [], "overall_sufficient": true, "follow_ups": []}',
            tokens_in=11, tokens_out=7, cost=0.001, latency_ms=42,
        )
        instance = response_model.model_validate(json.loads(result.text))
        return result, instance


class TestGenerateStructuredTracked:
    def test_returns_llm_result_and_validated_instance(self):
        client = _FlakyStructuredClient()
        result, instance = _generate_structured_tracked(
            client, "prompt", response_model=GapEvaluationSchema, context="t",
        )
        assert isinstance(result, LLMResult)
        assert result.tokens_in == 11 and result.cost == 0.001
        assert isinstance(instance, GapEvaluationSchema)
        assert instance.overall_sufficient is True
        assert client.temperatures == [0.7]

    def test_retries_on_validation_error_with_decreasing_temperatures(self):
        client = _FlakyStructuredClient(failures=2)
        result, instance = _generate_structured_tracked(
            client, "prompt", response_model=GapEvaluationSchema, context="t",
        )
        assert client.temperatures == [0.7, 0.4, 0.2]
        assert instance.overall_sufficient is True

    def test_raises_runtime_error_after_exhausting_retries(self):
        client = _FlakyStructuredClient(failures=3)
        with pytest.raises(RuntimeError, match=r"after 3 attempts \[t\]"):
            _generate_structured_tracked(
                client, "prompt", response_model=GapEvaluationSchema, context="t",
            )
        assert client.temperatures == [0.7, 0.4, 0.2]

    def test_non_validation_errors_propagate_without_retry(self):
        class Boom:
            def generate_structured_result(self, *a, **kw):
                raise ConnectionError("api down")

        with pytest.raises(ConnectionError):
            _generate_structured_tracked(
                Boom(), "prompt", response_model=GapEvaluationSchema, context="t",
            )


# ---------------------------------------------------------------------------
# MockLLMClient structured dispatch (per-marker JSON as single source)
# ---------------------------------------------------------------------------

class TestMockStructuredDispatch:
    def test_gap_engine_marker(self):
        result, instance = MockLLMClient().generate_structured_result(
            "GAP_ENGINE_EVAL ...", GapEvaluationSchema,
        )
        assert isinstance(result, LLMResult) and result.tokens_in > 0
        assert isinstance(instance, GapEvaluationSchema)
        assert instance.overall_sufficient is True

    def test_company_questions_starter_marker(self):
        instance = MockLLMClient().generate_structured(
            "GENERA_DOMANDE_A1_A20 PIANO: starter", StarterQuestionSetSchema,
        )
        assert len(instance.questions) == 10
        for question in instance.questions:
            assert len(question.suggested_answers) == 3

    def test_company_questions_professional_marker(self):
        instance = MockLLMClient().generate_structured(
            "GENERA_DOMANDE_A1_A20 PIANO: professional", QuestionSetSchema,
        )
        assert len(instance.questions) == 10
        assert instance.questions[0].suggested_answers is None

    def test_product_questions_marker(self):
        instance = MockLLMClient().generate_structured(
            "GENERA_DOMANDE_D1_D20 PIANO: enterprise", QuestionSetSchema,
        )
        assert len(instance.questions) == 10

    def test_cross_specialist_marker(self):
        instance = MockLLMClient().generate_structured(
            "CROSS_SPECIALIST_ANALYSIS", CrossSpecialistAnalysisSchema,
        )
        assert len(instance.consolidation_proposals) == 1
        assert instance.consolidation_proposals[0].target_layer == "nucleo_tecnico"

    def test_consistency_audit_marker(self):
        instance = MockLLMClient().generate_structured(
            "CONSISTENCY_AUDIT_V1", ConsistencyAuditSchema,
        )
        assert len(instance.issues) == 1
        assert instance.issues[0].severity == "medium"

    def test_concept_map_marker(self):
        instance = MockLLMClient().generate_structured(
            "CONCEPT_MAP_SPECIALISTA", ConceptMapSchema,
        )
        assert len(instance.entities) == 7
        dumped = instance.model_dump(by_alias=True)
        assert dumped["relations"][0]["from"] == "acciaio INOX AISI 304"

    def test_self_critique_marker(self):
        instance = MockLLMClient().generate_structured(
            "SELF_CRITIQUE_SPECIALISTA", SelfCritiqueSchema,
        )
        assert len(instance.proposals) == 3
        assert any(p.anti_memorization for p in instance.proposals)

    def test_feedback_proposals_marker(self):
        instance = MockLLMClient().generate_structured(
            "FEEDBACK_SPECIALISTA_GENERALE", FeedbackProposalsSchema,
        )
        assert len(instance.proposals) == 3

    def test_global_synthesis_marker_coerces_layers(self):
        instance = MockLLMClient().generate_structured(
            "SINTESI_GLOBALE_DNA", NarrativeDNASchema,
        )
        assert "sintetizzata" in instance.identita
        assert instance.sintesi_cognitiva.startswith("Sintesi cognitiva globale")

    def test_product_markers(self):
        client = MockLLMClient()
        for prompt in (
            "SEED_VARIANT MATERIALI",
            "SEED_VARIANT WORKFLOW",
            "MERGE_DNA_SPECIALISTA",
            "ANALISI_NEURALE_SPECIALISTA",
        ):
            instance = client.generate_structured(prompt, ProductDNASchema)
            assert instance.identita_tecnica.strip(), prompt

    def test_pre_dna_fallback_json_keeps_sintesi_cognitiva(self):
        instance = MockLLMClient().generate_structured(
            "prompt senza marker", PreDNAGeneraleSchema,
        )
        assert instance.sintesi_cognitiva.strip()
        assert instance.identita.postura.strip()


class TestExtraBodyGate:
    """Regressione OCR review: extra_body V4 solo per modelli deepseek-v4."""

    def test_v4_flash_plain_gets_effort_max(self):
        from apps.companies.llm_client import _extra_body_for

        assert _extra_body_for("deepseek-v4-flash", structured=False) == {
            "reasoning_effort": "max"
        }

    def test_v4_pro_structured_gets_thinking_disabled(self):
        from apps.companies.llm_client import _extra_body_for

        assert _extra_body_for("deepseek-v4-pro", structured=True) == {
            "thinking": {"type": "disabled"}
        }

    def test_legacy_models_get_no_extra_body(self):
        from apps.companies.llm_client import _extra_body_for

        for model in ("deepseek-chat", "gpt-4o", "gpt-4o-mini"):
            assert _extra_body_for(model, structured=False) is None
            assert _extra_body_for(model, structured=True) is None
