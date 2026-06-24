"""Tests for the ZEUS Fondamenta plan: 6-layer DNA schema + Instructor integration."""
import json

import pytest
from pydantic import ValidationError

from apps.companies.dna_schemas import (
    LAYER_KEYS,
    Confini,
    DNAGeneraleSchema,
    Identita,
    LogicaDecisionale,
    ModelliMentali,
    NucleoTecnico,
    Tono,
)
from apps.companies.llm_client import MockLLMClient
from apps.companies.models import Company, CompanyDNA, CompanyQuestion, SectionApproval


class TestDNASchema:
    def test_layer_keys_are_six(self):
        assert len(LAYER_KEYS) == 6
        assert set(LAYER_KEYS) == {
            "identita", "modelli_mentali", "nucleo_tecnico",
            "confini", "tono", "logica_decisionale",
        }

    def test_minimal_dna_validates(self):
        """A DNA with all 6 layers, each minimally populated, validates."""
        dna = DNAGeneraleSchema(
            identita=Identita(postura="affianca il cliente", convinzioni=["qualita"]),
            modelli_mentali=ModelliMentali(
                pilastri=["principio 1"], sequenza_di_lettura="parte dal caso d'uso",
            ),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="metodo X",
                trade_off_scelti="velocita vs qualita",
                famiglie_prodotto=["famiglia A"],
            ),
            confini=Confini(
                anti_pattern=["non promettere l'impossibile"],
                richieste_rifiutate="richieste sotto soglia",
            ),
            tono=Tono(
                registro="tecnico-accessibile",
                esempi=[{"sbagliato": "siamo i migliori", "giusto": "per X casi consigliamo Y"}],
            ),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="caso per caso",
                escalation="quando oltrepassa la competenza interna",
            ),
        )
        assert dna.identita.postura == "affianca il cliente"

    def test_invalid_dna_missing_layer_raises(self):
        """Pydantic should reject a DNA missing a required layer."""
        with pytest.raises(ValidationError):
            DNAGeneraleSchema(
                identita=Identita(postura="x", convinzioni=["x"]),
                modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="x"),
                nucleo_tecnico=NucleoTecnico(
                    approccio_distintivo="x", trade_off_scelti="x", famiglie_prodotto=["x"],
                ),
                confini=Confini(anti_pattern=["x"], richieste_rifiutate="x"),
                tono=Tono(registro="x", esempi=[{"sbagliato": "x", "giusto": "x"}]),
                # logica_decisionale MISSING
            )

    def test_dna_to_dict_roundtrip(self):
        """Schema should serialize to a dict that matches the 6-layer JSON shape."""
        dna = DNAGeneraleSchema(
            identita=Identita(postura="p", convinzioni=["c"]),
            modelli_mentali=ModelliMentali(pilastri=["p"], sequenza_di_lettura="s"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["a"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        d = dna.model_dump()
        assert set(d.keys()) == {
            "identita", "modelli_mentali", "nucleo_tecnico",
            "confini", "tono", "logica_decisionale",
        }

    def test_django_section_choices_use_six_layers(self):
        """Django models must use the same 6-layer keys as the Pydantic schema."""
        assert [key for key, _label in SectionApproval.SECTION_KEYS] == LAYER_KEYS
        field = CompanyQuestion._meta.get_field("section_key")
        assert field.default == "logica_decisionale"

    @pytest.mark.django_db
    def test_missing_sections_uses_six_layer_keys(self):
        """CompanyDNA.missing_sections must check approvals against the 6 layers."""
        company = Company.objects.create(schema_name="testco6", name="TestCo")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": {}},
            is_current=True,
        )

        assert set(dna.missing_sections()) == set(LAYER_KEYS)
class TestGenerateStructured:
    def test_mock_returns_valid_dna_schema(self):
        """MockLLMClient.generate_structured must return a DNAGeneraleSchema with 6 layers."""
        client = MockLLMClient()
        dna = client.generate_structured(
            prompt="GENERA DNA GENERALE",
            response_model=DNAGeneraleSchema,
        )
        assert isinstance(dna, DNAGeneraleSchema)
        assert dna.identita.postura  # non-empty
        assert len(dna.nucleo_tecnico.famiglie_prodotto) >= 1
        assert len(dna.confini.anti_pattern) >= 1
        assert len(dna.tono.esempi) >= 1

    def test_mock_generate_structured_to_dict(self):
        """The schema returned must serialize to the 6-layer dict shape."""
        client = MockLLMClient()
        dna = client.generate_structured(
            prompt="GENERA DNA GENERALE",
            response_model=DNAGeneraleSchema,
        )
        d = dna.model_dump()
        assert "chi_siamo" not in d  # old keys must be gone
        assert "identita" in d
        assert "logica_decisionale" in d


@pytest.mark.django_db
class TestQuestionPoolField:
    def test_question_has_pool_field_with_default(self):
        """CompanyQuestion must have a pool field defaulting to 'template'."""
        company = Company.objects.create(schema_name="poolco", name="PoolCo")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"identita": {}},
            is_current=True,
        )
        q = CompanyQuestion.objects.create(
            company=company,
            dna=dna,
            code="A1",
            section_key="confini",
            principle="test",
            question="test?",
        )
        assert q.pool == "template"

    def test_question_pool_choices(self):
        """pool must accept 'template' and 'kb_anchored'."""
        pools = dict(CompanyQuestion._meta.get_field("pool").choices)
        assert "template" in pools
        assert "kb_anchored" in pools


