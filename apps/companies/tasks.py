import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.companies.audit import compute_audit_hash
from apps.companies.dna_schemas import LAYER_KEYS, PRODUCT_LAYER_KEYS
from apps.companies.llm_client import (
    LLM_MODEL,
    LLM_MODEL_PRO,
    ZEUS_SYSTEM_PROMPT,
    _generate_with_retry,
    get_llm_client,
)
from apps.companies.models import (
    CompanyDNA,
    ConsistencyIssue,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    Source,
)
from apps.companies.scraper import get_scraper

logger = logging.getLogger(__name__)


def _run_in_schema(tenant_schema, func, *args):
    if tenant_schema:
        with schema_context(tenant_schema):
            return func(*args)
    return func(*args)


def _set_progress(run_id, step_num, steps_total, label):
    """Update PipelineRun.current_step with structured progress info.
    
    Format: \"step_num/steps_total: Label\"
    Example: \"3/10: Generazione layer cognitivo\"
    """
    try:
        run = PipelineRun.objects.get(pk=run_id)
        run.current_step = f"{step_num}/{steps_total}: {label}"
        run.status = PipelineRun.STATUS_RUNNING
        run.save(update_fields=["current_step", "status"])
    except PipelineRun.DoesNotExist:
        logger.warning(f"PipelineRun {run_id} not found for progress update")


def _set_product_generation_progress(product_id, step_num, steps_total, label):
    """Persist Specialist generation progress for the polling UI."""
    Product.objects.filter(pk=product_id).update(
        status=Product.STATUS_IN_COSTRUZIONE,
        generation_step=f"{step_num}/{steps_total}: {label}",
        updated_at=timezone.now(),
    )


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


def _text(value, limit=5000):
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value or "")
    return text[:limit]


def _consistency_specialist_records(company, product=None):
    if product is not None:
        products = company.products.filter(pk=product.pk).order_by("name")
    else:
        products = company.products.filter(status=Product.STATUS_ATTIVO).order_by("name")
    records = []
    for specialist in products:
        dna = specialist.dna_versions.filter(
            dna_type=ProductDNA.TYPE_COMPLETE,
            is_current=True,
        ).first()
        if not dna:
            continue
        content = dna.content if isinstance(dna.content, dict) else {}
        records.append({
            "product_id": specialist.pk,
            "product_name": specialist.name,
            "tipologia": specialist.tipologia or "",
            "codice": specialist.codice or "",
            "dna_id": dna.pk,
            "dna_version": dna.version,
            "layers": {key: _text(content.get(key, "")) for key in PRODUCT_LAYER_KEYS},
        })
    return records


def _normalize_consistency_issues(raw, scope, company_dna, records):
    raw = raw if isinstance(raw, dict) else {}
    raw_issues = raw.get("issues") or []
    if not isinstance(raw_issues, list):
        return []

    valid_severities = {
        ConsistencyIssue.SEVERITY_LOW,
        ConsistencyIssue.SEVERITY_MEDIUM,
        ConsistencyIssue.SEVERITY_HIGH,
    }
    valid_company_layers = set(LAYER_KEYS)
    valid_product_layers = set(PRODUCT_LAYER_KEYS)
    source_product_ids = {record["product_id"] for record in records}
    source_dna_ids = {record["dna_id"] for record in records}

    issues = []
    for item in raw_issues[:12]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), limit=160).strip()
        description = _text(item.get("description") or item.get("issue"), limit=4000).strip()
        if not title or not description:
            continue
        severity = str(item.get("severity") or ConsistencyIssue.SEVERITY_MEDIUM).lower()
        if severity not in valid_severities:
            severity = ConsistencyIssue.SEVERITY_MEDIUM
        company_layer = str(item.get("company_layer") or "")
        product_layer = str(item.get("product_layer") or "")
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        evidence.update({
            "company_dna_id": company_dna.pk,
            "company_dna_version": company_dna.version,
            "source_product_ids": sorted(source_product_ids),
            "source_product_dna_ids": sorted(source_dna_ids),
        })
        issues.append({
            "scope": scope,
            "issue_type": _text(item.get("issue_type") or "coherence", limit=40).strip(),
            "severity": severity,
            "title": title,
            "description": description,
            "recommendation": _text(item.get("recommendation"), limit=4000).strip(),
            "company_layer": company_layer if company_layer in valid_company_layers else "",
            "product_layer": product_layer if product_layer in valid_product_layers else "",
            "evidence": evidence,
        })
    return issues


