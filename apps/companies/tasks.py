import json
import logging
import re
from pathlib import Path

from celery import shared_task
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.companies.llm_client import LLM_MODEL, get_llm_client
from apps.companies.models import CompanyDNA, LLMCall, PipelineRun, Source
from apps.companies.scraper import get_scraper

logger = logging.getLogger(__name__)


def _generate_dna(source: Source, company):
    """Shared DNA generation logic — called by view or pipeline task."""
    prompt_path = Path(__file__).parent / "prompts" / "dna_aziendale_v0.1.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace("{{scraped_content}}", source.scraped_data.get("markdown", ""))

    client = get_llm_client()
    result = client.generate(prompt)

    llm_call = LLMCall.objects.create(
        company=company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
        source=source,
    )

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    try:
        content = json.loads(result.text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", result.text, re.DOTALL)
        if match:
            try:
                content = json.loads(match.group(1))
            except json.JSONDecodeError:
                content = {"raw": result.text}
        else:
            content = {"raw": result.text}

    company.dna_versions.filter(is_current=True).update(is_current=False)
    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        content=content,
    )
    return dna, llm_call


@shared_task
def scrape_source(source_id: int, tenant_schema: str | None = None):
    def _run():
        try:
            source = Source.objects.get(pk=source_id)
        except Source.DoesNotExist:
            logger.error("scrape_source: source %d not found", source_id)
            return

        source.status = Source.STATUS_SCRAPING
        source.save(update_fields=["status"])

        scraper = get_scraper()

        try:
            result = scraper.scrape(source.url)
            source.scraped_data = result
            source.status = Source.STATUS_SCRAPED
            source.save(update_fields=["scraped_data", "status"])
        except Exception:
            logger.exception("Scrape failed for source %d (%s)", source_id, source.url)
            source.status = Source.STATUS_FAILED
            source.error_msg = "scrape failed"
            source.save(update_fields=["status", "error_msg"])

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task
def run_pipeline(pipeline_run_id: int, tenant_schema: str | None = None):
    def _run():
        try:
            run = PipelineRun.objects.select_related("company", "source").get(pk=pipeline_run_id)
        except PipelineRun.DoesNotExist:
            logger.error("run_pipeline: pipeline run %d not found", pipeline_run_id)
            return

        run.status = PipelineRun.STATUS_RUNNING
        run.current_step = "scrape"
        run.save(update_fields=["status", "current_step"])

        source = run.source
        try:
            if source.status != Source.STATUS_SCRAPED:
                run.current_step = "scrape"
                run.save(update_fields=["current_step"])
                scraper = get_scraper()
                result = scraper.scrape(source.url)
                source.scraped_data = result
                source.status = Source.STATUS_SCRAPED
                source.save(update_fields=["scraped_data", "status"])

            run.current_step = "generate_dna"
            run.save(update_fields=["current_step"])
            dna, llm_call = _generate_dna(source, run.company)

            run.current_step = "done"
            run.status = PipelineRun.STATUS_COMPLETED
            run.completed_at = timezone.now()
            run.save(update_fields=["current_step", "status", "completed_at"])
            logger.info(
                "Pipeline %d completed: DNA v%d, cost $%.4f",
                pipeline_run_id, dna.version, llm_call.cost_usd,
            )
        except Exception:
            logger.exception("Pipeline %d failed", pipeline_run_id)
            run.status = PipelineRun.STATUS_FAILED
            run.error_msg = "pipeline failed"
            run.save(update_fields=["status", "error_msg"])

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()