@pytest.mark.django_db
class TestGenerateDnaNotesSeparation:
    def test_notes_and_documents_separated_in_prompt(self, monkeypatch):
        """_generate_dna must place client notes in {{company_notes}} and
        real documents in {{company_documents}}, not concatenate them."""
        from apps.companies import tasks
        from apps.companies.models import Company, CompanyFile, Source

        company = Company.objects.create(schema_name="notesco", name="NotesCo")
        CompanyFile.objects.create(
            company=company,
            original_name="note-azienda.txt",
            content_text="Note importanti del cliente sulle preferenze",
            file_size=50,
        )
        CompanyFile.objects.create(
            company=company,
            original_name="catalogo.pdf",
            content_text="Catalogo prodotti 2026 con specifiche tecniche",
            file_size=100,
        )
        source = Source.objects.create(
            company=company,
            url="https://example.com",
            status="scraped",
            scraped_data={"markdown": "# Example Corp\nProdotti industriali"},
        )

        captured_prompt = {}

        class FakeClient:
            def generate(self, prompt, model=None):
                captured_prompt["prompt"] = prompt
                from apps.companies.llm_client import LLMResult
                return LLMResult(
                    text='{"identita": {"postura": "x", "convinzioni": []}}',
                    tokens_in=100,
                    tokens_out=50,
                    cost=0.0001,
                    latency_ms=100,
                )

        monkeypatch.setattr(tasks, "get_llm_client", lambda: FakeClient())

        tasks._generate_dna(source, company)

        prompt = captured_prompt["prompt"]
        assert "=== CLIENT NOTES ===" in prompt
        assert "Note importanti del cliente" in prompt
        assert "Catalogo prodotti" in prompt
        assert "# note-azienda.txt" not in prompt


