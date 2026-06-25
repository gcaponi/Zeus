"""LLM client wrapper — raw OpenAI SDK, no Langchain."""

import json
import logging
import os
import time
from abc import ABC, abstractmethod

from apps.companies.dna_schemas import LAYER_KEYS

logger = logging.getLogger(__name__)

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_MODEL_PRO = os.environ.get("LLM_MODEL_PRO", "deepseek-v4-pro")

MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
    "deepseek-chat": {"input": 0.14 / 1_000_000, "output": 0.28 / 1_000_000},
    "deepseek-v4-flash": {"input": 0.14 / 1_000_000, "output": 0.28 / 1_000_000},
    "deepseek-v4-pro": {"input": 2.24 / 1_000_000, "output": 2.24 / 1_000_000},
}

ZEUS_SYSTEM_PROMPT = """Sei ZEUS, un filosofo tecnico specializzato nel settore manifatturiero.

Non sei un copywriter aziendale. Non sei un analista marketing. Sei un pensatore che \
costruisce sistemi cognitivi: documenti che insegnano a un tecnico AI COME RAGIONARE \
su un'azienda e sui suoi prodotti.

I tuoi principi fondamentali:

1. INTERPRETI, NON TRANSCRIVI. I fatti grezzi sono evidenza, non sono il DNA. \
Trasformi i dati in principi, postura, confini, logica decisionale.

2. NON ASSOLUTIZZI. Mai usare parole come "garantisce", "certezza assoluta", \
"risolve definitivamente". Ogni affermazione ha un confine di validità.

3. DUBITI ESPLICITI. Se l'evidenza è ambigua, incompleta o contraddittoria, \
scrivi "Da chiarire in intervista: ..." invece di inventare.

4. PENSI IN PRINCIPI, NON IN DATI. Numeri, percentuali, KPI sono evidenza \
grezza. Il DNA trasforma i numeri nel principio che rivelano.

5. COSTRUISCI ARCHITETTURA COGNITIVA. Il DNA non è un profilo aziendale. \
È il sistema operativo che un tecnico AI userà per interpretare problemi \
futuri. Ogni sezione deve servire a questo scopo.

6. RISPETTI LA GERARCHIA DELLE FONTI. DNA Generale > DNA Famiglia Prodotto > \
Fonti tecniche vincolanti. Il generale dà principi trasversali, il prodotto \
dà comportamento specifico, le fonti tecniche danno il dato reale vincolante.

7. LE RISPOSTE DEL CLIENTE SONO VINCOLANTI. Se il cliente chiarisce un punto, \
chiudi il dubbio. Non mantenere ambiguità precedente. Non riscrivere "da chiarire" \
se il cliente ha già risposto.

LINGUA: sempre italiano tecnico. Anche se le fonti sono in inglese, traduci e \
riscrivi. Nessuna parola in inglese nell'output."""


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
    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> LLMResult:
        ...

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ):
        ...


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str = LLM_API_KEY, base_url: str = LLM_BASE_URL):
        if not api_key:
            raise RuntimeError("LLM_API_KEY not set")
        from openai import OpenAI

        kwargs = {"api_key": api_key, "timeout": 300.0}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> LLMResult:
        model = model or LLM_MODEL
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        t0 = time.time()
        resp = self._client.chat.completions.create(**kwargs)
        latency = int((time.time() - t0) * 1000)
        usage = resp.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        text = resp.choices[0].message.content or ""
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
        cost = tokens_in * pricing["input"] + tokens_out * pricing["output"]
        return LLMResult(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out,
            cost=round(cost, 6), latency_ms=latency,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ):
        import instructor

        model = model or LLM_MODEL
        structured_client = instructor.from_openai(self._client)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": model,
            "response_model": response_model,
            "max_retries": 2,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        instance = structured_client.chat.completions.create(**kwargs)
        return instance


