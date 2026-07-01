import json
import logging
from pathlib import Path

from celery import shared_task
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.companies.audit import compute_audit_hash
from apps.companies.llm_client import (
    LLM_MODEL,
    LLM_MODEL_PRO,
    ZEUS_SYSTEM_PROMPT,
    _generate_with_retry,
    get_llm_client,
)
from apps.companies.models import CompanyDNA, LLMCall, PipelineRun, Product, ProductDNA, Source
from apps.companies.scraper import get_scraper

logger = logging.getLogger(__name__)


def _available_sources(source, company) -> dict:
    """Describe the sources actually available to the LLM for evidence checks."""
    files = [
        f.original_name
        for f in company.company_files.all()
        if f.original_name != "note-azienda.txt"
    ]
    has_note = company.company_files.filter(original_name="note-azienda.txt").exists()
    has_answer = company.company_questions.exclude(answer="").exists()
    return {
        "scrape": bool(source and source.scraped_data),
        "note": has_note,
        "files": files,
        "answer": has_answer,
    }


def _compute_enrichment(content, company, source=None) -> dict:
    """Compute the cognitive enrichment bundle for a DNA payload.

    Guarded: if enrichment fails (e.g. malformed content), returns a minimal
    bundle so the DNA is still saved. Enrichment is diagnostic, never blocking
    at the pre-DNA stage.
    """
    from apps.companies.dna_enrichment import build_enrichment
    try:
        if source is None:
            source = company.sources.filter(status=Source.STATUS_SCRAPED).order_by(
                "-created_at",
            ).first()
        available = _available_sources(source, company)
        return build_enrichment(content, available_sources=available)
    except Exception:
        logger.exception("Enrichment computation failed; saving DNA without full bundle")
        return {"error": "enrichment_computation_failed"}


def _validate_dna_content(content, company, *, stage="pre-dna"):
    """P4 — validate the 6-layer schema before saving.

    Non-blocking: logs a warning if the DNA is in safe_mode or structurally
    invalid, but never raises. Enrichment/validation are diagnostics; the
    pre-DNA stage still saves so the client can proceed to questions.
    """
    try:
        from pydantic import ValidationError

        from apps.companies.dna_schemas import coerce_dna_generale_content
        from apps.companies.dna_validator import validate_dna

        coerce_dna_generale_content(content)
        result = validate_dna(content)
        if result.safe_mode:
            logger.warning(
                "DNA %s in safe_mode for company %s: %s",
                stage,
                company.schema_name,
                [f.message for f in result.flags],
            )
    except ValidationError:
        logger.error(
            "DNA %s strutturalmente invalido per %s, contenuto: %s",
            stage,
            getattr(company, "schema_name", "?"),
            str(content)[:500],
        )
    except Exception:
        logger.exception(
            "DNA %s schema validation failed (non-blocking) for %s",
            stage,
            getattr(company, "schema_name", "?"),
        )


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

    from apps.companies.sector_archetypes import get_archetype_context
    operational_profile = get_archetype_context(company)

    prompt = prompt_template.replace(
        "{{scraped_content}}",
        source.scraped_data.get("markdown", "") if source.scraped_data else "",
    ).replace(
        "{{company_notes}}",
        "\n\n".join(notes_parts) or "Nessuna nota del cliente.",
    ).replace(
        "{{company_documents}}",
        "\n\n".join(docs_parts) or "Nessun documento aziendale caricato.",
    ).replace(
        "{{operational_profile}}",
        operational_profile or "Nessun profilo operativo fornito.",
    )

    client = get_llm_client()
    result, content = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL_PRO,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2),
        context="pre-dna",
    )

    llm_call = LLMCall.objects.create(
        company=company,
        model_name=LLM_MODEL_PRO,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
        source=source,
    )

    _validate_dna_content(content, company, stage="pre-dna")

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    company.dna_versions.filter(is_current=True).update(is_current=False)
    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        dna_type=CompanyDNA.TYPE_PRE,
        content=content,
    )
    # Cognitive enrichment + audit hash (PIANO 1.5 integration).
    dna._enrichment = _compute_enrichment(content, company, source)
    dna.audit_hash = compute_audit_hash(content, previous_hash="")
    dna.previous_hash = ""
    dna.save(update_fields=["_enrichment", "audit_hash", "previous_hash"])
    return dna, llm_call