class TestDNAValidator:
    """PIANO 1.5 Task 1 — deterministic 7-guard validation layer."""

    def _good_dna(self) -> DNAGeneraleSchema:
        """A well-formed DNA that passes all 7 guards."""
        return DNAGeneraleSchema(
            identita=Identita(
                postura="Affianca il cliente con competenza tecnica [SRC:scrape]",
                convinzioni=["La qualita del materiale non e negoziabile [SRC:file:iso.txt]"],
            ),
            modelli_mentali=ModelliMentali(
                pilastri=["Partire sempre dal caso d'uso reale [SRC:note]"],
                sequenza_di_lettura="Prima il caso d'uso, poi i materiali",
            ),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="Lavorazione su misura con controllo qualita [SRC:scrape]",
                trade_off_scelti="Tempi piu lunghi per garantire qualita [SRC:note]",
                famiglie_prodotto=["Serbatoi pressurizzati [SRC:scrape]"],
            ),
            confini=Confini(
                anti_pattern=["Non promettere tempistiche inferiori a 3 settimane [SRC:note]"],
                richieste_rifiutate="Commesse sotto le 5 unita [SRC:note]",
            ),
            tono=Tono(
                registro="Tecnico-accessibile, preciso ma comprensibile",
                esempi=[{"sbagliato": "siamo i migliori", "giusto": "consigliamo il 316 oltre 200 gradi"}],
            ),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="Valutiamo il custom caso per caso, partendo dalla fattibilita tecnica",
                escalation="Coinvolgere un tecnico senior su requisiti non chiari",
            ),
        )

    def test_valid_dna_passes_all_guards(self):
        from apps.companies.dna_validator import validate_dna

        result = validate_dna(self._good_dna())
        assert result.valid is True
        assert result.flags == []
        assert result.guards_passed == 7
        assert result.guards_total == 7
        assert result.safe_mode is False
        assert result.score >= 90

    def test_layer_completeness_critical_when_layer_empty(self):
        """A layer with all fields empty → CRITICAL flag + safe_mode."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.confini = Confini(anti_pattern=[], richieste_rifiutate="")
        result = validate_dna(dna)
        flag = next(f for f in result.flags if f.guard == "layer_completeness")
        assert flag.severity == "CRITICAL"
        assert flag.layer == "confini"
        assert result.safe_mode is True

    def test_echo_chamber_detected(self):
        """DNA with no anti-patterns → cognitive_tension flag (HIGH)."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.confini = Confini(
            anti_pattern=[],
            richieste_rifiutate="Richieste fuori standard rifiutate [SRC:note]",
        )
        result = validate_dna(dna)
        assert "cognitive_tension" in {f.guard for f in result.flags}

    def test_boundary_realism_flagged(self):
        """Rich famiglie_prodotto (>=2) + empty anti_pattern → boundary_realism."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.nucleo_tecnico = NucleoTecnico(
            approccio_distintivo="metodo X [SRC:scrape]",
            trade_off_scelti="t [SRC:note]",
            famiglie_prodotto=["fam A [SRC:scrape]", "fam B [SRC:scrape]", "fam C [SRC:scrape]"],
        )
        dna.confini = Confini(anti_pattern=[], richieste_rifiutate="rifiutiamo X [SRC:note]")
        result = validate_dna(dna)
        assert "boundary_realism" in {f.guard for f in result.flags}

    def test_evidence_grounding_flagged_when_no_sources(self):
        """No [SRC:] markers anywhere → evidence_grounding flag (MEDIUM)."""
        from apps.companies.dna_validator import validate_dna

        dna = DNAGeneraleSchema(
            identita=Identita(postura="Affianca il cliente", convinzioni=["qualita"]),
            modelli_mentali=ModelliMentali(pilastri=["principio"], sequenza_di_lettura="caso d'uso"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="metodo", trade_off_scelti="tempi", famiglie_prodotto=["fam"],
            ),
            confini=Confini(anti_pattern=["non promettiamo"], richieste_rifiutate="sotto soglia"),
            tono=Tono(registro="tecnico-accessibile", esempi=[{"sbagliato": "x", "giusto": "y"}]),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="caso per caso secondo principi tecnici",
                escalation="tecnico senior interviene",
            ),
        )
        result = validate_dna(dna)
        assert "evidence_grounding" in {f.guard for f in result.flags}

    def test_tone_anchoring_flagged(self):
        """Generic registro + no examples → tone_anchoring flag (MEDIUM)."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.tono = Tono(registro="professionale", esempi=[])
        result = validate_dna(dna)
        assert "tone_anchoring" in {f.guard for f in result.flags}

    def test_decisional_depth_flagged(self):
        """Short filosofia + generic escalation → decisional_depth flag (MEDIUM)."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.logica_decisionale = LogicaDecisionale(
            filosofia_custom="breve", escalation="chiedere al superiore",
        )
        result = validate_dna(dna)
        assert "decisional_depth" in {f.guard for f in result.flags}

    def test_identity_coherence_flagged(self):
        """Leading posture + deferential registro → identity_coherence flag (HIGH)."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.identita = Identita(
            postura="Guidiamo il cliente con visione [SRC:scrape]",
            convinzioni=["qualita [SRC:note]"],
        )
        dna.tono = Tono(
            registro="Deferente e ossequiente",
            esempi=[{"sbagliato": "x", "giusto": "y"}],
        )
        result = validate_dna(dna)
        assert "identity_coherence" in {f.guard for f in result.flags}

    def test_safe_mode_caps_score_at_39(self):
        """Any CRITICAL flag → safe_mode=True, score <= 39."""
        from apps.companies.dna_validator import validate_dna

        dna = self._good_dna()
        dna.confini = Confini(anti_pattern=[], richieste_rifiutate="")
        result = validate_dna(dna)
        assert result.safe_mode is True
        assert result.score <= 39

    def test_validator_accepts_dict_input(self):
        """validate_dna must accept a plain dict (as stored in CompanyDNA.content)."""
        from apps.companies.dna_validator import validate_dna

        dna_dict = self._good_dna().model_dump()
        result = validate_dna(dna_dict)
        assert result.valid is True
        assert result.guards_passed == 7

    def test_malformed_dict_triggers_safe_mode(self):
        """A dict that fails schema validation → CRITICAL safe_mode result."""
        from apps.companies.dna_validator import validate_dna

        result = validate_dna({"not": "a valid dna"})
        assert result.safe_mode is True
        assert result.score == 0
        assert result.guards_passed == 0

    def test_score_decreases_with_medium_flags(self):
        """Each flag should reduce the score from the clean baseline."""
        from apps.companies.dna_validator import validate_dna

        good = validate_dna(self._good_dna())
        dna = self._good_dna()
        dna.tono = Tono(registro="professionale", esempi=[])  # tone_anchoring MEDIUM
        flagged = validate_dna(dna)
        assert flagged.score < good.score