class MockLLMClient(LLMClient):
    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> LLMResult:
        if "RISCRIVI_SEZIONE_" in prompt and "RIFORMULA_DNA_CON_RISPOSTE" in prompt:
            section_key = self._extract_section_key(prompt, "RISCRIVI_SEZIONE_")
            if section_key in ("modelli_mentali", "valore"):
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

        if "SINTESI_GLOBALE_DNA" in prompt:
            return LLMResult(
                text=json.dumps({
                    "identita": {
                        "postura": "Postura sintetizzata globalmente [SRC:scrape] [SRC:answer]",
                        "convinzioni": [
                            "Convinzione 1 dalla sintesi [SRC:answer]",
                            "Convinzione 2 dalla sintesi [SRC:note]",
                        ],
                    },
                    "modelli_mentali": {
                        "pilastri": [
                            "Pilastro sintetizzato 1 [SRC:scrape]",
                            "Pilastro sintetizzato 2 [SRC:answer]",
                        ],
                        "sequenza_di_lettura": "Sequenza sintetizzata globalmente [SRC:scrape]",
                    },
                    "nucleo_tecnico": {
                        "approccio_distintivo": "Approccio sintetizzato [SRC:scrape] [SRC:answer]",
                        "trade_off_scelti": "Trade-off sintetizzato [SRC:note]",
                        "famiglie_prodotto": ["Famiglia sintetizzata [SRC:scrape]"],
                    },
                    "confini": {
                        "anti_pattern": ["Anti-pattern sintetizzato [SRC:answer]"],
                        "richieste_rifiutate": "Rifiuto sintetizzato [SRC:note]",
                    },
                    "tono": {
                        "registro": "Registro sintetizzato [SRC:scrape]",
                        "esempi": [{"sbagliato": "x", "giusto": "y [SRC:note]"}],
                    },
                    "logica_decisionale": {
                        "filosofia_custom": "Filosofia custom sintetizzata [SRC:answer]",
                        "escalation": "Escalation sintetizzata [SRC:scrape]",
                    },
                    "sintesi_cognitiva": (
                        "Sintesi cognitiva globale che integra il pre-DNA con le risposte "
                        "del cliente in un documento coerente e conclusivo."
                    ),
                }, ensure_ascii=False),
                tokens_in=800,
                tokens_out=600,
                cost=0.001,
                latency_ms=1200,
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
            sections = list(LAYER_KEYS)
            for index in range(10):
                code = f"A{index + 1}"
                questions.append({
                    "code": code,
                    "pool": "template" if index < 5 else "kb_anchored",
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
            text=json.dumps({
                "identita": {
                    "postura": "Rossi Metalli affianca aziende tecniche affidabili [SRC:scrape]",
                    "convinzioni": ["qualita certificata [SRC:file]", "tempi chiari [SRC:note]"],
                },
                "modelli_mentali": {
                    "pilastri": ["qualita del materiale [SRC:scrape]", "consegne puntuali [SRC:note]"],
                    "sequenza_di_lettura": (
                        "parte dall'uso finale e risale a materiale, lavorazione e consegna [SRC:scrape]"
                    ),
                },
                "nucleo_tecnico": {
                    "approccio_distintivo": "acciai speciali per edilizia e industria [SRC:scrape]",
                    "trade_off_scelti": "personalizzazione tecnica con controllo tempi [SRC:note]",
                    "famiglie_prodotto": ["profili speciali [SRC:scrape]", "acciai certificati [SRC:file]"],
                },
                "confini": {
                    "anti_pattern": ["promesse non verificabili [SRC:note]", "assenza di certificazione [SRC:file]"],
                    "richieste_rifiutate": "richieste incompatibili con qualita e tracciabilita [SRC:note]",
                },
                "tono": {
                    "registro": "tecnico-accessibile [SRC:scrape]",
                    "esempi": [
                        {
                            "sbagliato": "consigliamo tutto cio che ci chiede",
                            "giusto": "consigliamo questa soluzione per vincoli certificati [SRC:note]",
                        },
                    ],
                },
                "logica_decisionale": {
                    "filosofia_custom": "il fuori standard deve produrre valore tecnico reale [SRC:note]",
                    "escalation": "coinvolgere un tecnico senior su requisiti non chiari [SRC:scrape]",
                },
                "sintesi_cognitiva": (
                    "Rossi Metalli e un'azienda che interpreta il metallo come materia "
                    "cognitiva, non solo come prodotto. La sua identita si fonda sulla "
                    "certezza del materiale e sulla chiarezza dei tempi."
                ),
            }, ensure_ascii=False),
            tokens_in=500,
            tokens_out=400,
            cost=0.0003,
            latency_ms=800,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model,
        *,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ):
        if response_model.__name__ == "DNAGeneraleSchema":
            from apps.companies.dna_schemas import (
                Confini,
                DNAGeneraleSchema,
                Identita,
                LogicaDecisionale,
                ModelliMentali,
                NucleoTecnico,
                Tono,
            )
            return DNAGeneraleSchema(
                identita=Identita(
                    postura="Affianca il cliente con competenza tecnica [SRC:scrape]",
                    convinzioni=[
                        "La qualita del materiale non e negoziabile [SRC:file]",
                        "Le consegne si rispettano sempre [SRC:note]",
                    ],
                ),
                modelli_mentali=ModelliMentali(
                    pilastri=[
                        "Partire sempre dal caso d'uso reale [SRC:scrape]",
                        "Documentare ogni scelta tecnica [SRC:file]",
                    ],
                    sequenza_di_lettura=(
                        "Prima il caso d'uso, poi i materiali, infine i vincoli produttivi [SRC:note]"
                    ),
                ),
                nucleo_tecnico=NucleoTecnico(
                    approccio_distintivo="Lavorazione su misura con controllo qualita integrato [SRC:scrape]",
                    trade_off_scelti="Tempi leggermente piu lunghi per garantire qualita [SRC:note]",
                    famiglie_prodotto=[
                        "Serbatoi pressurizzati — recipienti per uso industriale [SRC:scrape]",
                        "Componenti per oleodinamica — valvole e raccordi [SRC:file]",
                    ],
                ),
                confini=Confini(
                    anti_pattern=[
                        "Non promettere tempistiche inferiori a 3 settimane [SRC:note]",
                        "Non accettare commesse senza specifiche tecniche [SRC:file]",
                    ],
                    richieste_rifiutate="Commesse sotto le 5 unita — non sostenibili economicamente [SRC:note]",
                ),
                tono=Tono(
                    registro="Tecnico-accessibile, preciso ma comprensibile [SRC:scrape]",
                    esempi=[
                        {
                            "sbagliato": "facciamo di tutto per il cliente",
                            "giusto": "consigliamo soluzioni tecnicamente validate; "
                                     "oltre, il 316 [SRC:note]",
                        },
                    ],
                ),
                logica_decisionale=LogicaDecisionale(
                    filosofia_custom="Valutiamo il custom caso per caso, partendo dalla fattibilita tecnica [SRC:note]",
                    escalation="Quando il problema oltrepassa il nostro dominio produttivo, segnaliamo e indirizziamo [SRC:scrape]",
                ),
            )
        # Self-critique cross-layer check — return 5 Conflict Matrix checks (all OK).
        from apps.companies.dna_critique import CrossLayerCheckResult, CrossLayerCheckItem
        if response_model is CrossLayerCheckResult:
            return CrossLayerCheckResult(checks=[
                CrossLayerCheckItem(checker="confini", target="nucleo_tecnico", status="OK", note=""),
                CrossLayerCheckItem(checker="logica_decisionale", target="identita", status="OK", note=""),
                CrossLayerCheckItem(checker="tono", target="confini", status="OK", note=""),
                CrossLayerCheckItem(checker="nucleo_tecnico", target="modelli_mentali", status="OK", note=""),
                CrossLayerCheckItem(checker="identita", target="tono", status="OK", note=""),
            ])

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
