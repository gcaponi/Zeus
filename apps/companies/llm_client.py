"""LLM client wrapper — raw OpenAI SDK, no Langchain."""

import json
import logging
import os
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
    "deepseek-chat": {"input": 0.14 / 1_000_000, "output": 0.28 / 1_000_000},
    "deepseek-v4-flash": {"input": 0.14 / 1_000_000, "output": 0.28 / 1_000_000},
    "deepseek-v4-pro": {"input": 2.24 / 1_000_000, "output": 2.24 / 1_000_000},
}


class LLMResult:
    def __init__(
        self, text: str, tokens_in: int, tokens_out: int,
        cost: float, latency_ms: int = 0,
    ):
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost = cost
        self.latency_ms = latency_ms


class LLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, model: str | None = None) -> LLMResult:
        ...

    @abstractmethod
    def generate_structured(self, prompt: str, response_model, model: str | None = None):
        ...


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str = LLM_API_KEY, base_url: str = LLM_BASE_URL):
        if not api_key:
            raise RuntimeError("LLM_API_KEY not set")
        from openai import OpenAI

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def generate(self, prompt: str, model: str | None = None) -> LLMResult:
        model = model or LLM_MODEL
        t0 = time.time()
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort="high",
        )
        latency = int((time.time() - t0) * 1000)
        usage = resp.usage
        tokens_in = usage.prompt_tokens
        tokens_out = usage.completion_tokens
        text = resp.choices[0].message.content or ""
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
        cost = tokens_in * pricing["input"] + tokens_out * pricing["output"]
        return LLMResult(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out,
            cost=round(cost, 6), latency_ms=latency,
        )

    def generate_structured(self, prompt: str, response_model, model: str | None = None):
        import instructor

        model = model or LLM_MODEL
        structured_client = instructor.from_openai(self._client)
        instance = structured_client.chat.completions.create(
            model=model,
            response_model=response_model,
            max_retries=2,
            messages=[{"role": "user", "content": prompt}],
        )
        return instance