def _extract_concept_map(product: Product, company):
    """Stage 1 — Extract structured concept map from product documents before DNA generation.

    This is the 'explicit planning phase': instead of asking the LLM to go from raw
    documents to DNA in one pass, we first extract entities, relations, parameters
    and gaps. The concept map then feeds into the DNA generation prompt.
    """
    documents = []
    for product_file in product.product_files.all()[:10]:
        documents.append(f"# {product_file.original_name}\n{product_file.content_text}")

    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = ""
    if company_dna:
        company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    from apps.companies.sector_archetypes import get_archetype_context
    archetype_context = get_archetype_context(company)

    prompt = f"""
CONCEPT_MAP_SPECIALISTA

Sei ZEUS. Analizza i documenti tecnici del prodotto "{product.name}" dell'azienda {company.name}.
Estrai una mappa concettuale strutturata. Non generare ancora il DNA: estrai SOLO i dati grezzi organizzati.

DNA AZIENDALE (contesto):
{company_context or "Non disponibile"}

PROFILO OPERATIVO:
{archetype_context or "Non disponibile"}

DOCUMENTI PRODOTTO:
{chr(10).join(documents) or "Nessun documento caricato."}

Estrai 4 categorie:

1. ENTITA: oggetti fisici, materiali, componenti, standard, certificazioni, processi.
2. RELAZIONI: dipendenze, causazioni, vincoli reciproci tra entita.
3. PARAMETRI: valori numerici, tolleranze, range operativi (con unita di misura).
4. GAPS: informazioni incomplete o ambigue nei documenti che richiedono chiarimento.

Output JSON:
{{
  "entities": [
    {{"name": "acciaio INOX AISI 304", "type": "materiale"}},
    {{"name": "saldatura TIG", "type": "processo"}}
  ],
  "relations": [
    {{"from": "acciaio INOX AISI 304", "to": "resistenza corrosione", "type": "determina"}}
  ],
  "parameters": [
    {{"name": "spessore", "value": "2", "unit": "mm", "source": "documento"}},
    {{"name": "temperatura max", "value": "80", "unit": "C", "source": "documento"}}
  ],
  "gaps": [
    {{"what": "certificazione food-grade", "why_missing": "non menzionata nei documenti", "can_ask": true}}
  ]
}}

REGOLE:
- Estrai SOLO cio che e nei documenti, non inventare.
- Sii specifico: nomi esatti di materiali, valori numerici precisi.
- Per i GAPS, indica sempre se possono essere chiesti al cliente (can_ask: true).

Rispondi SOLO JSON valido, senza markdown.
""".strip()

    client = get_llm_client()

    def _parse_concept_map(text):
        data = json.loads(text)
        for key in ("entities", "relations", "parameters", "gaps"):
            if not isinstance(data.get(key), list):
                data[key] = []
        return data

    try:
        result, concept_map = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.4, 0.3, 0.2),
            parse=_parse_concept_map,
            context="product-concept-map",
        )
        LLMCall.objects.create(
            company=company,
            model_name=LLM_MODEL,
            prompt_text=prompt,
            response_text=result.text,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost,
            latency_ms=result.latency_ms,
        )
        return concept_map
    except Exception:
        logger.exception("Concept map extraction failed for product %s", product.pk)
        return None


