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


class MockLLMClient(LLMClient):
    def generate(self, prompt: str, model: str | None = None) -> LLMResult:
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


def get_llm_client() -> LLMClient:
    if LLM_API_KEY:
        return OpenAIClient(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or "")
    logger.info("LLM_API_KEY not set, using mock LLM client")
    return MockLLMClient()
