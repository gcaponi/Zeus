import json
import logging
import re
from pathlib import Path

from celery import shared_task
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.companies.llm_client import LLM_MODEL, get_llm_client
from apps.companies.models import CompanyDNA, LLMCall, PipelineRun, Product, ProductDNA, Source
from apps.companies.scraper import get_scraper

logger = logging.getLogger(__name__)


def _generate_dna(source: Source, company):
    """Shared DNA generation logic — called by view or pipeline task.

    Reads 3 separate sources: scraped website, client notes, and company documents.
    The client notes (saved as 'note-azienda.txt') are separated from real documents
    so the LLM sees them as distinct context blocks.
    """
    prompt_path = Path(__file__).parent / "prompts" / "dna_generale_v1.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")

    notes_parts = []
    docs_parts = []
    for company_file in company.company_files.all()[:10]:
        if company_file.original_name == "note-azienda.txt":
            notes_parts.append(company_file.content_text)
        else:
            docs_parts.append(f"# {company_file.original_name}\n{company_file.content_text}")

    prompt = prompt_template.replace(
        "{{scraped_content}}",
        source.scraped_data.get("markdown", "") if source.scraped_data else "",
    ).replace(
        "{{company_notes}}",
        "\n\n".join(notes_parts) or "Nessuna nota del cliente.",
    ).replace(
        "{{company_documents}}",
        "\n\n".join(docs_parts) or "Nessun documento aziendale caricato.",
    )

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
        dna_type=CompanyDNA.TYPE_PRE,
        content=content,
    )
    return dna, llm_call


def _generate_product_dna(product: Product, company):
    """Generate ProductDNA from product files and company DNA."""
    documents = []
    for product_file in product.product_files.all()[:10]:
        documents.append(f"# {product_file.original_name}\n{product_file.content_text}")

    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = ""
    if company_dna:
        company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    prompt = f"""
Sei ZEUS. Genera un pre-DNA per il prodotto "{product.name}" dell'azienda {company.name}.

DNA AZIENDALE:
{company_context or "Non disponibile"}

DOCUMENTI PRODOTTO:
{chr(10).join(documents) or "Nessun documento prodotto caricato."}

Genera un JSON con queste sezioni:
{{
  "descrizione": "Descrizione sintetica del prodotto",
  "applicazione": "Come e dove si usa il prodotto",
  "specifiche": "Specifiche tecniche principali",
  "vincoli": "Vincoli tecnici, produttivi o applicativi",
  "valore": "Valore differenziante del prodotto"
}}

Rispondi SOLO JSON valido, senza markdown.
""".strip()

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
    )

    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    try:
        content = json.loads(result.text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", result.text, re.DOTALL)
        if match:
            try:
                content = json.loads(match.group(1))
            except json.JSONDecodeError:
                content = {"raw": result.text}
        else:
            content = {"raw": result.text}

    product.dna_versions.filter(is_current=True).update(is_current=False)
    dna = ProductDNA.objects.create(
        product=product,
        version=next_version,
        dna_type=ProductDNA.TYPE_PRE,
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


@shared_task
def generate_complete_dna(company_id, pre_dna_id, user_id, tenant_schema=None):
    def _run():
        from django.contrib.auth import get_user_model
        from apps.companies.models import Company
        from apps.companies.views import _create_complete_dna

        try:
            company = Company.objects.get(pk=company_id)
            pre_dna = CompanyDNA.objects.get(pk=pre_dna_id)
        except (Company.DoesNotExist, CompanyDNA.DoesNotExist):
            logger.error("generate_complete_dna: company or pre_dna not found")
            return

        User = get_user_model()
        user = User.objects.filter(pk=user_id).first() if user_id else None

        try:
            _create_complete_dna(company, pre_dna, user)
            logger.info("Complete DNA generated for company %s", company.schema_name)
        except Exception:
            logger.exception(
                "Complete DNA generation failed for company %s", company.schema_name
            )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task
def generate_complete_product_dna(product_id, pre_dna_id, user_id, tenant_schema=None):
    def _run():
        from django.contrib.auth import get_user_model
        from apps.companies.models import Product
        from apps.companies.views import _create_complete_product_dna

        try:
            product = Product.objects.get(pk=product_id)
            pre_dna = ProductDNA.objects.get(pk=pre_dna_id)
        except (Product.DoesNotExist, ProductDNA.DoesNotExist):
            logger.error(
                "generate_complete_product_dna: product or pre_dna not found"
            )
            return

        User = get_user_model()
        user = User.objects.filter(pk=user_id).first() if user_id else None

        try:
            _create_complete_product_dna(product, pre_dna, user)
            logger.info("Complete product DNA generated for product %s", product.pk)
        except Exception:
            logger.exception(
                "Complete product DNA generation failed for product %s", product.pk
            )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()