def _generate_product_dna(product: Product, company):
    """Generate ProductDNA from concept map + company DNA using multi-layer analysis."""
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = ""
    if company_dna:
        company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    from apps.companies.sector_archetypes import get_archetype_context
    archetype_context = get_archetype_context(company)

    concept_map = _extract_concept_map(product, company)

    if concept_map:
        concept_map_json = json.dumps(concept_map, ensure_ascii=False, indent=2)
        source_block = f"CONCEPT MAP (entita, relazioni, parametri, gap gia estratti):\n{concept_map_json}"
    else:
        documents = []
        for product_file in product.product_files.all()[:10]:
            documents.append(f"# {product_file.original_name}\n{product_file.content_text}")
        source_block = f"DOCUMENTI PRODOTTO:\n{chr(10).join(documents) or 'Nessun documento caricato.'}"

    prompt = f"""
ANALISI_NEURALE_SPECIALISTA

Sei ZEUS. Genera il DNA tecnico del prodotto "{product.name}" dell'azienda {company.name}.

Hai a disposizione una CONCEPT MAP gia estratta dai documenti. Il tuo compito e
mapparla su 6 sezioni tecniche strutturate, colmando i gap con intervista.

{source_block}

DNA AZIENDALE (contesto — eredita, non ripetere):
{company_context or "Non disponibile"}

PROFILO OPERATIVO AZIENDALE:
{archetype_context or "Non disponibile"}

Output JSON con ESATTAMENTE queste 6 chiavi (ogni valore e una stringa
narrativa tecnica completa e autonoma):

{{
  "identita_tecnica": "Cosa e il prodotto, che problema risolve, categoria tecnica di appartenenza",
  "architettura": "Materiali, struttura, componenti, come e costruito fisicamente",
  "specifiche": "Dimensioni, tolleranze, standard di riferimento, certificazioni, parametri numerici",
  "applicazione": "Come si monta, si usa, si mantiene, workflow di installazione e ispezione",
  "vincoli": "Cosa NON fa, limiti ambientali, incompatibilita, controindicazioni tecniche",
  "configurazione": "Varianti disponibili, personalizzazioni accettate, quando dire no a richieste custom"
}}

REGOLE:
- Usa i PARAMETRI della concept map per arricchire le sezioni con dati numerici precisi.
- Usa le RELAZIONI per spiegare come le entita si influenzano a vicenda.
- Usa i GAPS per identificare cosa scrivere come "Da chiarire in intervista".
- Sintetizza, non parafrasare: riformula, collega, aggiungi interpretazione tecnica.
- Non inventare dati tecnici non presenti nella concept map.
- Il tono e tecnico-preciso, non commerciale.
- Se il PROFILO OPERATIVO contiene contesto libero dal cliente, usalo come fonte primaria.

Rispondi SOLO JSON valido, senza markdown, senza preambolo.
""".strip()

    client = get_llm_client()
    result, content = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2),
        context="product-pre-dna",
    )

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

    from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS as PLK
    missing = [k for k in PLK if not content.get(k)]
    if missing:
        logger.warning(
            "Product pre-DNA incompleto per %s, sezioni mancanti: %s",
            company.schema_name,
            missing,
        )

    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
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


@shared_task(soft_time_limit=300, time_limit=360)
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


@shared_task(soft_time_limit=600, time_limit=660)
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


@shared_task
def generate_product_questions_task(product_id, pre_dna_id, tenant_schema=None):
    def _run():
        from apps.companies.models import Product, ProductDNA
        from apps.companies.views import _generate_product_questions

        try:
            product = Product.objects.get(pk=product_id)
            pre_dna = ProductDNA.objects.get(pk=pre_dna_id)
        except (Product.DoesNotExist, ProductDNA.DoesNotExist):
            logger.error("generate_product_questions: product or pre_dna not found")
            return

        try:
            _generate_product_questions(product, pre_dna)
            logger.info("Product questions generated for product %s", product.pk)
        except Exception:
            logger.exception(
                "Product question generation failed for product %s", product.pk
            )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()
