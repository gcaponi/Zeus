"""LLM client wrapper — raw OpenAI SDK, no Langchain."""

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from apps.companies.dna_schemas import LAYER_KEYS, PRODUCT_LAYER_KEYS

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


def _parse_llm_json(text: str, context: str = "") -> Any:
    """Parse LLM JSON output with progressively more lenient strategies.

    1. Direct json.loads
    2. Extract from ```json ... ``` fenced blocks
    3. Find the first balanced { ... } substring

    Raises ValueError if every strategy fails. Never returns a silent
    {"raw": ...} fallback — callers must decide how to handle failure.
    """
    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. Fenced code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 3. First balanced object
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(
        f"LLM JSON parse failed{f' [{context}]' if context else ''}: {text[:200]}"
    )


def _generate_with_retry(
    client,
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    temperatures=(0.7, 0.4, 0.2),
    parse: Callable[[str], Any] | None = None,
    context: str = "",
):
    """Generate + parse JSON, retrying with lower temperature on parse failure.

    Only retries on JSON parse failure (ValueError), not on HTTP/timeout
    errors (those surface to the caller). Returns (LLMResult, parsed).

    `parse` defaults to a context-bound _parse_llm_json (expects a JSON
    object). Pass a custom callable `parse(text) -> content` that raises
    ValueError on failure to validate structured output (e.g. question count).
    """
    if parse is None:
        def parse(t):
            payload = _parse_llm_json(t, context=context)
            if not isinstance(payload, dict):
                raise ValueError(f"LLM JSON response must be an object [{context}]")
            return payload
    last_exc: ValueError | None = None
    for attempt, temp in enumerate(temperatures, start=1):
        result = client.generate(
            prompt,
            model=model,
            temperature=temp,
            system_prompt=system_prompt,
        )
        try:
            return result, parse(result.text)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "LLM JSON parse failed [%s] attempt %d/%d (temp=%.1f), retrying",
                context, attempt, len(temperatures), temp,
            )
    raise RuntimeError(
        f"LLM generation failed after {len(temperatures)} attempts [{context}]"
    ) from last_exc


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

