"""FireCrawl client with abstract interface for future GroktoCrawl swap."""

import logging
import os
import time
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE_URL = os.environ.get(
    "FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v2"
)

RETRY_MAX = 3
RETRY_BACKOFF = 2.0  # exponential base in seconds


class ScraperClient(ABC):
    """Abstract scraper — swap implementation without changing callers."""

    @abstractmethod
    def scrape(self, url: str) -> dict:
        """Scrape a URL and return structured data."""
        ...


class FireCrawlClient(ScraperClient):
    """FireCrawl v2 API client with retry logic."""

    def __init__(
        self,
        api_key: str = FIRECRAWL_API_KEY,
        base_url: str = FIRECRAWL_BASE_URL,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def scrape(self, url: str) -> dict:
        last_error = None
        for attempt in range(1, RETRY_MAX + 1):
            try:
                return self._call(url)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code < 500:
                    break
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
            if attempt < RETRY_MAX:
                wait = RETRY_BACKOFF ** attempt
                logger.warning(
                    "FireCrawl scrape attempt %d/%d failed for %s, "
                    "retrying in %.1fs: %s",
                    attempt, RETRY_MAX, url, wait, last_error,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"FireCrawl scrape failed for {url} after {RETRY_MAX} attempts"
        ) from last_error

    def _call(self, url: str) -> dict:
        if not self.api_key:
            raise RuntimeError("FIRECRAWL_API_KEY not set")
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/scrape",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": url, "formats": ["markdown"]},
            )
            resp.raise_for_status()
            data = resp.json()
            result = {
                "url": url,
                "markdown": "",
                "title": "",
                "description": "",
            }
            if "data" in data:
                result["markdown"] = data["data"].get("markdown", "")
                result["title"] = data["data"].get("metadata", {}).get("title", "")
                result["description"] = data["data"].get("metadata", {}).get(
                    "description", ""
                )
            return result


class MockScraperClient(ScraperClient):
    """Returns canned data — no network calls."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def scrape(self, url: str) -> dict:
        if self.fail:
            raise RuntimeError(f"Mock failure for {url}")
        return {
            "url": url,
            "title": "Rossi Metalli SRL — Prodotti Siderurgici",
            "description": "Azienda leader nella produzione di acciai speciali.",
            "markdown": (
                "# Rossi Metalli SRL\n\n"
                "## Chi Siamo\n"
                "Rossi Metalli è un'azienda siderurgica con 40 anni di esperienza "
                "nella produzione di acciai speciali per l'edilizia e l'industria.\n\n"
                "## Mission\n"
                "Fornire acciai di alta qualità con tempi di consegna ridotti.\n\n"
                "## Prodotti\n"
                "- Travi in acciaio al carbonio\n"
                "- Lamiere zincate\n"
                "- Profili personalizzati\n\n"
                "## Settori\n"
                "- Edilizia residenziale\n"
                "- Grandi infrastrutture\n"
                "- Industria meccanica\n"
            ),
        }


def get_scraper() -> ScraperClient:
    """Factory — returns FireCrawlClient if key is set, else MockScraperClient."""
    if FIRECRAWL_API_KEY:
        return FireCrawlClient()
    logger.info("FIRECRAWL_API_KEY not set, using mock scraper")
    return MockScraperClient()