class TestDNAScoring:
    """PIANO 1.5 Task 2 — deterministic cognitive scoring (no LLM)."""

    def _rich_dna(self) -> DNAGeneraleSchema:
        """A cognitively rich DNA — high tension, multiple sources, deep layers."""
        return DNAGeneraleSchema(
            identita=Identita(
                postura="Affianca il cliente con competenza tecnica [SRC:scrape]",
                convinzioni=[
                    "La qualita del materiale non e negoziabile [SRC:file:iso.txt]",
                    "Le certificazioni devono essere tracciabili [SRC:note]",
                ],
            ),
            modelli_mentali=ModelliMentali(
                pilastri=["Partire sempre dal caso d'uso reale [SRC:scrape]"],
                sequenza_di_lettura="Prima il caso d'uso, poi i materiali [SRC:note]",
            ),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="Lavorazione su misura con controllo qualita [SRC:scrape]",
                trade_off_scelti="Tempi piu lunghi per garantire qualita [SRC:note]",
                famiglie_prodotto=["Serbatoi pressurizzati [SRC:scrape]"],
            ),
            confini=Confini(
                anti_pattern=["Non promettere tempistiche inferiori a 3 settimane [SRC:note]"],
                richieste_rifiutate="Commesse sotto le 5 unita [SRC:note]",
            ),
            tono=Tono(
                registro="Tecnico-accessibile, preciso ma comprensibile",
                esempi=[{"sbagliato": "siamo i migliori", "giusto": "consigliamo il 316 oltre 200 gradi"}],
            ),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="Valutiamo il custom caso per caso, partendo dalla fattibilita tecnica",
                escalation="Coinvolgere un tecnico senior su requisiti non chiari",
            ),
        )

    def test_score_returns_six_metrics(self):
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna())
        assert 0 <= s.completeness <= 100
        assert 0 <= s.cognitive_tension <= 100
        assert 0 <= s.evidence_density <= 100
        assert 0 <= s.source_diversity <= 100
        assert s.confidence in ("LOW", "MEDIUM", "HIGH")
        assert len(s.reproducibility_hash) == 64  # sha256 hex
        assert 0 <= s.overall <= 100

    def test_completeness_full_when_all_fields_populated(self):
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna())
        assert s.completeness == 100

    def test_completeness_drops_when_fields_empty(self):
        from apps.companies.dna_scoring import score_dna

        dna = self._rich_dna()
        dna.tono = Tono(registro="", esempi=[])  # both tono fields empty
        s = score_dna(dna)
        assert s.completeness < 100

    def test_completeness_uses_layer_weights(self):
        """nucleo_tecnico (weight 1.5) empty should hurt more than tono (0.8)."""
        from apps.companies.dna_scoring import score_dna

        dna_tono = self._rich_dna()
        dna_tono.tono = Tono(registro="", esempi=[])
        dna_nucleo = self._rich_dna()
        dna_nucleo.nucleo_tecnico = NucleoTecnico(
            approccio_distintivo="", trade_off_scelti="", famiglie_prodotto=[],
        )
        score_tono = score_dna(dna_tono).completeness
        score_nucleo = score_dna(dna_nucleo).completeness
        # Clearing the heavily-weighted nucleo_tecnico must hurt more.
        assert score_nucleo < score_tono

    def test_cognitive_tension_high_for_rich_dna(self):
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna())
        # Has anti_pattern + trade_off + esempi + specific convinzioni → >= 80.
        assert s.cognitive_tension >= 80

    def test_cognitive_tension_low_for_brochure_dna(self):
        from apps.companies.dna_scoring import score_dna

        dna = DNAGeneraleSchema(
            identita=Identita(postura="leader del mercato", convinzioni=["qualita"]),
            modelli_mentali=ModelliMentali(pilastri=["eccellenza"], sequenza_di_lettura="innovazione"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="i migliori", trade_off_scelti="",
                famiglie_prodotto=["prodotti premium"],
            ),
            confini=Confini(anti_pattern=[], richieste_rifiutate=""),
            tono=Tono(registro="professionale", esempi=[]),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="siamo i migliori", escalation="",
            ),
        )
        s = score_dna(dna)
        # No anti_pattern, no trade_off, no examples, generic convictions → low.
        assert s.cognitive_tension <= 20

    def test_evidence_density_increases_with_sources(self):
        from apps.companies.dna_scoring import score_dna

        no_src = DNAGeneraleSchema(
            identita=Identita(postura="affianca", convinzioni=["qualita"]),
            modelli_mentali=ModelliMentali(pilastri=["principio"], sequenza_di_lettura="caso"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="metodo", trade_off_scelti="tempi", famiglie_prodotto=["fam"],
            ),
            confini=Confini(anti_pattern=["no promesse"], richieste_rifiutate="sotto soglia"),
            tono=Tono(registro="tecnico", esempi=[{"sbagliato": "x", "giusto": "y"}]),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="caso per caso secondo principi tecnici", escalation="senior",
            ),
        )
        s_empty = score_dna(no_src)
        s_rich = score_dna(self._rich_dna())
        assert s_rich.evidence_density > s_empty.evidence_density

    def test_source_diversity_counts_distinct_types(self):
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna())
        # rich_dna has scrape, file, note → 3 distinct types → high diversity.
        assert s.source_diversity >= 60

    def test_reproducibility_hash_is_deterministic(self):
        """Same DNA → same hash. Different DNA → different hash."""
        from apps.companies.dna_scoring import score_dna

        s1 = score_dna(self._rich_dna())
        s2 = score_dna(self._rich_dna())
        assert s1.reproducibility_hash == s2.reproducibility_hash

        dna2 = self._rich_dna()
        dna2.identita = Identita(
            postura="Cambiamo postura [SRC:scrape]",
            convinzioni=["nuova convinzione [SRC:note]"],
        )
        s3 = score_dna(dna2)
        assert s3.reproducibility_hash != s1.reproducibility_hash

    def test_confidence_thresholds(self):
        from apps.companies.dna_scoring import score_dna

        # Rich DNA → HIGH confidence.
        assert score_dna(self._rich_dna()).confidence == "HIGH"

        # Brochure DNA → LOW confidence.
        brochure = DNAGeneraleSchema(
            identita=Identita(postura="leader", convinzioni=["qualita"]),
            modelli_mentali=ModelliMentali(pilastri=["eccellenza"], sequenza_di_lettura="innovazione"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="migliori", trade_off_scelti="",
                famiglie_prodotto=["premium"],
            ),
            confini=Confini(anti_pattern=[], richieste_rifiutate=""),
            tono=Tono(registro="professionale", esempi=[]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="siamo i migliori", escalation=""),
        )
        assert score_dna(brochure).confidence == "LOW"

    def test_overall_is_weighted_combination(self):
        """Overall must sit within the range of its component metrics."""
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna())
        components = [s.completeness, s.cognitive_tension, s.evidence_density, s.source_diversity]
        assert min(components) <= s.overall <= max(components) + 1

    def test_scoring_accepts_dict_input(self):
        from apps.companies.dna_scoring import score_dna

        s = score_dna(self._rich_dna().model_dump())
        assert s.completeness == 100
        assert len(s.reproducibility_hash) == 64