8. FORMATO OUTPUT QUANDO È RICHIESTO JSON. Quando ti viene chiesto un JSON, il \
tuo output inizia con { e finisce con }. Nessun testo prima o dopo. Nessun \
preambolo ("Ecco il DNA:", "Certamente"), nessuna spiegazione finale, nessun \
blocco markdown, nessun ```json. Il primo carattere deve essere { e l'ultimo }.

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

        if "GAP_ENGINE_EVAL" in prompt:
            return LLMResult(
                text=json.dumps({
                    "evaluations": [],
                    "overall_sufficient": True,
                    "follow_ups": [],
                }, ensure_ascii=False),
                tokens_in=600,
                tokens_out=150,
                cost=0.0002,
                latency_ms=700,
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

        if "GENERA_DOMANDE_D1_D20" in prompt:
            plan_slug = "starter"
            answer_depth = "generica"
            if "PIANO: professional" in prompt:
                plan_slug = "professional"
                answer_depth = "mirata"
            elif "PIANO: enterprise" in prompt:
                plan_slug = "enterprise"
                answer_depth = "analitica"
            guidance = {
                "starter": "Risposta sintetica sulla famiglia prodotto.",
                "professional": "Risposta completa e specifica della famiglia.",
                "enterprise": "Risposta analitica sulla logica della famiglia prodotto.",
            }[plan_slug]
            questions = []
            sections = list(PRODUCT_LAYER_KEYS)
            for index in range(10):
                code = f"D{index + 1}"
                if index < 5:
                    pool = "template"
                    combinational = [
                        f"Domanda {code}: il vincolo di temperatura nello specialista e coerente con i confini operativi dichiarati nel DNA Generale? Se diverge, in quale direzione?",
                        f"Domanda {code}: la configurazione custom descritta rispecchia la logica decisionale aziendale sul 'fuori standard'? Ci sono casi dove il prodotto la segue o la contrasta?",
                        f"Domanda {code}: le specifiche tecniche (materiali, tolleranze) arricchiscono o contraddicono il nucleo tecnico aziendale? Cosa rende questo prodotto tecnicamente unico?",
                        f"Domanda {code}: il processo di installazione descritto e standard o richiede adattamenti? Come si collega al modo in cui l'azienda approccia il mercato?",
                        f"Domanda {code}: l'identita del prodotto si allinea alla postura aziendale? Se l'azienda e 'tecnica e precisa', questo prodotto lo riflette pienamente?",
                    ][index]
                    question = combinational
                elif index < 8:
                    pool = "kb_anchored"
                    question = [
                        f"Domanda {code}: i documenti tecnici specificano tolleranze o parametri che il pre-DNA ha riassunto in modo generico. Quali numeri esatti dovremmo registrare?",
                        f"Domanda {code}: il manuale o la brochure menzionano un caso d'uso o limite che non e stato catturato nel pre-DNA? Quale?",
                        f"Domanda {code}: cosa rivela il file caricato sul processo di produzione o sui controlli qualita che il DNA non ha ancora riflesso?",
                    ][index - 5]
                else:
                    pool = "meta"
                    question = {
                        8: "Cosa sa chi lavora con questo prodotto ogni giorno che non e scritto nei documenti e che cambierebbe il modo in cui lo presentiamo ai clienti?",
                        9: "Qual e l'errore piu costoso che un installatore fa con questo prodotto nei primi 30 giorni, e come lo preveniamo?",
                    }[index]
                questions.append({
                    "code": code,
                    "pool": pool,
                    "section_key": sections[index % len(sections)],
                    "principle": f"Principio {code}",
                    "question": question,
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

        if "SEED_VARIANT" in prompt:
            if "MATERIALI" in prompt:
                return LLMResult(
                    text=json.dumps({
                        "identita_tecnica": "Sistema di scarico in INOX per edilizia, categoria canali di drenaggio tecnico residenziale.",
                        "architettura": "Corpo in acciaio INOX AISI 304 spessore 2mm, saldature TIG. Copricanale removibile. La scelta del 304 sul 316 e un compromise costo-resistenza adeguato per applicazioni standard.",
                        "specifiche": "Sezione 90x90mm, lunghezza 100cm modulare, tolleranza +-0.5mm. Portata 12 l/s. Peso 3.2 kg/m. EN 1123. Temperatura max 80C.",
                        "applicazione": "Installazione su letto di malta. Il copricanale removibile permette ispezione senza smontaggio.",
                        "vincoli": "Max 200kg carico verticale. Non compatibile con resina. Max 80C. Non per agenti chimici corrosivi forti.",
                        "configurazione": "Sezione fissa, lunghezza modulare 50-100cm. Custom su lotti >50pz.",
                    }, ensure_ascii=False),
                    tokens_in=500, tokens_out=350, cost=0.0003, latency_ms=700,
                )
            elif "WORKFLOW" in prompt:
                return LLMResult(
                    text=json.dumps({
                        "identita_tecnica": "Canale ispezionabile per drenaggio tecnico, progettato per accessibilita di manutenzione senza attrezzi.",
                        "architettura": "Struttura INOX con copricanale a scorrimento laterale. Il design enfatizza la smontabilita rispetto alla portata.",
                        "specifiche": "Sezione 90x90mm, moduli 100cm. Portata 12 l/s. Peso 3.2 kg/m.",
                        "applicazione": "Posa su malta con stuccaggio epossidico perimetrale. Ispezione tramite sfila-copricanale senza attrezzi. Manutenzione raccomandata ogni 6 mesi per residui. Il punto critico del workflow e la sigillatura.",
                        "vincoli": "Non per carichi >200kg. Temperatura max 80C. La manutenzione semestrale e obbligatoria per garantire la portata.",
                        "configurazione": "Modulare in lunghezza. Custom su lotti >50pz. La configurazione segue le esigenze di ispezione del cantiere.",
                    }, ensure_ascii=False),
                    tokens_in=500, tokens_out=350, cost=0.0003, latency_ms=700,
                )
            elif "DECISIONE" in prompt or "CONFIGURAZIONE" in prompt:
                return LLMResult(
                    text=json.dumps({
                        "identita_tecnica": "Canale di drenaggio modulare INOX, configurabile per progetti residenziali e commerciali standard.",
                        "architettura": "INOX AISI 304, 2mm, saldature TIG. Copricanale removibile.",
                        "specifiche": "Sezione 90x90mm, lunghezza modulare 50-100cm. Portata 12 l/s.",
                        "applicazione": "Installazione standard su malta. Ispezione senza attrezzi.",
                        "vincoli": "La sezione geometrica e fissa: questo e il vincolo fondamentale. Non si modifica la forma, solo la lunghezza. Max 80C, max 200kg.",
                        "configurazione": "Regola decisionale chiave: sezione geometrica IMMODIFICABILE. Custom solo in lunghezza (moduli 50-100cm). Varianti di forma MAI per richieste singole. Custom accettati solo per lotti >50pezzi con lead time 4-6 settimane. Il costo di setup per custom e il filtro naturale: chi non e disposto a pagarlo non e il cliente target.",
                    }, ensure_ascii=False),
                    tokens_in=500, tokens_out=350, cost=0.0003, latency_ms=700,
                )

        if "MERGE_DNA_SPECIALISTA" in prompt:
            return LLMResult(
                text=json.dumps({
                    "identita_tecnica": (
                        "Sistema di scarico integrato in vasca in acciaio INOX, progettato per garantire "
                        "ispezionabilita senza smontaggio. Canale di drenaggio tecnico modulare per edilizia "
                        "residenziale e commerciale. La configurabilita in lunghezza lo adatta a progetti diversi "
                        "ma la sezione geometrica fissa ne definisce il perimetro applicativo."
                    ),
                    "architettura": (
                        "Canale in acciaio INOX AISI 304 spessore 2mm, giunzioni saldate TIG. "
                        "Copricanale removibile con sistema di scorrimento laterale. "
                        "Piastrino di ispezione integrato per accesso diretto senza attrezzi. "
                        "La scelta dell'AISI 304 riflette un compromise tra resistenza alla corrosione "
                        "e costi, adeguato per applicazioni standard."
                    ),
                    "specifiche": (
                        "Sezione 90x90mm, lunghezza modulare 100cm, tolleranza +-0.5mm. "
                        "Conforme EN 1123. Portata nominale 12 l/s. Peso 3.2 kg/m lineare. "
                        "Temperatura massima 80C."
                    ),
                    "applicazione": (
                        "Installazione su letto di malta, stuccaggio perimetrale con sigillante "
                        "epossidico. Ispezione tramite sfila-copricanale senza attrezzi. "
                        "Manutenzione consigliata ogni 6 mesi per rimozione residui. "
                        "Il punto critico del workflow e la sigillatura perimetrale."
                    ),
                    "vincoli": (
                        "Non per carichi verticali superiori a 200kg. Non compatibile con vasche "
                        "in resina. Temperatura massima 80C. Non installare in presenza di "
                        "agenti chimici corrosivi forti. La manutenzione semestrale e obbligatoria."
                    ),
                    "configurazione": (
                        "Sezione geometrica IMMODIFICABILE, modificabile solo in lunghezza "
                        "(moduli da 50-100cm). Custom accettati su lotti superiori a 50pz. "
                        "Non si realizzano varianti di forma per richieste singole. "
                        "Lead time custom: 4-6 settimane lavorative."
                    ),
                }, ensure_ascii=False),
                tokens_in=800, tokens_out=500, cost=0.0005, latency_ms=900,
            )

        if "REFINEMENT_SEZIONE" in prompt:
            section_texts = {
                "IDENTITA_TECNICA": (
                    "Sistema di scarico integrato in vasca in acciaio INOX AISI 304, progettato per "
                    "garantire piena ispezionabilita senza smontaggio. Appartiene alla categoria dei "
                    "canali di drenaggio tecnico modulare per edilizia residenziale e commerciale. "
                    "La configurabilita in lunghezza lo adatta a progetti diversi, mentre la sezione "
                    "geometrica fissa ne definisce il perimetro applicativo. Si distingue dai canali "
                    "a incastro tradizionali per il copricanale a scorrimento laterale removibile."
                ),
                "ARCHITETTURA": (
                    "Corpo principale in acciaio INOX AISI 304 spessore 2mm con giunzioni saldate TIG "
                    "per garantire tenuta stagna a lungo termine. La scelta dell'AISI 304 rispetto al "
                    "316L riflette un compromise tra resistenza alla corrosione e costi, adeguato per "
                    "applicazioni residenziali e commerciali standard. Copricanale removibile con "
                    "sistema di scorrimento laterale per accesso diretto. Piastrino di ispezione "
                    "integrato che distingue questo prodotto dai sistemi a incastro tradizionali."
                ),
                "SPECIFICHE": (
                    "Sezione interna 90x90mm. Lunghezza modulare 100cm (moduli disponibili: 50cm e "
                    "100cm). Tolleranza dimensionale +-0.5mm. Portata nominale 12 l/s verificata "
                    "secondo EN 1253-2. Peso 3.2 kg/m lineare. Conforme EN 1123 (scarichi liquidi). "
                    "Temperatura di esercizio massima 80C. Carico verticale massimo 200kg. "
                    "Materiale: acciaio INOX AISI 304, spessore 2mm. Certificazione food-grade "
                    "per applicazioni HACCP: Da chiarire in intervista."
                ),
                "APPLICAZIONE": (
                    "Installazione su letto di malta con stuccaggio perimetrale utilizzando sigillante "
                    "epossidico. Il copricanale si rimuove per scorrimento laterale senza attrezzi, "
                    "consentendo ispezione completa del canale. Manutenzione programmata ogni 6 mesi "
                    "per rimozione residui e verifica della tenuta della sigillatura. Il punto critico "
                    "del processo di installazione e la corretta esecuzione dello stuccaggio "
                    "perimetrale, che determina la tenuta idraulica a lungo termine."
                ),
                "VINCOLI": (
                    "Carico verticale massimo 200kg — non idoneo per aree con transito veicolare. "
                    "Temperatura massima 80C — non per scarichi di fluidi caldi industriali. "
                    "Incompatibile con vasche in resina per differenza strutturale. Non installare "
                    "in presenza di agenti chimici corrosivi forti (acidi concentrati, alcali forti). "
                    "Manutenzione semestrale obbligatoria per garantire la portata nominale nel tempo."
                ),
                "CONFIGURAZIONE": (
                    "Sezione geometrica IMMODIFICABILE — la personalizzazione avviene esclusivamente "
                    "in lunghezza modulare (moduli da 50cm o 100cm). Regola decisionale: varianti di "
                    "forma non vengono realizzate MAI per richieste singole. Custom accettati solo "
                    "per lotti superiori a 50 pezzi, con lead time di 4-6 settimane lavorative e "
                    "costo di setup aggiuntivo per modifica matrice di taglio. Il costo di setup "
                    "funziona da filtro naturale: chi non e disposto a sostenerlo non e il "
                    "cliente target per configurazioni personalizzate."
                ),
            }
            for section_key, text in section_texts.items():
                if section_key in prompt:
                    return LLMResult(
                        text=text,
                        tokens_in=300, tokens_out=200, cost=0.0002, latency_ms=500,
                    )

        if "CONCEPT_MAP_SPECIALISTA" in prompt:
            return LLMResult(
                text=json.dumps({
                    "entities": [
                        {"name": "acciaio INOX AISI 304", "type": "materiale"},
                        {"name": "saldatura TIG", "type": "processo"},
                        {"name": "copricanale removibile", "type": "componente"},
                        {"name": "piastrino di ispezione", "type": "componente"},
                        {"name": "EN 1123", "type": "standard"},
                        {"name": "malta di posa", "type": "materiale"},
                        {"name": "sigillante epossidico", "type": "materiale"},
                    ],
                    "relations": [
                        {"from": "acciaio INOX AISI 304", "to": "resistenza corrosione", "type": "determina"},
                        {"from": "spessore 2mm", "to": "tenuta strutturale", "type": "garantisce"},
                        {"from": "copricanale removibile", "to": "ispezionabilita", "type": "abilita"},
                        {"from": "malta di posa", "to": "stabilita installazione", "type": "necessaria"},
                    ],
                    "parameters": [
                        {"name": "spessore", "value": "2", "unit": "mm", "source": "documento"},
                        {"name": "sezione", "value": "90x90", "unit": "mm", "source": "documento"},
                        {"name": "lunghezza modulare", "value": "100", "unit": "cm", "source": "documento"},
                        {"name": "tolleranza", "value": "+-0.5", "unit": "mm", "source": "documento"},
                        {"name": "portata nominale", "value": "12", "unit": "l/s", "source": "documento"},
                        {"name": "peso", "value": "3.2", "unit": "kg/m", "source": "documento"},
                        {"name": "temperatura max", "value": "80", "unit": "C", "source": "documento"},
                        {"name": "carico verticale max", "value": "200", "unit": "kg", "source": "documento"},
                    ],
                    "gaps": [
                        {"what": "certificazione EN 1253-1", "why_missing": "non menzionata nei documenti ma rilevante per canali di drenaggio", "can_ask": True},
                        {"what": "compatibilita agenti chimici", "why_missing": "i documenti non specificano quali agenti chimici sono corrosivi per il prodotto", "can_ask": True},
                        {"what": "lead time custom", "why_missing": "non quantificato nei documenti per ordini personalizzati", "can_ask": True},
                    ],
                }, ensure_ascii=False),
                tokens_in=500,
                tokens_out=400,
                cost=0.0003,
                latency_ms=600,
            )

        if "ANALISI_NEURALE_SPECIALISTA" in prompt or "SINTESI_GLOBALE_DNA_SPECIALISTA" in prompt:
            return LLMResult(
                text=json.dumps({
                    "identita_tecnica": (
                        "Sistema di scarico integrato in vasca, progettato per garantire "
                        "ispezionabilita senza smontaggio. Appartiene alla categoria dei "
                        "canali di drenaggio tecnico per edilizia residenziale e commerciale."
                    ),
                    "architettura": (
                        "Canale in acciaio INOX AISI 304 spessore 2mm, giunzioni saldate TIG. "
                        "Copricanale removibile con sistema di scorrimento laterale. "
                        "Piastrino di ispezione integrato per accesso diretto."
                    ),
                    "specifiche": (
                        "Sezione 90x90mm, lunghezza modulare 100cm, tolleranza +-0.5mm. "
                        "Conforme EN 1123. Portata nominale 12 l/s. Peso 3.2 kg/m lineare."
                    ),
                    "applicazione": (
                        "Installazione su letto di malta, stuccaggio perimetrale con sigillante "
                        "epossidico. Ispezione tramite sfila-copricanale senza attrezzi. "
                        "Manutenzione consigliata ogni 6 mesi per rimozione residui."
                    ),
                    "vincoli": (
                        "Non per carichi verticali superiori a 200kg. Non compatibile con vasche "
                        "in resina. Temperatura massima 80C. Non installare in presenza di "
                        "agenti chimici corrosivi forti."
                    ),
                    "configurazione": (
                        "Sezione geometrica fissa, modificabile solo in lunghezza (moduli da 50-100cm). "
                        "Custom accettati su lotti superiori a 50pz. Non si realizzano varianti "
                        "di forma per richieste singole."
                    ),
                }, ensure_ascii=False),
                tokens_in=600,
                tokens_out=400,
                cost=0.0004,
                latency_ms=800,
            )

        if "FEEDBACK_SPECIALISTA_GENERALE" in prompt:
            return LLMResult(
                text=json.dumps({
                    "proposals": [
                        {
                            "target_layer": "nucleo_tecnico",
                            "current_value": "Acciai speciali per edilizia e industria",
                            "proposed_value": "Acciai speciali per edilizia e industria. Sistema di canali ispezionabili in INOX AISI 304 come famiglia prodotto distintiva.",
                            "rationale": "Il DNA Specialista rivela 'Canale Ispezionabile' come famiglia prodotto tecnico non ancora catturata nel DNA Generale.",
                        },
                        {
                            "target_layer": "confini",
                            "current_value": "Richieste incompatibili con qualita e tracciabilita",
                            "proposed_value": "Richieste incompatibili con qualita e tracciabilita. Non si realizzano varianti di forma per richieste singole: custom solo su lotti superiori a 50pz.",
                            "rationale": "Il DNA Specialista definisce un confine operativo chiaro sulle personalizzazioni che arricchisce i confini aziendali.",
                        },
                        {
                            "target_layer": "logica_decisionale",
                            "current_value": "Il fuori standard deve produrre valore tecnico reale",
                            "proposed_value": "Il fuori standard deve produrre valore tecnico reale. La sezione geometrica dei prodotti e fissa: la personalizzazione avviene solo in lunghezza modulare, mai nella forma.",
                            "rationale": "La logica di configurazione del specialista definisce un principio decisionale aziendale sull'adattabilita dei prodotti.",
                        },
                    ]
                }, ensure_ascii=False),
                tokens_in=700,
                tokens_out=400,
                cost=0.0004,
                latency_ms=900,
            )

        if "SELF_CRITIQUE_SPECIALISTA" in prompt:
            return LLMResult(
                text=json.dumps({
                    "proposals": [
                        {
                            "section_key": "specifiche",
                            "issue": "La sezione specifiche manca di certificazioni di conformita specifiche al mercato europeo oltre alla EN 1123.",
                            "anti_memorization": False,
                            "proposed_text": (
                                "Sezione 90x90mm, lunghezza modulare 100cm, tolleranza +-0.5mm. "
                                "Conforme EN 1123 (scarichi liquidi) e EN 1253-1 (canali di drenaggio edilizi). "
                                "Portata nominale 12 l/s verificata secondo EN 1253-2. "
                                "Peso 3.2 kg/m lineare. Materiale certificato food-grade per applicazioni HACCP."
                            ),
                        },
                        {
                            "section_key": "configurazione",
                            "issue": "La logica di configurazione non quantifica i costi di setup per i custom.",
                            "anti_memorization": False,
                            "proposed_text": (
                                "Sezione geometrica fissa, modificabile solo in lunghezza (moduli da 50-100cm). "
                                "Custom accettati su lotti superiori a 50pz con costo di setup di circa 800 eur "
                                "per modifica matrice di taglio. Non si realizzano varianti di forma per "
                                "richieste singole. Lead time custom: 4-6 settimane lavorative."
                            ),
                        },
                        {
                            "section_key": "architettura",
                            "issue": "La descrizione dei materiali ripete quasi word-for-word il documento tecnico caricato, senza aggiungere interpretazione.",
                            "anti_memorization": True,
                            "proposed_text": (
                                "Il sistema combina INOX AISI 304 (spessore 2mm) per il corpo principale con "
                                "saldature TIG per garantire tenuta stagna a lungo termine. La scelta dell'AISI 304 "
                                "rispetto al 316 riflette un compromise tra resistenza alla corrosione e costi, "
                                "adeguato per applicazioni residenziali e commerciali standard. Il copricanale "
                                "removibile con scorrimento laterale e un dettaglio di progettazione che "
                                "distingue questo prodotto dai canali a incastro tradizionali."
                            ),
                        },
                    ]
                }, ensure_ascii=False),
                tokens_in=600,
                tokens_out=400,
                cost=0.0004,
                latency_ms=800,
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
