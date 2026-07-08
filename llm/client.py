"""Wrapper unificato per API LLM.

Supporta:
- Kimi API (default)
- OpenAI API
- Anthropic API

Gestisce retry, timeout, e streaming (future).
"""

import json
from typing import Any, AsyncGenerator, Optional

import httpx

from zeus.config import LLMConfig, get_config


class LLMError(Exception):
    """Errore generico LLM."""

    pass


class LLMClient:
    """Client LLM unificato."""

    PROVIDER_URLS = {
        "kimi-coding": "https://api.moonshot.cn/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
    }

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or get_config().llm
        self._client = httpx.Client(timeout=self.config.timeout)

    def _get_headers(self) -> dict[str, str]:
        """Restituisce gli header HTTP per il provider attuale."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.provider == "anthropic":
            headers["x-api-key"] = self.config.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _get_url(self, endpoint: str = "chat/completions") -> str:
        """Restituisce l'URL completo per un endpoint."""
        base = self.config.base_url or self.PROVIDER_URLS.get(
            self.config.provider, ""
        )
        if self.config.provider == "anthropic":
            endpoint = "messages"
        return f"{base}/{endpoint}"

    def _build_payload(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> dict[str, Any]:
        """Costruisce il payload JSON per la richiesta."""
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        payload.update(kwargs)

        if self.config.provider == "anthropic":
            # Anthropic usa formato diverso
            system_msg = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    user_messages.append(msg)
            payload = {
                "model": self.config.model,
                "system": system_msg,
                "messages": user_messages,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            }

        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Effettua una chiamata chat completion sincrona.

        Args:
            messages: Lista di messaggi [{role, content}]
            **kwargs: Parametri aggiuntivi per l'API

        Returns:
            Testo della risposta

        Raises:
            LLMError: Se la chiamata fallisce
        """
        url = self._get_url()
        headers = self._get_headers()
        payload = self._build_payload(messages, **kwargs)

        try:
            response = self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            if self.config.provider == "anthropic":
                return data["content"][0]["text"]
            return data["choices"][0]["message"]["content"]

        except httpx.HTTPStatusError as e:
            raise LLMError(f"HTTP {e.response.status_code}: {e.response.text}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"Risposta malformata: {e}") from e
        except Exception as e:
            raise LLMError(f"Errore LLM: {e}") from e

    def close(self) -> None:
        """Chiude il client HTTP."""
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def quick_chat(system: str, user: str, **kwargs: Any) -> str:
    """Funzione di convenienza per una singola chiamata.

    Args:
        system: System prompt
        user: User message
        **kwargs: Parametri extra

    Returns:
        Risposta testuale
    """
    client = LLMClient()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return client.chat(messages, **kwargs)