class TestPromptSourceProtocol:
    """PIANO 1.5 Task 3 — the prompt must instruct the LLM to tag claims
    with [SRC:...] markers, and the mock client must emit them."""

    def test_prompt_template_contains_source_protocol(self):
        """The dna_generale_v1.md prompt must define the [SRC:...] convention."""
        from pathlib import Path

        prompt_path = Path(__file__).resolve().parents[1] / "apps" / "companies" / "prompts" / "dna_generale_v1.md"
        text = prompt_path.read_text(encoding="utf-8")
        # The three canonical source categories must be documented in the prompt.
        assert "[SRC:scrape]" in text
        assert "[SRC:file]" in text
        assert "[SRC:note]" in text

    def test_mock_generate_emits_source_markers(self):
        """MockLLMClient.generate (free-text DNA) must include [SRC:...] markers."""
        client = MockLLMClient()
        result = client.generate("GENERA DNA GENERALE")
        data = json.loads(result.text)
        blob = json.dumps(data, ensure_ascii=False)
        assert "[SRC:" in blob  # at least one marker present
        # Markers must use one of the canonical categories.
        assert "[SRC:scrape]" in blob or "[SRC:file]" in blob or "[SRC:note]" in blob

    def test_mock_generate_structured_emits_source_markers(self):
        """MockLLMClient.generate_structured must return a schema whose values
        carry [SRC:...] markers — so validator/scorer see grounded claims."""
        client = MockLLMClient()
        dna = client.generate_structured(prompt="GENERA DNA GENERALE", response_model=DNAGeneraleSchema)
        from apps.companies.dna_validator import validate_dna
        result = validate_dna(dna)
        # With markers present, evidence_grounding must NOT flag.
        guard_names = {f.guard for f in result.flags}
        assert "evidence_grounding" not in guard_names