class MockLLMClient(LLMClient):
    def generate(self, prompt: str, model: str | None = None) -> LLMResult:
        if "RISCRIVI_SEZIONE_" in prompt and "RIFORMULA_DNA_CON_RISPOSTE" in prompt:
            section_key = self._extract_section_key(prompt, "RISCRIVI_SEZIONE_")
            if section_key in ("pilastri", "valore"):
                return LLMResult(
                    text=json.dumps([
                        f"Pilastro {section_key} 1 riformulato",
                        f"Pilastro {section_key} 2 riformulato",
                        f"Pilastro {section_key} 3 riformulato",
                    ], ensure_ascii=False),
                    tokens_in=400,
                    tokens_out=120,
                    cost=0.0001,
                    latency_ms=600,
                )
            return LLMResult(
                text=json.dumps(
                    f"Sezione {section_key} riformulata integrando le risposte "
                    "del cliente nel testo finale, senza elenco domanda-risposta.",
                    ensure_ascii=False,
                ),
                tokens_in=400,
                tokens_out=150,
                cost=0.0001,
                latency_ms=600,
            )

        if "RISCRIVI_SEZIONE_" in prompt and "RIFORMULA_PRODUCT_DNA_CON_RISPOSTE" in prompt:
            section_key = self._extract_section_key(prompt, "RISCRIVI_SEZIONE_")
            if section_key == "valore":
                return LLMResult(
                    text=json.dumps([
                        "Valore prodotto 1 riformulato",
                        "Valore prodotto 2 riformulato",
                        "Valore prodotto 3 riformulato",
                    ], ensure_ascii=False),
                    tokens_in=400,
                    tokens_out=120,
                    cost=0.0001,
                    latency_ms=600,
                )
            return LLMResult(
                text=json.dumps(
                    f"Sezione prodotto {section_key} riformulata integrando le risposte "
                    "del cliente nel testo finale, senza elenco domanda-risposta.",
                    ensure_ascii=False,
                ),
                tokens_in=400,
                tokens_out=150,
                cost=0.0001,
                latency_ms=600,
            )

        if "GENERA_DOMANDE_A1_A20" in prompt:
            plan_slug = "starter"
            answer_depth = "generica"
            if "PIANO: professional" in prompt:
                plan_slug = "professional"
                answer_depth = "mirata"
            elif "PIANO: enterprise" in prompt:
                plan_slug = "enterprise"
                answer_depth = "analitica"
            guidance = {
                "starter": "Risposta sintetica utile a completare un DNA base di almeno 2 pagine.",
                "professional": "Risposta completa, specifica e basata su quanto emerso dai dati.",
                "enterprise": (
                    "Risposta analitica sulla mentalita aziendale e sui criteri decisionali."
                ),
            }[plan_slug]
            anchor = "pre-DNA"
            if "ISO 9001" in prompt:
                anchor = "ISO 9001"
            if plan_slug == "enterprise":
                anchor = "mentalita aziendale"
            questions = []
            sections = ["chi_siamo", "mission", "pilastri", "mercato", "settore"]
            for index in range(10):
                code = f"A{index + 1}"
                questions.append({
                    "code": code,
                    "section_key": sections[index % len(sections)],
                    "principle": f"Principio {code}",
                    "question": (
                        f"Domanda {code} per piano {plan_slug}: chiarisci {anchor} "
                        "partendo dal pre-DNA generato."
                    ),
                    "answer_depth": answer_depth,
                    "answer_guidance": guidance,
                })
            return LLMResult(
                text=json.dumps({"questions": questions}, ensure_ascii=False),
                tokens_in=500,
                tokens_out=300,
                cost=0.0002,
                latency_ms=900,
            )

        return LLMResult(
            text=(
                '{"chi_siamo": "Rossi Metalli SRL opera da 40 anni nel settore siderurgico '
                'con produzione di acciai speciali per edilizia e industria.", '
                '"mission": "Fornire acciai di alta qualità con tempi di consegna ridotti.", '
                '"settore": "Siderurgia — produzione e distribuzione di acciai speciali.", '
                '"mercato": "Edilizia residenziale, grandi infrastrutture, industria meccanica.", '
                '"pilastri": ["Qualità del materiale certificata", '
                '"Consegne rapide e puntuali", '
                '"Personalizzazione dei profili", '
                '"Assistenza tecnica pre e post vendita"]}'
            ),
            tokens_in=350,
            tokens_out=180,
            cost=0.0001,
            latency_ms=1200,
        )

    def generate_structured(self, prompt: str, response_model, model: str | None = None):
        """Mock structured generation — returns a valid DNAGeneraleSchema instance."""
        from apps.companies.dna_schemas import (
            DNAGeneraleSchema,
            Identita,
            ModelliMentali,
            NucleoTecnico,
            Confini,
            Tono,
            LogicaDecisionale,
        )

        if response_model is DNAGeneraleSchema:
            return DNAGeneraleSchema(
                identita=Identita(
                    postura="Affianca il cliente con competenza tecnica",
                    convinzioni=[
                        "La qualita del materiale non e negoziabile",
                        "Le consegne si rispettano sempre",
                    ],
                ),
                modelli_mentali=ModelliMentali(
                    pilastri=[
                        "Partire sempre dal caso d'uso reale",
                        "Documentare ogni scelta tecnica",
                    ],
                    sequenza_di_lettura=(
                        "Prima il caso d'uso, poi i materiali, infine i vincoli produttivi"
                    ),
                ),
                nucleo_tecnico=NucleoTecnico(
                    approccio_distintivo=(
                        "Lavorazione su misura con controllo qualita integrato"
                    ),
                    trade_off_scelti=(
                        "Tempi leggermente piu lunghi per garantire qualita"
                    ),
                    famiglie_prodotto=[
                        "Serbatoi pressurizzati — recipienti per uso industriale",
                        "Componenti per oleodinamica — valvole e raccordi",
                    ],
                ),
                confini=Confini(
                    anti_pattern=[
                        "Non promettere tempistiche inferiori a 3 settimane",
                        "Non accettare commesse senza specifiche tecniche",
                    ],
                    richieste_rifiutate=(
                        "Commesse sotto le 5 unita — non sostenibili economicamente"
                    ),
                ),
                tono=Tono(
                    registro="Tecnico-accessibile, preciso ma comprensibile",
                    esempi=[
                        {
                            "sbagliato": "I nostri prodotti sono i migliori del mercato",
                            "giusto": (
                                "Per applicazioni sotto i 200 gradi consigliamo il 304; "
                                "oltre, il 316"
                            ),
                        }
                    ],
                ),
                logica_decisionale=LogicaDecisionale(
                    filosofia_custom=(
                        "Valutiamo il custom caso per caso, partendo dalla fattibilita tecnica"
                    ),
                    escalation=(
                        "Quando il problema oltrepassa il nostro dominio produttivo, "
                        "segnaliamo e indirizziamo"
                    ),
                ),
            )
        # Generic fallback: instantiate with empty defaults if possible
        try:
            return response_model()
        except Exception:
            return response_model.model_validate({})

    @staticmethod
    def _extract_section_key(prompt: str, prefix: str) -> str:
        idx = prompt.find(prefix)
        if idx < 0:
            return ""
        rest = prompt[idx + len(prefix):]
        end = len(rest)
        for i, ch in enumerate(rest):
            if not (ch.isalnum() or ch == "_"):
                end = i
                break
        return rest[:end].lower()


def get_llm_client() -> LLMClient:
    if LLM_API_KEY:
        return OpenAIClient(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or "")
    logger.info("LLM_API_KEY not set, using mock LLM client")
    return MockLLMClient()
