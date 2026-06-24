"""Tests for the ZEUS Fondamenta plan: 6-layer DNA schema + Instructor integration."""
import pytest
from apps.companies.dna_schemas import (
    DNAGeneraleSchema,
    Identita,
    ModelliMentali,
    NucleoTecnico,
    Confini,
    Tono,
    LogicaDecisionale,
    LAYER_KEYS,
)


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
        with pytest.raises(Exception):
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


from apps.companies.llm_client import MockLLMClient


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
