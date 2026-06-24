"""Tests for the ZEUS Fondamenta plan: 6-layer DNA schema + Instructor integration."""
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