def _update_accumulated_structure(company_dna, records, audit_summary):
    content = dict(company_dna.content) if isinstance(company_dna.content, dict) else {}
    content.pop("_consistency_audit_pending", None)
    accumulated = content.get("_accumulated") if isinstance(content.get("_accumulated"), dict) else {}
    accumulated.update({
        "schema_version": 1,
        "updated_at": timezone.now().isoformat(),
        "company_dna_id": company_dna.pk,
        "company_dna_version": company_dna.version,
        "active_specialist_count": len(records),
        "source_product_dna_ids": sorted(record["dna_id"] for record in records),
        "last_consistency_audit": audit_summary,
    })
    content["_accumulated"] = accumulated
    company_dna.content = content
    company_dna.audit_hash = compute_audit_hash(content, company_dna.previous_hash or "")
    company_dna.save(update_fields=["content", "audit_hash"])


def _run_consistency_audit(company_id, scope=ConsistencyIssue.SCOPE_PERIODIC, product_id=None):
    from apps.companies.models import Company

    try:
        company = Company.objects.get(pk=company_id)
    except Company.DoesNotExist:
        logger.error("run_consistency_audit: company %s not found", company_id)
        return 0

    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not company_dna:
        logger.error("run_consistency_audit: Company DNA not found for %s", company.schema_name)
        return 0

    product = None
    product_dna = None
    if product_id is not None:
        try:
            product = company.products.get(pk=product_id)
        except Product.DoesNotExist:
            logger.error("run_consistency_audit: product %s not found", product_id)
            return 0
        product_dna = product.dna_versions.filter(
            dna_type=ProductDNA.TYPE_COMPLETE,
            is_current=True,
        ).first()

    records = _consistency_specialist_records(company, product=product)
    if not records:
        audit_summary = {
            "scope": scope,
            "generated_at": timezone.now().isoformat(),
            "issue_count": 0,
            "status": "no_active_specialists",
        }
        _update_accumulated_structure(company_dna, [], audit_summary)
        return 0

    prompt_path = Path(__file__).parent / "prompts" / "consistency_audit_v1.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace(
        "{{scope}}",
        scope,
    ).replace(
        "{{company_dna}}",
        json.dumps({key: company_dna.content.get(key, "") for key in LAYER_KEYS}, ensure_ascii=False, indent=2),
    ).replace(
        "{{specialist_records}}",
        json.dumps(records, ensure_ascii=False, indent=2),
    ).replace(
        "{{company_layers}}",
        ", ".join(LAYER_KEYS),
    ).replace(
        "{{product_layers}}",
        ", ".join(PRODUCT_LAYER_KEYS),
    )

    raw = {"summary": "Audit non eseguito.", "issues": []}
    try:
        client = get_llm_client()
        result, raw = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.25, 0.15, 0.05),
            context="consistency-audit",
        )
        LLMCall.objects.create(
            company=company,
            model_name=LLM_MODEL_PRO,
            prompt_text=prompt,
            response_text=result.text,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost,
            latency_ms=result.latency_ms,
        )
    except Exception:
        logger.exception("Consistency audit failed for company %s", company.schema_name)

    issues = _normalize_consistency_issues(raw, scope, company_dna, records)
    now = timezone.now()
    with transaction.atomic():
        previous = ConsistencyIssue.objects.filter(
            company=company,
            company_dna=company_dna,
            scope=scope,
            status=ConsistencyIssue.STATUS_OPEN,
        )
        if product is not None:
            previous = previous.filter(product=product)
        else:
            previous = previous.filter(product__isnull=True)
        previous.update(status=ConsistencyIssue.STATUS_ARCHIVED, resolved_at=now)

        ConsistencyIssue.objects.bulk_create([
            ConsistencyIssue(
                company=company,
                company_dna=company_dna,
                product=product,
                product_dna=product_dna,
                **issue,
            )
            for issue in issues
        ])

    audit_summary = {
        "scope": scope,
        "generated_at": now.isoformat(),
        "issue_count": len(issues),
        "high_count": sum(1 for issue in issues if issue["severity"] == ConsistencyIssue.SEVERITY_HIGH),
        "summary": _text(raw.get("summary"), limit=1000),
        "source_product_dna_ids": sorted(record["dna_id"] for record in records),
    }
    _update_accumulated_structure(company_dna, records, audit_summary)
    logger.info(
        "Consistency audit completed for %s scope=%s issues=%d",
        company.schema_name, scope, len(issues),
    )
    return len(issues)