class TestEvidenceGrounding:
    """PIANO 1.5 Task 5 — parse [SRC:...] markers and detect source mismatches."""

    def test_extract_sources_from_dna(self):
        """extract_sources must return all [SRC:type] markers found in a DNA."""
        from apps.companies.evidence import extract_sources

        dna = DNAGeneraleSchema(
            identita=Identita(
                postura="Affianca il cliente [SRC:scrape]",
                convinzioni=["qualita [SRC:file:brochure.pdf]", "tempi [SRC:note]"],
            ),
            modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="y"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["n"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        sources = extract_sources(dna)
        # Three distinct source categories should be present.
        categories = {s.category for s in sources}
        assert "scrape" in categories
        assert "file" in categories
        assert "note" in categories
        # The file source should carry its identifier.
        file_refs = [s for s in sources if s.category == "file"]
        assert any(s.ref == "brochure.pdf" for s in file_refs)

    def test_extract_sources_from_dict(self):
        """extract_sources must accept a plain dict (CompanyDNA.content shape)."""
        from apps.companies.evidence import extract_sources

        dna_dict = {
            "identita": {"postura": "x [SRC:scrape]", "convinzioni": ["y [SRC:note]"]},
            "modelli_mentali": {"pilastri": ["z"], "sequenza_di_lettura": "w"},
            "nucleo_tecnico": {"approccio_distintivo": "a", "trade_off_scelti": "t", "famiglie_prodotto": ["f"]},
            "confini": {"anti_pattern": ["n"], "richieste_rifiutate": "r"},
            "tono": {"registro": "r", "esempi": [{"sbagliato": "s", "giusto": "g"}]},
            "logica_decisionale": {"filosofia_custom": "c", "escalation": "e"},
        }
        sources = extract_sources(dna_dict)
        assert len(sources) >= 2
        assert any(s.category == "scrape" for s in sources)

    def test_extract_sources_empty_when_no_markers(self):
        """A DNA with no [SRC:] markers yields an empty source list."""
        from apps.companies.evidence import extract_sources

        dna = DNAGeneraleSchema(
            identita=Identita(postura="no sources here", convinzioni=["none"]),
            modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="y"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["n"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        assert extract_sources(dna) == []

    def test_source_mismatch_detected_when_ref_not_in_available(self):
        """A [SRC:file:name] referencing a file not in the available set is a mismatch."""
        from apps.companies.evidence import check_source_consistency, SourceMismatch

        dna = DNAGeneraleSchema(
            identita=Identita(
                postura="x [SRC:scrape]",
                convinzioni=["claim grounded in un file inesistente [SRC:file:fantasma.pdf]"],
            ),
            modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="y"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["n"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        # Available sources: scrape + note present, but no files at all.
        available = {"scrape": True, "note": True, "files": []}
        result = check_source_consistency(dna, available)
        assert result.has_mismatch is True
        assert any(
            isinstance(m, SourceMismatch) and "fantasma.pdf" in m.detail
            for m in result.mismatches
        )

    def test_source_consistency_ok_when_refs_match(self):
        """No mismatch when all [SRC:file:name] reference real available files."""
        from apps.companies.evidence import check_source_consistency

        dna = DNAGeneraleSchema(
            identita=Identita(
                postura="x [SRC:scrape]",
                convinzioni=["claim da un file reale [SRC:file:catalogo.pdf]"],
            ),
            modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="y"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a [SRC:scrape]", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["n [SRC:note]"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        available = {
            "scrape": True, "note": True,
            "files": ["catalogo.pdf", "altro.docx"],
        }
        result = check_source_consistency(dna, available)
        assert result.has_mismatch is False
        assert result.mismatches == []

    def test_source_mismatch_when_category_unavailable(self):
        """[SRC:note] present but notes were not provided is a mismatch."""
        from apps.companies.evidence import check_source_consistency

        dna = DNAGeneraleSchema(
            identita=Identita(postura="x [SRC:scrape]", convinzioni=["da nota [SRC:note]"]),
            modelli_mentali=ModelliMentali(pilastri=["x"], sequenza_di_lettura="y"),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="a", trade_off_scelti="t", famiglie_prodotto=["f"],
            ),
            confini=Confini(anti_pattern=["n"], richieste_rifiutate="r"),
            tono=Tono(registro="r", esempi=[{"sbagliato": "s", "giusto": "g"}]),
            logica_decisionale=LogicaDecisionale(filosofia_custom="c", escalation="e"),
        )
        available = {"scrape": True, "note": False, "files": []}
        result = check_source_consistency(dna, available)
        assert result.has_mismatch is True


class TestSelfCritiqueLoop:
    """PIANO 1.5 Task 4 — Delphi-inspired 2-pass self-critique loop.

    Pass 1: cross-layer challenge (Conflict Matrix checks coherence).
    Pass 2: refinement (reformulates layers flagged as TENSION/CONFLICT).
    A single LLM reviews its own output — NOT multi-agent.
    """

    def _good_dna(self) -> DNAGeneraleSchema:
        return DNAGeneraleSchema(
            identita=Identita(
                postura="Affianca il cliente [SRC:scrape]",
                convinzioni=["qualita certificata [SRC:file]"],
            ),
            modelli_mentali=ModelliMentali(
                pilastri=["principio [SRC:scrape]"], sequenza_di_lettura="caso d'uso [SRC:note]",
            ),
            nucleo_tecnico=NucleoTecnico(
                approccio_distintivo="metodo [SRC:scrape]", trade_off_scelti="tempi [SRC:note]",
                famiglie_prodotto=["fam [SRC:scrape]"],
            ),
            confini=Confini(
                anti_pattern=["no promesse [SRC:note]"], richieste_rifiutate="sotto soglia [SRC:note]",
            ),
            tono=Tono(
                registro="tecnico-accessibile", esempi=[{"sbagliato": "x", "giusto": "y"}],
            ),
            logica_decisionale=LogicaDecisionale(
                filosofia_custom="caso per caso secondo principi tecnici",
                escalation="tecnico senior interviene",
            ),
        )

    def test_cross_layer_check_returns_five_checks(self):
        """The Conflict Matrix always produces exactly 5 cross-layer checks."""
        from apps.companies.dna_critique import run_cross_layer_check

        client = MockLLMClient()
        checks = run_cross_layer_check(self._good_dna(), client)
        assert len(checks) == 5
        # Each check carries a checker, target, status, and note.
        for check in checks:
            assert check.checker in {"confini", "logica_decisionale", "tono", "nucleo_tecnico", "identita"}
            assert check.target in {"nucleo_tecnico", "identita", "confini", "modelli_mentali", "tono"}
            assert check.status in {"OK", "TENSION", "CONFLICT"}

    def test_self_critique_returns_dna_when_no_tension(self):
        """If all cross-layer checks are OK, the DNA is returned unchanged."""
        from apps.companies.dna_critique import self_critique_dna, CrossLayerCheck
        from apps.companies.dna_schemas import DNAGeneraleSchema as Schema

        # A mock client whose cross-layer check returns all OK.
        ok_checks = [
            CrossLayerCheck(checker="confini", target="nucleo_tecnico", status="OK", note=""),
            CrossLayerCheck(checker="logica_decisionale", target="identita", status="OK", note=""),
            CrossLayerCheck(checker="tono", target="confini", status="OK", note=""),
            CrossLayerCheck(checker="nucleo_tecnico", target="modelli_mentali", status="OK", note=""),
            CrossLayerCheck(checker="identita", target="tono", status="OK", note=""),
        ]

        class AllOkClient:
            def generate_structured(self, prompt, response_model, model=None):
                return ok_checks

        dna = self._good_dna()
        result, report = self_critique_dna(dna, AllOkClient())
        assert isinstance(result, DNAGeneraleSchema)
        # No refinement happened: same content.
        assert result.model_dump() == dna.model_dump()
        assert report.refined is False
        assert len(report.checks) == 5

    def test_self_critique_refines_on_tension(self):
        """If any check is TENSION/CONFLICT, the loop refines the DNA."""
        from apps.companies.dna_critique import self_critique_dna, CrossLayerCheck

        tension_checks = [
            CrossLayerCheck(checker="confini", target="nucleo_tecnico", status="OK", note=""),
            CrossLayerCheck(checker="logica_decisionale", target="identita", status="TENSION", note="postura vs decisione"),
            CrossLayerCheck(checker="tono", target="confini", status="OK", note=""),
            CrossLayerCheck(checker="nucleo_tecnico", target="modelli_mentali", status="OK", note=""),
            CrossLayerCheck(checker="identita", target="tono", status="OK", note=""),
        ]

        class TensionClient:
            def __init__(self):
                self.calls = 0
            def generate_structured(self, prompt, response_model, model=None):
                self.calls += 1
                if self.calls == 1:
                    return tension_checks
                # Second call: return a refined DNA schema.
                return MockLLMClient().generate_structured(prompt, response_model, model)

        client = TensionClient()
        result, report = self_critique_dna(self._good_dna(), client)
        assert report.refined is True
        assert client.calls == 2  # challenge + refine
        assert isinstance(result, DNAGeneraleSchema)

    def test_conflict_matrix_covers_all_pairs(self):
        """The 5 Conflict Matrix pairs must cover every layer at least once."""
        from apps.companies.dna_critique import CONFLICT_MATRIX

        layers_involved = set()
        for checker, target, _desc in CONFLICT_MATRIX:
            layers_involved.add(checker)
            layers_involved.add(target)
        assert layers_involved == set(LAYER_KEYS)

    def test_self_critique_report_carries_checks(self):
        """The critique report must expose the cross-layer checks for audit."""
        from apps.companies.dna_critique import self_critique_dna, CritiqueReport

        client = MockLLMClient()
        _, report = self_critique_dna(self._good_dna(), client)
        assert isinstance(report, CritiqueReport)
        assert len(report.checks) == 5
        assert hasattr(report, "refined")


class TestAuditHashChain:
    """PIANO 1.5 Task 6 — HMAC-SHA256 audit chain over DNA versions.

    Each version's hash includes the previous version's hash, so any
    retroactive edit breaks the chain (tamper-evident).
    """

    def test_hash_is_deterministic(self):
        """Same payload + same previous hash → same audit hash."""
        from apps.companies.audit import compute_audit_hash

        payload = {"identita": {"postura": "x"}, "nucleo_tecnico": {"fam": ["a"]}}
        h1 = compute_audit_hash(payload, previous_hash="")
        h2 = compute_audit_hash(payload, previous_hash="")
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_hash_changes_with_payload(self):
        """Different payload → different hash."""
        from apps.companies.audit import compute_audit_hash

        a = compute_audit_hash({"x": 1}, previous_hash="")
        b = compute_audit_hash({"x": 2}, previous_hash="")
        assert a != b

    def test_hash_changes_with_previous_hash(self):
        """Different previous_hash → different hash (chain linking)."""
        from apps.companies.audit import compute_audit_hash

        payload = {"x": 1}
        standalone = compute_audit_hash(payload, previous_hash="")
        chained = compute_audit_hash(payload, previous_hash="abc123")
        assert standalone != chained

    def test_hash_independent_of_key_order(self):
        """JSON key ordering must not change the hash (sort_keys)."""
        from apps.companies.audit import compute_audit_hash

        a = compute_audit_hash({"a": 1, "b": 2}, previous_hash="")
        b = compute_audit_hash({"b": 2, "a": 1}, previous_hash="")
        assert a == b

    def test_verify_accepts_correct_hash(self):
        """verify_audit_hash returns True for the matching hash."""
        from apps.companies.audit import compute_audit_hash, verify_audit_hash

        payload = {"identita": {"postura": "affianca"}}
        h = compute_audit_hash(payload, previous_hash="")
        assert verify_audit_hash(h, payload, previous_hash="") is True

    def test_verify_rejects_tampered_payload(self):
        """A modified payload must fail verification (tamper detection)."""
        from apps.companies.audit import compute_audit_hash, verify_audit_hash

        original = {"identita": {"postura": "affianca"}}
        h = compute_audit_hash(original, previous_hash="")
        tampered = {"identita": {"postura": "leader"}}
        assert verify_audit_hash(h, tampered, previous_hash="") is False

    def test_verify_rejects_wrong_previous_hash(self):
        """A different previous_hash must fail verification."""
        from apps.companies.audit import compute_audit_hash, verify_audit_hash

        payload = {"x": 1}
        h = compute_audit_hash(payload, previous_hash="real_prev")
        assert verify_audit_hash(h, payload, previous_hash="fake_prev") is False

    def test_chain_links_pre_to_complete(self):
        """Complete DNA hash must differ when it includes the pre-DNA hash."""
        from apps.companies.audit import compute_audit_hash

        payload = {"identita": {"postura": "affianca"}}
        pre_hash = compute_audit_hash(payload, previous_hash="")
        # Complete DNA links to the pre-DNA hash.
        complete_payload = {**payload, "confini": {"anti_pattern": ["x"]}}
        unlinked = compute_audit_hash(complete_payload, previous_hash="")
        linked = compute_audit_hash(complete_payload, previous_hash=pre_hash)
        assert unlinked != linked  # chain linkage changes the hash