def _validate_dna_content(content, company, *, stage="pre-dna"):
    """P4 — validate the 6-layer schema before saving.

    Non-blocking: logs a warning if the DNA is in safe_mode or structurally
    invalid, but never raises. Enrichment/validation are diagnostics; the
    pre-DNA stage still saves so the client can proceed to questions.

    Also checks for editorial leakage (A1) — fragments like "Aggiungere:",
    "TODO:", "Problema di Zeus" that should never appear in published DNA.
    """
    try:
        from pydantic import ValidationError

        from apps.companies.dna_schemas import coerce_dna_generale_content
        from apps.companies.dna_validator import validate_dna, validate_no_editorial_leakage

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

    # A1 — editorial leakage check for all DNA types
    try:
        leaks = validate_no_editorial_leakage(content)
        if leaks:
            logger.error(
                "DNA %s EDITORIAL LEAKAGE for %s: %d fragments found. First: %s",
                stage,
                company.schema_name,
                len(leaks),
                leaks[0][:200],
            )
    except Exception:
        logger.exception("Editorial leakage check failed for %s", company.schema_name)


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

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

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


_SEED_ANGLES = {
    "materials": (
        "ANGOLO MATERIALI E VINCOLI FISICI. "
        "Parti dall'architettura materiale: cosa e fatto, come e costruito, "
        "quali vincoli fisici ne derivano. Poi deduci identita, specifiche, "
        "applicazione e configurazione dai materiali."
    ),
    "workflow": (
        "ANGOLO APPLICAZIONE E WORKFLOW OPERATIVO. "
        "Parti da come si installa e si usa sul campo: qual e il processo, "
        "quali sono i passaggi critici. Poi deduci vincoli, configurazione, "
        "specifiche e architettura dal workflow reale."
    ),
    "decision": (
        "ANGOLO CONFIGURAZIONE E LOGICA DECISIONALE. "
        "Parti dalle decisioni: cosa e standard, cosa e personalizzabile, "
        "quando si dice no. Poi deduci vincoli, specifiche, applicazione "
        "e architettura dalla logica decisionale."
    ),
}


def _generate_seed_variant(concept_map, company, product, angle):
    """Generate one pre-DNA variant from a specific reading angle."""
    instruction = _SEED_ANGLES[angle]
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2) if company_dna else ""

    from apps.companies.sector_archetypes import get_archetype_context
    archetype_context = get_archetype_context(company)

    if concept_map:
        concept_map_json = json.dumps(concept_map, ensure_ascii=False, indent=2)
        source_block = f"CONCEPT MAP:\n{concept_map_json}"
    else:
        documents = []
        for pf in product.product_files.all()[:10]:
            documents.append(f"# {pf.original_name}\n{pf.content_text}")
        source_block = f"DOCUMENTI:\n{chr(10).join(documents) or 'Nessun documento.'}"

    prompt = f"""
SEED_VARIANT — {instruction}

Sei ZEUS. Analizza il prodotto "{product.name}" dell'azienda {company.name}.
Genera il DNA a 6 sezioni tecniche partendo dall'angolo indicato sopra.

{source_block}

DNA AZIENDALE (contesto):
{company_context or "Non disponibile"}

PROFILO OPERATIVO:
{archetype_context or "Non disponibile"}

REGOLE:
- Tutte le 6 sezioni devono essere presenti e complete.
- Approccia ogni sezione dall'angolo indicato, ma assicurati che sia autonoma.
- Usa i PARAMETRI della concept map per dati numerici precisi.
- Usa i GAPS per identificare "Da chiarire in intervista".
- Sintetizza, non parafrasare.

Output JSON con 6 chiavi: identita_tecnica, architettura, specifiche,
applicazione, vincoli, configurazione. Rispondi SOLO JSON.
""".strip()

    client = get_llm_client()
    result, content = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2),
        context=f"product-seed-{angle}",
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
    return content


def _merge_seed_variants(variants, concept_map, company, product):
    """Merge multiple seed variants into a unified pre-DNA."""
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2) if company_dna else ""

    variant_blocks = []
    angle_labels = {
        "materials": "VARIANTE A (materiali)",
        "workflow": "VARIANTE B (workflow)",
        "decision": "VARIANTE C (decisione)",
    }
    for angle, content in variants.items():
        label = angle_labels.get(angle, angle)
        variant_blocks.append(f"{label}:\n{json.dumps(content, ensure_ascii=False, indent=2)}")

    prompt = f"""
MERGE_DNA_SPECIALISTA

Sei ZEUS. Tre analisi parallele del prodotto "{product.name}" sono state generate
da angoli di lettura diversi. Uniscile in un DNA unificato.

{chr(10).join(variant_blocks)}

DNA AZIENDALE (riferimento):
{company_context or "Non disponibile"}

REGOLE:
- Per ogni sezione, seleziona il contenuto piu completo e preciso tra le varianti.
- Se una variante contiene informazioni che le altre mancano, integrarle.
- Risolvi le contraddizioni preferendo la variante piu specifica e documentata.
- Elimina ridondanze: il DNA finale deve essere conciso ma completo.
- Mantieni tutti i dati numerici precisi trovati nelle varianti.
- Se tutte le varianti dicono "Da chiarire", mantienilo.

Output JSON con 6 chiavi: identita_tecnica, architettura, specifiche,
applicazione, vincoli, configurazione. Rispondi SOLO JSON.
""".strip()

    client = get_llm_client()
    result, content = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.4, 0.3, 0.2),
        context="product-merge",
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
    return content, llm_call


_SECTION_FOCUS = {
    "identita_tecnica": "categoria tecnica, problema risolto, posizionamento di mercato, cio che lo distingue da alternative generiche",
    "architettura": "materiali specifici con grado/peso, componenti, costruzione, scelta progettuale (perche questo materiale, questa giunzione)",
    "specifiche": "tutti i numeri: dimensioni, tolleranze, portate, pesi, standard, certificazioni — niente senza unita di misura",
    "applicazione": "passaggi installazione in ordine, attrezzi, tempistiche, punti critici, manutenzione programmata",
    "vincoli": "limiti numerici (max/min), incompatibilita testate, controindicazioni — ogni limite con il valore numerico",
    "configurazione": "regola decisionale per custom (se/threshold), varianti, lead time, cosa non si fa MAI e perche",
}


def _refine_single_section(key, current_text, concept_map, product, company):
    """Refine one DNA section independently, using only the concept map as reference."""
    focus = _SECTION_FOCUS.get(key, "")
    concept_map_json = json.dumps(concept_map, ensure_ascii=False, indent=2) if concept_map else "Non disponibile"

    prompt = f"""
REFINEMENT_SEZIONE — {key.upper()}

Sei ZEUS. Raffina UNA sola sezione del DNA Specialista per "{product.name}".
Non vedere le altre sezioni — concentrati esclusivamente su questa.

FOCUS DELLA SEZIONE: {focus}

CONCEPT MAP (riferimento per dati):
{concept_map_json}

VERSIONE ATTUALE:
{current_text or "Vuota o mancante."}

ISTRUZIONI:
- Aggiungi dettagli tecnici specifici usando i PARAMETRI della concept map.
- Se mancano dati numerici e sono nella concept map, aggiungili con unita di misura.
- Verifica che ogni affermazione sia supportata dalla concept map.
- Se un'informazione non e verificabile, scrivi "Da chiarire in intervista".
- Riformula per chiarezza tecnica: sintetizza, non elencare.
- Non aggiungere informazioni non presenti nella concept map.
- Mantieni il testo come narrativa tecnica fluida, non come lista.

Output: SOLO il testo della sezione {key}, nessun JSON, nessun preambolo.
""".strip()

    client = get_llm_client()
    result = client.generate(
        prompt,
        model=LLM_MODEL,
        temperature=0.3,
        system_prompt=ZEUS_SYSTEM_PROMPT,
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
    return result.text.strip()


def _refine_sections_parallel(merged_content, concept_map, product, company, tenant_schema=None):
    """Refine all 6 sections independently in parallel to eliminate anchoring bias."""
    from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS

    refined = dict(merged_content)
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_to_key = {}
        for key in PRODUCT_LAYER_KEYS:
            current_text = merged_content.get(key, "")
            future = pool.submit(
                _run_in_schema,
                tenant_schema,
                _refine_single_section,
                key,
                current_text,
                concept_map,
                product,
                company,
            )
            future_to_key[future] = key
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                new_text = future.result()
                if new_text:
                    refined[key] = new_text
            except Exception:
                logger.exception("Section refinement '%s' failed for product %s", key, product.pk)
    return refined


def _generate_product_dna(product: Product, company, tenant_schema=None):
    """Generate ProductDNA: concept map → 3 seed variants (parallel) → merge."""
    _set_product_generation_progress(product.pk, 1, 5, "Concept Map")
    concept_map = _extract_concept_map(product, company)

    _set_product_generation_progress(product.pk, 2, 5, "Multi-seed")
    variants = {}
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_to_angle = {
            pool.submit(
                _run_in_schema,
                tenant_schema,
                _generate_seed_variant,
                concept_map,
                company,
                product,
                angle,
            ): angle
            for angle in _SEED_ANGLES
        }
        for future in as_completed(future_to_angle):
            angle = future_to_angle[future]
            try:
                variants[angle] = future.result()
            except Exception:
                logger.exception("Seed variant '%s' failed for product %s", angle, product.pk)
                errors.append(angle)

    if not variants:
        logger.error("All seed variants failed for product %s, falling back to single-pass", product.pk)
        return _generate_product_dna_singlepass(product, company, concept_map)

    if len(variants) == 1:
        angle, content = next(iter(variants.items()))
        logger.info("Only 1 seed variant succeeded (%s) for product %s", angle, product.pk)
        llm_call = LLMCall.objects.create(
            company=company, model_name=LLM_MODEL,
            prompt_text=f"single-variant:{angle}", response_text="",
            tokens_in=0, tokens_out=0, cost_usd=0, latency_ms=0,
        )
    else:
        _set_product_generation_progress(product.pk, 3, 5, "Merge")
        content, llm_call = _merge_seed_variants(variants, concept_map, company, product)

    _set_product_generation_progress(product.pk, 4, 5, "Refinement")
    content = _refine_sections_parallel(content, concept_map, product, company, tenant_schema)

    from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS as PLK
    missing = [k for k in PLK if not content.get(k)]
    if missing:
        logger.warning(
            "Product pre-DNA incompleto per %s, sezioni mancanti: %s",
            company.schema_name,
            missing,
        )

    # A1 — editorial leakage check for product DNA
    try:
        from apps.companies.dna_validator import validate_no_editorial_leakage
        leaks = validate_no_editorial_leakage(content)
        if leaks:
            logger.error(
                "Product pre-DNA EDITORIAL LEAKAGE for %s (%s): %d fragments. First: %s",
                company.schema_name, product.name, len(leaks), leaks[0][:200],
            )
    except Exception:
        logger.exception("Editorial leakage check failed for product %s", product.pk)

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    product.dna_versions.filter(is_current=True).update(is_current=False)
    dna = ProductDNA.objects.create(
        product=product,
        version=next_version,
        dna_type=ProductDNA.TYPE_PRE,
        content=content,
    )
    _set_product_generation_progress(product.pk, 5, 5, "Generazione domande")
    return dna, llm_call


def _generate_product_dna_singlepass(product: Product, company, concept_map=None):
    """Fallback: single-pass DNA generation without multi-seed (used when all seeds fail)."""
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2) if company_dna else ""

    from apps.companies.sector_archetypes import get_archetype_context
    archetype_context = get_archetype_context(company)

    if concept_map:
        source_block = f"CONCEPT MAP:\n{json.dumps(concept_map, ensure_ascii=False, indent=2)}"
    else:
        documents = [f"# {pf.original_name}\n{pf.content_text}" for pf in product.product_files.all()[:10]]
        source_block = f"DOCUMENTI:\n{chr(10).join(documents) or 'Nessun documento.'}"

    prompt = f"""
ANALISI_NEURALE_SPECIALISTA — SINGLE PASS (fallback)

Sei ZEUS. Genera il DNA tecnico del prodotto "{product.name}" dell'azienda {company.name}.
Non stai scrivendo una scheda tecnica. Stai costruendo ARCHITETTURA COGNITIVA SPECIALIZZATA:
un sistema che insegna a un tecnico AI COME PENSARE davanti a questa famiglia prodotto.

MISSIONE: estrai GIUDIZIO, non solo fatti. Un DNA che elenca solo specifiche tecniche
e un lettore di brochure, non un fondatore. Per ogni sezione, cerca il principio
cognitivo, il trade-off, il confine, la logica decisionale che i documenti rivelano.

{source_block}

DNA AZIENDALE (contesto — eredita senza ripetere):
{company_context or "Non disponibile"}

PROFILO OPERATIVO:
{archetype_context or "Non disponibile"}

REGOLE:
- Tutte le 6 sezioni devono essere presenti e complete.
- EREDITA DAL DNA GENERALE: non ripetere principi gia stabiliti. Aggiungi SOLO
  specificita tecniche del prodotto che il Generale non copre.
- Sintetizza, non parafrasare. Riformula, collega, interpreta.
- Non assolutizzare MAI ("garantisce", "certezza assoluta").
- Se un dato non e coperto dalle fonti, scrivi "Da chiarire in intervista: ...".
- Usa i PARAMETRI della concept map per dati numerici precisi.
- Usa i GAPS per identificare "Da chiarare in intervista".

LE 6 SEZIONI TECNICHE:
1. identita_tecnica — categoria tecnica, problema risolto, posizionamento
2. architettura — materiali, struttura, componenti, costruzione fisica
3. specifiche — dimensioni, tolleranze, standard, parametri numerici
4. applicazione — installazione, uso, manutenzione, workflow operativo
5. vincoli — limiti, incompatibilita, range operativi, controindicazioni
6. configurazione — varianti, personalizzazioni, logica decisionale per custom

Output JSON con 6 chiavi: identita_tecnica, architettura, specifiche,
applicazione, vincoli, configurazione. Rispondi SOLO JSON.
""".strip()

    client = get_llm_client()
    result, content = _generate_with_retry(
        client, prompt, model=LLM_MODEL, system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2), context="product-pre-dna-fallback",
    )
    llm_call = LLMCall.objects.create(
        company=company, model_name=LLM_MODEL, prompt_text=prompt,
        response_text=result.text, tokens_in=result.tokens_in,
        tokens_out=result.tokens_out, cost_usd=result.cost, latency_ms=result.latency_ms,
    )
    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    product.dna_versions.filter(is_current=True).update(is_current=False)
    # A1 — editorial leakage check
    try:
        from apps.companies.dna_validator import validate_no_editorial_leakage
        leaks = validate_no_editorial_leakage(content)
        if leaks:
            logger.error(
                "Product singlepass EDITORIAL LEAKAGE for %s (%s): %d fragments. First: %s",
                company.schema_name, product.name, len(leaks), leaks[0][:200],
            )
    except Exception:
        logger.exception("Editorial leakage check failed for product %s", product.pk)

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    dna = ProductDNA.objects.create(
        product=product, version=next_version,
        dna_type=ProductDNA.TYPE_PRE, content=content,
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
        _set_progress(run.id, 1, 4, "Scraping sito web")

        source = run.source
        try:
            if source.status != Source.STATUS_SCRAPED:
                _set_progress(run.id, 1, 4, "Scraping sito web")
                scraper = get_scraper()
                result = scraper.scrape(source.url)
                source.scraped_data = result
                source.status = Source.STATUS_SCRAPED
                source.save(update_fields=["scraped_data", "status"])

            _set_progress(run.id, 2, 4, "Generazione Pre-DNA")
            dna, llm_call = _generate_dna(source, run.company)

            _set_progress(run.id, 3, 4, "Self-critique e validazione")

            _set_progress(run.id, 4, 4, "Completamento")
            run.status = PipelineRun.STATUS_COMPLETED
            run.current_step = "4/4: Completamento"
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
        from apps.companies.views import _create_complete_dna, _set_company_generation_progress

        try:
            company = Company.objects.get(pk=company_id)
            pre_dna = CompanyDNA.objects.get(pk=pre_dna_id)
        except (Company.DoesNotExist, CompanyDNA.DoesNotExist):
            logger.error("generate_complete_dna: company or pre_dna not found")
            return

        User = get_user_model()
        user = User.objects.filter(pk=user_id).first() if user_id else None

        try:
            _set_company_generation_progress(
                pre_dna,
                2,
                4,
                "Sintesi cognitiva globale",
                status="running",
            )
            _create_complete_dna(company, pre_dna, user)
            _set_company_generation_progress(
                pre_dna,
                4,
                4,
                "Revisione pronta",
                status="completed",
            )
            logger.info("Complete DNA generated for company %s", company.schema_name)
        except Exception as exc:
            _set_company_generation_progress(
                pre_dna,
                3,
                4,
                "Validazione DNA Generale",
                status="failed",
                error=str(exc)[:500],
            )
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
        from apps.companies.views import _create_complete_product_dna, _set_product_gap_processing

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
            _set_product_gap_processing(
                pre_dna,
                status="complete_generating",
                step_num=3,
                steps_total=4,
                step_label="Sintesi DNA Specialista completo",
            )
            _create_complete_product_dna(product, pre_dna, user)
            _set_product_gap_processing(
                pre_dna,
                status="complete_ready",
                step_num=4,
                steps_total=4,
                step_label="Revisione pronta",
            )
            logger.info("Complete product DNA generated for product %s", product.pk)
        except Exception as exc:
            _set_product_gap_processing(
                pre_dna,
                status="failed",
                error=str(exc)[:500],
                step_num=3,
                steps_total=4,
                step_label="Sintesi DNA Specialista completo",
            )
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
            _set_product_generation_progress(product.pk, 5, 5, "Generazione domande")
            _generate_product_questions(product, pre_dna)
            product.generation_step = "5/5: Domande pronte"
            product.save(update_fields=["generation_step", "updated_at"])
            logger.info("Product questions generated for product %s", product.pk)
        except Exception:
            product.generation_step = "5/5: Domande non completate"
            product.save(update_fields=["generation_step", "updated_at"])
            logger.exception(
                "Product question generation failed for product %s", product.pk
            )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task(soft_time_limit=600, time_limit=660)
def process_product_gap_round_task(
    product_id,
    pre_dna_id,
    current_round,
    user_id=None,
    tenant_schema=None,
):
    """Evaluate specialist answers outside the HTTP request to avoid 504s."""
    def _run():
        from apps.companies.views import (
            _create_product_gap_followups,
            _evaluate_product_answer_sufficiency,
            _gap_engine_product_limits,
            _plan_slug_for_company,
            _set_product_gap_processing,
        )

        try:
            product = Product.objects.get(pk=product_id)
            pre_dna = ProductDNA.objects.get(pk=pre_dna_id)
        except (Product.DoesNotExist, ProductDNA.DoesNotExist):
            logger.error("process_product_gap_round: product or pre_dna not found")
            return

        plan_slug = _plan_slug_for_company(product.company)
        limits = _gap_engine_product_limits(plan_slug)

        def _dispatch_complete():
            _set_product_gap_processing(
                pre_dna,
                round=current_round,
                status="complete_pending",
                result="complete",
                step_num=3,
                steps_total=4,
                step_label="Avvio DNA Specialista completo",
            )
            generate_complete_product_dna.delay(
                product.id,
                pre_dna.id,
                user_id,
                tenant_schema=tenant_schema,
            )

        try:
            # max_rounds = number of follow-up rounds allowed (excluding round 1).
            # If current_round > max_rounds, we have used all allowed follow-ups.
            _set_product_gap_processing(
                pre_dna,
                round=current_round,
                status="evaluating",
                step_num=2,
                steps_total=4,
                step_label="Verifica lacune e contraddizioni",
            )
            if current_round > limits["max_rounds"] + 1:
                _dispatch_complete()
                return

            answered_questions = list(
                pre_dna.questions.exclude(answer="").order_by("question_round", "id")
            )
            evaluation = _evaluate_product_answer_sufficiency(
                product, pre_dna, answered_questions, plan_slug,
            )
            followups = evaluation.get("follow_ups", [])[: limits["max_followups"]]
            if evaluation.get("overall_sufficient") or not followups:
                _dispatch_complete()
                return

            _create_product_gap_followups(
                product, pre_dna, followups, current_round, plan_slug,
            )
            _set_product_gap_processing(
                pre_dna,
                round=current_round,
                status="followups_ready",
                result="followups",
                target_round=current_round + 1,
                step_num=4,
                steps_total=4,
                step_label="Follow-up pronti",
            )
            logger.info(
                "Product gap follow-ups created for product %s round %s",
                product.pk,
                current_round,
            )
        except Exception as exc:
            logger.exception(
                "Product gap processing failed for product %s round %s; dispatching complete DNA",
                product.pk,
                current_round,
            )
            _set_product_gap_processing(
                pre_dna,
                round=current_round,
                status="complete_pending",
                result="complete",
                error=str(exc)[:500],
                step_num=3,
                steps_total=4,
                step_label="Avvio DNA Specialista completo",
            )
            generate_complete_product_dna.delay(
                product.id,
                pre_dna.id,
                user_id,
                tenant_schema=tenant_schema,
            )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task(soft_time_limit=600, time_limit=660)
def apply_specialist_feedback_task(company_id, company_dna_id, tenant_schema=None):
    """Regenerate Company DNA from approved specialist feedback (async to avoid 504)."""
    def _run():
        from apps.companies.models import Company, CompanyDNA, Product, ProductDNA
        from apps.companies.audit import compute_audit_hash

        try:
            company = Company.objects.get(pk=company_id)
            company_dna = CompanyDNA.objects.get(pk=company_dna_id)
        except (Company.DoesNotExist, CompanyDNA.DoesNotExist):
            logger.error("apply_specialist_feedback_task: company or dna not found")
            return

        pending = (company_dna.content or {}).get("_pending_specialist_feedback") or {}
        product_id = pending.get("product_id")
        specialist_dna_id = pending.get("specialist_dna_id")
        selected_proposals = pending.get("selected_proposals") or []

        if not selected_proposals or not product_id or not specialist_dna_id:
            logger.error("apply_specialist_feedback_task: incomplete pending data")
            return

        try:
            product = Product.objects.get(pk=product_id)
            specialist_dna = ProductDNA.objects.get(pk=specialist_dna_id)
        except (Product.DoesNotExist, ProductDNA.DoesNotExist):
            logger.error("apply_specialist_feedback_task: product or specialist_dna not found")
            return

        from apps.companies.views import (
            _regenerate_company_dna_from_specialist_feedback,
            _set_company_generation_progress,
        )

        _set_company_generation_progress(
            company_dna,
            2,
            4,
            "Riformulazione DNA Generale",
            status="running",
            flow="specialist_feedback",
            product_id=product.pk,
            product_name=product.name,
        )
        new_content = _regenerate_company_dna_from_specialist_feedback(
            company, product, company_dna, specialist_dna, selected_proposals,
        )
        new_content.pop("_pending_specialist_feedback", None)

        # A2 — normalize punctuation before save
        from apps.companies.dna_validator import normalize_dna_punctuation
        new_content = normalize_dna_punctuation(new_content)

        last_version = company.dna_versions.order_by("-version").first()
        next_version = (last_version.version + 1) if last_version else 1
        company.dna_versions.filter(is_current=True).update(is_current=False)

        new_dna = CompanyDNA.objects.create(
            company=company,
            version=next_version,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content=new_content,
            is_current=True,
            previous_hash=company_dna.audit_hash or "",
        )
        new_dna.audit_hash = compute_audit_hash(new_content, new_dna.previous_hash or "")
        new_dna.save(update_fields=["audit_hash"])
        _set_company_generation_progress(
            company_dna,
            4,
            4,
            "Revisione pronta",
            status="completed",
            flow="specialist_feedback",
            product_id=product.pk,
            product_name=product.name,
        )

        logger.info(
            "Specialist feedback applied: company %s DNA v%d",
            company.schema_name, next_version,
        )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task(soft_time_limit=600, time_limit=660)
def generate_product_dna_task(product_id, tenant_schema=None):
    """Generate pre-DNA (concept map → seeds → merge → refinement) + dispatch questions."""
    def _run():
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            logger.error("generate_product_dna_task: product %d not found", product_id)
            return

        company = product.company
        try:
            _set_product_generation_progress(product.pk, 1, 5, "Concept Map")
            dna, _ = _generate_product_dna(product, company, tenant_schema=tenant_schema)
            logger.info("Pre-DNA generated for product %s, dispatching questions", product.pk)
            generate_product_questions_task.delay(
                product.id, dna.id, tenant_schema=tenant_schema,
            )
        except Exception:
            logger.exception("Pre-DNA generation failed for product %s", product.pk)

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task(soft_time_limit=600, time_limit=660)
def generate_specialist_feedback_task(product_id, specialist_dna_id, company_dna_id, tenant_schema=None):
    """Generate Specialist→General feedback proposals asynchronously.

    The LLM call that compares the Specialist DNA with the Company DNA can take
    up to 2 minutes; running it synchronously in the HTTP request caused 504s
    and a terrible UX. This task stores the proposals (or an empty list when
    the Specialist adds nothing new) in the Specialist DNA content under the
    ``_feedback_proposals`` key so the GET view can serve them without any LLM
    call and the POST apply view can read them from the DB instead of session.
    """
    def _run():
        try:
            product = Product.objects.get(pk=product_id)
            specialist_dna = ProductDNA.objects.get(pk=specialist_dna_id)
            company_dna = CompanyDNA.objects.get(pk=company_dna_id)
        except (Product.DoesNotExist, ProductDNA.DoesNotExist, CompanyDNA.DoesNotExist):
            logger.error(
                "generate_specialist_feedback_task: missing product/specialist/company DNA "
                "(product=%s specialist=%s company=%s)",
                product_id, specialist_dna_id, company_dna_id,
            )
            return

        from apps.companies.views import (
            _generate_specialist_feedback_proposals,
            _set_specialist_feedback_generation,
        )

        try:
            _set_specialist_feedback_generation(
                specialist_dna,
                status="running",
                step_num=2,
                steps_total=4,
                step_label="Confronto con il DNA Generale",
            )
            proposals = _generate_specialist_feedback_proposals(
                product, specialist_dna, company_dna,
            )
            _set_specialist_feedback_generation(
                specialist_dna,
                status="running",
                step_num=4,
                steps_total=4,
                step_label="Preparazione proposte",
            )
            final_status = "completed"
        except Exception as exc:
            _set_specialist_feedback_generation(
                specialist_dna,
                status="failed",
                step_num=2,
                steps_total=4,
                step_label="Confronto con il DNA Generale",
                error=str(exc)[:500],
            )
            logger.exception(
                "Specialist feedback generation failed for product %s", product_id,
            )
            proposals = []
            final_status = "completed"

        # Persist proposals on the Specialist DNA content (not audit-hashed, like
        # _critique and _cross_specialist). Empty list is a valid result.
        specialist_dna.content["_feedback_proposals"] = proposals
        specialist_dna.content["_feedback_generation"] = {
            "status": final_status,
            "step_num": 4,
            "steps_total": 4,
            "step_label": "Proposte pronte",
            "updated_at": timezone.now().isoformat(),
        }
        specialist_dna.save(update_fields=["content"])
        logger.info(
            "Specialist feedback generated for product %s: %d proposals",
            product_id, len(proposals) if isinstance(proposals, list) else 0,
        )

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            _run()
    else:
        _run()


@shared_task(soft_time_limit=600, time_limit=660)
def run_consistency_audit(
    company_id,
    scope=ConsistencyIssue.SCOPE_PERIODIC,
    product_id=None,
    tenant_schema=None,
):
    """Run Motore C coherence audit for Company DNA and active specialists."""
    def _run():
        return _run_consistency_audit(company_id, scope=scope, product_id=product_id)

    if tenant_schema and hasattr(connection, "tenant"):
        with schema_context(tenant_schema):
            return _run()
    return _run()
