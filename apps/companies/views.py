import json
import logging
import re
import textwrap
from urllib.parse import urlparse

import fitz
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.companies.dna_numeric import (
    extract_all_layer_specs,
    extract_technical_specs,
)
from apps.companies.dna_schemas import (
    LAYER_KEYS,
    LAYER_TITLES,
    PRODUCT_LAYER_KEYS,
    PRODUCT_LAYER_TITLES,
)
from apps.companies.sector_archetypes import get_archetype_context
from apps.companies.llm_client import (
    LLM_MODEL,
    LLM_MODEL_PRO,
    ZEUS_SYSTEM_PROMPT,
    _generate_with_retry,
    _parse_llm_json,
    get_llm_client,
)
from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    CompanyQuestion,
    ConsistencyIssue,
    DNAFeedback,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
    ProductPublication,
    ProductQuestion,
    ProductSectionApproval,
    SectionApproval,
    Source,
)
from apps.companies.tasks import _generate_dna, run_pipeline, scrape_source
from apps.core.models import Plan, WorkspaceSubscription
from apps.core.views import redirect_to_workspace_or_login

logger = logging.getLogger(__name__)

DNA_GENERALE_FALLBACK_LAYER = "logica_decisionale"

# Tier limits for the Gap Engine (Motore A). The first 10 questions are round 1;
# follow-up rounds start at 2 and are capped both in number of rounds and in
# follow-up questions per round.
GAP_ENGINE_LIMITS = {
    Plan.SLUG_STARTER: {"max_rounds": 1, "max_followups": 3},
    Plan.SLUG_PROFESSIONAL: {"max_rounds": 2, "max_followups": 5},
    Plan.SLUG_ENTERPRISE: {"max_rounds": 3, "max_followups": 20},
}
GAP_ENGINE_PRODUCT_LIMITS = {
    Plan.SLUG_STARTER: {"max_rounds": 1, "max_followups": 3},
    Plan.SLUG_PROFESSIONAL: {"max_rounds": 2, "max_followups": 5},
    Plan.SLUG_ENTERPRISE: {"max_rounds": 3, "max_followups": 20},
}
SOURCE_MARKER_RE = re.compile(r"\s*\[SRC:[^\]]+\]", re.IGNORECASE)
INSTRUCTION_PREFIX_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:aggiungere|da aggiungere|integrare|inserire|"
    r"aggiornare|sostituire|riscrivere|proposta)\s*:\s*"
)
SYNTHESIS_LAYER_ALIASES = {
    "identita_e_promessa": "identita",
    "identita_funzionale": "identita",
    "postura_aziendale": "identita",
    "confini_produttivi": "confini",
    "confini_materiali": "confini",
    "limiti_operativi": "confini",
    "tono_comunicativo": "tono",
    "tono_di_voce": "tono",
    "registro_comunicativo": "tono",
}

QUESTION_GENERATION_PROFILES = {
    Plan.SLUG_STARTER: {
        "label": "Foundation - domande generiche per DNA base",
        "answer_depth": "generica",
        "instruction": (
            "Genera domande semplici e comprensibili. Devono completare un DNA "
            "Aziendale base di almeno 2 pagine. Le risposte attese possono essere "
            "sintetiche, ma devono chiarire identita, promessa, clienti, limiti e tono."
        ),
    },
    Plan.SLUG_PROFESSIONAL: {
        "label": "Professional - domande mirate su sito, file e pre-DNA",
        "answer_depth": "mirata",
        "instruction": (
            "Genera domande mirate e contestuali. Ogni domanda deve partire da una "
            "cosa specifica emersa da scraping, file caricati o pre-DNA: una lacuna, "
            "un'ambiguita, un'affermazione da verificare, un mercato, un vincolo, "
            "una prova o una contraddizione. Le risposte attese devono essere complete."
        ),
    },
    Plan.SLUG_ENTERPRISE: {
        "label": "Legacy - analisi profonda della mentalita aziendale",
        "answer_depth": "analitica",
        "instruction": (
            "Agisci come un analista aziendale senior. Genera domande profonde, non "
            "ovvie, per estrarre mentalita aziendale, filosofia decisionale, cultura, "
            "trade-off, antideriva, governance della risposta e verita non negoziabili. "
            "Le risposte attese devono essere analitiche e molto complete."
        ),
    },
}

# Motore C (Consistency Audit) tier profiles. Foundation wants a quick, focused
# check on real contradictions; Professional adds governance gaps and
# absolutizations; Legacy runs the full deep analysis including boundary cases.
CONSISTENCY_AUDIT_TIER_PROFILES = {
    Plan.SLUG_STARTER: {
        "max_issues": 5,
        "depth_instruction": (
            "Solo contraddizioni operative evidenti tra Specialisti e DNA Generale. "
            "Ignora boundary case e dettagli minori."
        ),
    },
    Plan.SLUG_PROFESSIONAL: {
        "max_issues": 8,
        "depth_instruction": (
            "Contraddizioni, assolutizzazioni (vincoli di uno specialista che "
            "rischiano di diventare legge aziendale) e buchi di governo."
        ),
    },
    Plan.SLUG_ENTERPRISE: {
        "max_issues": 15,
        "depth_instruction": (
            "Analisi completa: includi boundary case, principi stale e dettagli "
            "minori. Massima copertura."
        ),
    },
}
# Fallback when the company plan is unknown: keeps the previous behaviour
# (12 issues, no depth modifier) so the audit is backwards compatible.
CONSISTENCY_AUDIT_DEFAULT_PROFILE = {
    "max_issues": 12,
    "depth_instruction": "",
}


def _consistency_audit_profile(company):
    """Resolve the Motore C profile (max_issues + depth) for the company plan."""
    plan_slug = _plan_slug_for_company(company)
    return CONSISTENCY_AUDIT_TIER_PROFILES.get(
        plan_slug, CONSISTENCY_AUDIT_DEFAULT_PROFILE
    )


def _tenant_company(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return None
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    return company


def _workspace_block_reason(company):
    subscription = WorkspaceSubscription.objects.select_related("plan").filter(
        client__schema_name=company.schema_name,
    ).first()
    if subscription and not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    return None


def _normalize_source_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = f"https://{url}"
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if "." not in hostname and hostname != "localhost":
        return ""
    return url


def _subscription_for_company(company):
    return WorkspaceSubscription.objects.select_related("plan").filter(
        client__schema_name=company.schema_name,
    ).first()


def _plan_slug_for_company(company):
    subscription = _subscription_for_company(company)
    if not subscription or not subscription.plan:
        return Plan.SLUG_STARTER
    if subscription.plan.slug in QUESTION_GENERATION_PROFILES:
        return subscription.plan.slug
    return Plan.SLUG_STARTER


def _question_plan_label(plan_slug):
    return QUESTION_GENERATION_PROFILES.get(
        plan_slug,
        QUESTION_GENERATION_PROFILES[Plan.SLUG_STARTER],
    )["label"]


def _company_file_bytes_used(company):
    return company.company_files.aggregate(t=Sum("file_size"))["t"] or 0


def _company_file_block_reason(company):
    subscription = _subscription_for_company(company)
    if not subscription:
        return None
    current_bytes = _company_file_bytes_used(company)
    if subscription.company_files_bytes_used != current_bytes:
        subscription.company_files_bytes_used = current_bytes
        subscription.save(update_fields=["company_files_bytes_used"])
    if not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    return None


def _onboarding_context(request):
    company = _tenant_company(request)
    if not company:
        return None
    latest_source = company.sources.order_by("-created_at").first()
    latest_run = company.pipeline_runs.select_related("source").order_by("-created_at").first()
    latest_dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    sections = _dna_sections(latest_dna.content) if latest_dna else []
    review_start = request.GET.get("revise") == "1"
    run_is_pending = bool(latest_run and latest_run.status in {"running", "pending"})
    if review_start:
        step = 1
    elif latest_dna and latest_dna.dna_type == CompanyDNA.TYPE_COMPLETE:
        step = 3
    elif latest_dna:
        step = 2
    else:
        step = 1
    has_questions = company.company_questions.exists()
    context = {
        "company": company,
        "source": latest_source,
        "run": latest_run,
        "dna": latest_dna,
        "sections": sections,
        "step": step,
        "step_has_run": latest_run is not None,
        "step_has_dna": latest_dna is not None,
        "step_has_questions": has_questions,
        "show_source_form": review_start or (latest_dna is None and not run_is_pending),
        **_source_form_context(company, review_mode=review_start),
        "is_done": latest_dna is not None,
    }
    if latest_dna:
        context.update(_onboarding_dna_context(company, latest_dna))
    return context


def _dna_sections(content, old_content=None):
    sections = []
    # Pre-extract specs across all layers for efficiency
    all_specs = extract_all_layer_specs(content, LAYER_KEYS) if isinstance(content, dict) else {}
    for key in LAYER_KEYS:
        label = LAYER_TITLES[key]
        raw_value = _as_text(content.get(key) if isinstance(content, dict) else None)
        value = _strip_source_markers(raw_value)
        old_value = None
        if old_content and isinstance(old_content, dict):
            old_value = _strip_source_markers(_as_text(old_content.get(key)))
        sections.append({
            "key": key,
            "label": label,
            "value": value or "",
            "raw_value": raw_value or "",
            "paragraphs": _document_paragraphs(value),
            "old_value": old_value or "",
            "changed": bool(old_value and old_value != value),
            "specs": all_specs.get(key, []),
        })
    return sections


def _dna_public_document(content):
    """Return the client-facing Sintesi Cognitiva, never the internal layer map."""
    if not isinstance(content, dict):
        return _strip_source_markers(_as_text(content).strip())

    explicit = _strip_source_markers(_as_text(content.get("sintesi_cognitiva")).strip())
    if explicit:
        return explicit

    # Fallback for DNA generated before the public renderer existed: keep the
    # internal order but hide layer labels so the client sees a continuous text.
    paragraphs = []
    for key in LAYER_KEYS:
        text = _strip_source_markers(_as_text(content.get(key)).strip())
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _dna_final_document(content):
    """Return the complete client document as continuous conceptual text.

    The final PDF/popup must not expose internal layer titles. It combines the
    public synthesis with the generated layer text as paragraphs only.
    """
    if not isinstance(content, dict):
        return _strip_source_markers(_as_text(content).strip())

    paragraphs = []
    synthesis = _strip_source_markers(_as_text(content.get("sintesi_cognitiva")).strip())
    if synthesis:
        paragraphs.append(synthesis)

    for key in LAYER_KEYS:
        text = _strip_source_markers(_as_text(content.get(key)).strip())
        if text and text not in paragraphs:
            paragraphs.append(text)

    if paragraphs:
        return "\n\n".join(paragraphs)
    return _dna_public_document(content)


def _document_paragraphs(document):
    """Split a client document into display/PDF paragraphs.

    Rendering owns formatting; the stored text stays plain and title-free.
    Keeps natural paragraph flow: only splits truly enormous blocks so the
    browser can wrap gracefully. A professional paragraph is 6-12 lines.
    """
    text = _as_text(document).strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n+", text)
    formatted = []
    for paragraph in paragraphs:
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        # Paragraphs under ~1600 chars are fine as-is (6-10 lines on desktop)
        if len(paragraph) <= 1600:
            formatted.append(paragraph)
            continue

        # Only split truly huge blocks at sentence boundaries
        chunk = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if chunk and len(chunk) + len(sentence) + 1 > 1400:
                formatted.append(chunk)
                chunk = sentence
            else:
                chunk = f"{chunk} {sentence}".strip()
        if chunk:
            formatted.append(chunk)
    return formatted


def _compact_display_excerpt(text, limit=900):
    text = str(text or "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].strip()
    return f"{clipped}..." if clipped else text[:limit]


def _source_extract_context(source):
    if not source or not isinstance(source.scraped_data, dict):
        return {
            "analyzed_source": source,
            "source_title": "",
            "source_description": "",
            "source_excerpt": "",
        }

    data = source.scraped_data
    return {
        "analyzed_source": source,
        "source_title": _strip_source_markers(_as_text(data.get("title")).strip()),
        "source_description": _strip_source_markers(
            _as_text(data.get("description")).strip(),
        ),
        "source_excerpt": _compact_display_excerpt(data.get("markdown", "")),
    }


def _onboarding_dna_context(company, dna):
    sections = _dna_sections(dna.content)
    public_document = _dna_public_document(dna.content)
    source = company.sources.filter(status=Source.STATUS_SCRAPED).order_by(
        "-created_at",
    ).first() or company.sources.order_by("-created_at").first()
    note = company.company_files.filter(original_name="note-azienda.txt").order_by(
        "-created_at",
    ).first()
    documents = list(
        company.company_files.exclude(original_name="note-azienda.txt").order_by(
            "-created_at",
        )[:8],
    )
    context = {
        "dna": dna,
        "sections": sections,
        "has_section_values": any(section["value"] for section in sections),
        "public_document": public_document,
        "public_paragraphs": _document_paragraphs(public_document),
        "customer_notes_excerpt": (
            _compact_display_excerpt(note.content_text, 700) if note else ""
        ),
        "analyzed_documents": documents,
    }
    context.update(_source_extract_context(source))
    return context


def _as_text(value):
    if isinstance(value, list):
        parts = [_as_text(item).strip() for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        preferred_keys = (
            "descrizione",
            "description",
            "testo",
            "text",
            "contenuto",
            "content",
            "value",
        )
        for key in preferred_keys:
            if key in value:
                return _as_text(value.get(key))
        if len(value) == 1:
            return _as_text(next(iter(value.values())))
        parts = [_as_text(item).strip() for item in value.values()]
        return "\n\n".join(part for part in parts if part)
    return str(value or "")


def _strip_instruction_prefixes(text):
    cleaned = INSTRUCTION_PREFIX_RE.sub("", str(text or "")).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _sanitize_public_value(value):
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_public_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return _strip_instruction_prefixes(value)
    return value


def _public_content(content):
    if not isinstance(content, dict):
        return {}
    return {
        key: _sanitize_public_value(value)
        for key, value in content.items()
        if not str(key).startswith("_")
    }


def _strip_source_markers(text):
    return _strip_instruction_prefixes(SOURCE_MARKER_RE.sub("", str(text or "")))


def _strip_markdown(text):
    """Strip lightweight markdown formatting to plain text for PDF output.

    Handles the subset that the LLM generates in DNA fields.
    """
    text = str(text or "")
    # Remove bold/italic markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Convert markdown links to plain "text (url)"
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    # Remove heading markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Convert unordered list markers to plain dash
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    # Convert ordered list markers to keep number + dot
    text = re.sub(r"^\s*(\d+[.)])\s+", r"\1 ", text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def _section_context(content, section_key):
    if not isinstance(content, dict):
        return "Non disponibile"
    text = _as_text(content.get(section_key)).strip()
    if not text:
        return "Non disponibile"
    return text[:240]


def _compact_context_text(text, limit):
    compact = " ".join(str(text or "").split())
    return compact[:limit]


def _company_document_context(company):
    """Build a richer source context for question generation.

    The DNA question stage should not see only filenames or tiny snippets: it needs
    enough source material to detect doubts, contradictions and missing philosophy.
    We still cap the total prompt payload to keep latency and cost predictable.
    """
    max_total_chars = 14000
    blocks = []
    remaining = max_total_chars

    def add_block(title, text, per_block_limit):
        nonlocal remaining
        if remaining <= 0:
            return
        chunk = _compact_context_text(text, min(per_block_limit, remaining))
        if not chunk:
            return
        blocks.append(f"## {title}\n{chunk}")
        remaining -= len(chunk)

    source = company.sources.filter(status=Source.STATUS_SCRAPED).order_by(
        "-created_at",
    ).first()
    if source and source.scraped_data:
        add_block("Sito web scrapato", source.scraped_data.get("markdown", ""), 3500)

    note = company.company_files.filter(original_name="note-azienda.txt").order_by(
        "-created_at",
    ).first()
    if note:
        add_block("Note dirette del cliente", note.content_text, 3000)

    for company_file in company.company_files.exclude(
        original_name="note-azienda.txt",
    ).order_by("-created_at"):
        add_block(f"Documento: {company_file.original_name}", company_file.content_text, 1800)

    return "\n\n".join(blocks) or "Nessun documento aziendale caricato"


def _latest_source_url(company):
    source = company.sources.order_by("-created_at").first()
    return source.url if source else ""


def _current_company_notes(company):
    note = company.company_files.filter(
        original_name="note-azienda.txt",
    ).order_by("-created_at").first()
    return note.content_text if note else ""


def _existing_company_documents(company):
    return list(
        company.company_files.exclude(original_name="note-azienda.txt")
        .order_by("-created_at")
        .values_list("original_name", flat=True)[:8]
    )


def _source_form_context(company, *, error=None, notice=None, review_mode=False):
    return {
        "error": error,
        "notice": notice,
        "review_mode": review_mode,
        "source_url": _latest_source_url(company),
        "company_notes": _current_company_notes(company),
        "existing_documents": _existing_company_documents(company),
        "company_files": company.company_files.exclude(
            original_name="note-azienda.txt",
        ).order_by("-created_at"),
        "has_existing_dna": company.dna_versions.exists(),
        "settore_primario": company.settore_primario,
        "prodotto_fisico": company.prodotto_fisico,
        "cliente_diretto": company.cliente_diretto,
        "custom_frequenza": company.custom_frequenza,
        "installatori_in_filiera": company.installatori_in_filiera,
        "settore_secondario": company.settore_secondario,
        "contesto_libero": company.contesto_libero,
    }


def _company_files_response(request, company, error=None):
    return render(
        request,
        "core/onboarding/_company_files.html",
        {
            "company_files": company.company_files.exclude(
                original_name="note-azienda.txt",
            ).order_by("-created_at"),
            "upload_error": error,
        },
    )


def _initial_info_changed(company, url, notes, uploaded_file) -> bool:
    if not company.dna_versions.exists():
        return True
    if url != _latest_source_url(company):
        return True
    if notes.strip() != _current_company_notes(company).strip():
        return True
    if bool(uploaded_file):
        return True
    return False


def _question_generation_prompt(company, dna, plan_slug):
    profile = QUESTION_GENERATION_PROFILES[plan_slug]
    content = json.dumps(dna.content, ensure_ascii=False, indent=2)
    documents = _company_document_context(company)
    return f"""
GENERA_DOMANDE_A1_A20

Sei ZEUS. Devi generare 10 domande per il cliente DOPO aver creato un pre-DNA.
Le domande NON devono essere fisse o da template: devono nascere interpretando il
pre-DNA, lo scraping e i file caricati.

PIANO: {plan_slug}
PROFILO: {profile["label"]}
ISTRUZIONE DI PROFONDITA: {profile["instruction"]}

Regole obbligatorie:
- Genera esattamente 10 domande originali.
- Dividile in due pool obbligatori:
  - 5 domande `template`: assi cognitivi fondamentali che ogni DNA Generale deve chiarire.
  - 5 domande `kb_anchored`: domande nate da una lacuna, una contraddizione,
    un dubbio o una tensione nel sito, nei documenti, nelle note o nel pre-DNA.
- Ogni domanda deve partire da una lacuna, ambiguita, affermazione o opportunita
  che noti nel pre-DNA o nei documenti.
- Non fare domande generiche se il piano e Professional o Legacy.
- Per Legacy comportati da vero analista professionale: devi estrarre
  mentalita aziendale, filosofia decisionale e anti-deriva.
- Usa i principi A1-A10 come assi di analisi, ma scegli tu i 10 piu utili.
- Non chiedere numeri, percentuali o statistiche se non sono indispensabili: chiedi
  criteri, decisioni, confini, trade-off, filosofia produttiva e verita da confermare.
- Se ZEUS ha un dubbio, la domanda deve esplicitarlo. Non lasciare zone oscure.

GAP DETECTION — dimensioni tecniche da verificare:
Prima di generare le domande, controlla se nel pre-DNA e nei documenti sono assenti
o deboli queste dimensioni tecniche. Se lo sono, le domande kb_anchored DEVONO
esplorarle (non tutte, ma almeno quelle piu critiche per l'azienda):
- Funzioni tecniche chiave del prodotto (impermeabilita, scarico, isolamento, ecc.)
- Materiali e loro comportamento (rigido vs lavorabile, durata, compatibilita)
- Tolleranze, precisione e modalita di assorbimento
- Fissaggio, giunzioni e punti critici strutturali
- Validazione prodotto (come verificano che funziona)
- Confini produttivi (cosa non riescono a fare, soglie minime/massime)
- Approccio al custom vs standard
- Logica decisionale su scelte tecniche controverse

Rispondi SOLO JSON valido, senza markdown.

Formato JSON:
{{
  "questions": [
    {{
      "code": "A1",
      "pool": "template|kb_anchored",
      "section_key": "identita|modelli_mentali|nucleo_tecnico|confini|tono|logica_decisionale",
      "principle": "nome breve del principio usato",
      "question": "domanda al cliente",
      "answer_depth": "generica|mirata|analitica",
      "answer_guidance": "che tipo di risposta ti aspetti dal cliente"
    }}
  ]
}}

PRE-DNA:
{content}

CONTESTO ORIGINALE (SITO + NOTE + DOCUMENTI):
{documents}
""".strip()


def _parse_question_generation(text):
    payload = _parse_llm_json(text, context="question-generation")
    questions = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(questions, list) or len(questions) != 10:
        raise ValueError("LLM must return exactly 10 questions")
    return questions


def _question_pool(raw_question, index):
    pool = str(raw_question.get("pool", "")).strip()
    valid_pools = {CompanyQuestion.POOL_TEMPLATE, CompanyQuestion.POOL_KB_ANCHORED}
    if pool in valid_pools:
        return pool
    return CompanyQuestion.POOL_TEMPLATE if index < 5 else CompanyQuestion.POOL_KB_ANCHORED


def _gap_engine_limits(plan_slug):
    return GAP_ENGINE_LIMITS.get(plan_slug, GAP_ENGINE_LIMITS[Plan.SLUG_STARTER])


def _gap_engine_prompt(company, pre_dna, questions, plan_slug):
    """Build the Gap Engine prompt to evaluate answer sufficiency in batch."""
    limits = _gap_engine_limits(plan_slug)
    content = json.dumps(pre_dna.content, ensure_ascii=False, indent=2)
    qa_lines = []
    for question in questions:
        answer = (question.answer or "").strip()
        qa_lines.append(
            f"DOMANDA {question.code} [{question.section_key}] — {question.principle}\n"
            f"Q: {question.question}\n"
            f"A: {answer if answer else '[nessuna risposta]'}"
        )
    qa_block = "\n\n".join(qa_lines)

    return f"""
GAP_ENGINE_EVAL

Sei ZEUS. Hai appena ricevuto le risposte del cliente alle domande di approfondimento
per il DNA Generale. Il tuo compito è valutare la QUALITA cognitiva di ogni risposta,
non la sua lunghezza.

PIANO: {plan_slug}
LIMITE FOLLOW-UP: massimo {limits['max_followups']} domande in questa tornata.

Valuta ogni risposta secondo questi criteri:
- sufficiente: la risposta chiarisce il punto e aggiunge giudizio, confini, logica
  decisionale o contesto reale all'azienda.
- insufficiente: la risposta è troppo generica, vaga, puramente descrittiva o evita
  il punto. Serve un approfondimento mirato.
- contradicts: la risposta contradice il pre-DNA, un'altra risposta o un documento.
  Segnala il conflitto e chiedi chiarimento.

Regole per i follow-up:
- Genera SOLO domande che valgono davvero la pena. Meglio zero follow-up che domande
  inutili.
- Le domande follow-up devono essere nate da lacune, contraddizioni o ambiguita reali.
- Non ripetere domande gia poste.
- Massimo {limits['max_followups']} follow-up in questa tornata.

Output JSON esatto:
{{
  "evaluations": [
    {{
      "question_code": "A1",
      "status": "sufficiente|insufficiente|contradicts",
      "rationale": "1 frase di motivazione"
    }}
  ],
  "overall_sufficient": true|false,
  "follow_ups": [
    {{
      "target_question_code": "A1",
      "section_key": "identita|modelli_mentali|nucleo_tecnico|confini|tono|logica_decisionale",
      "principle": "nome breve del principio",
      "question": "domanda follow-up mirata",
      "answer_depth": "generica|mirata|analitica",
      "answer_guidance": "che tipo di risposta ti aspetti"
    }}
  ]
}}

Se tutte le risposte sono sufficienti, "follow_ups" deve essere un array vuoto e
"overall_sufficient" true.

Rispondi SOLO con il JSON richiesto. Nessun preambolo, nessun markdown.

PRE-DNA:
{content}

DOMANDE E RISPOSTE CLIENTE:
{qa_block}
""".strip()


def _parse_gap_evaluation(text):
    payload = _parse_llm_json(text, context="gap-engine")
    if not isinstance(payload, dict):
        raise ValueError("Gap engine response must be a JSON object")
    evaluations = payload.get("evaluations") or []
    follow_ups = payload.get("follow_ups") or []
    if not isinstance(evaluations, list) or not isinstance(follow_ups, list):
        raise ValueError("Gap engine evaluations and follow_ups must be arrays")
    for evaluation in evaluations:
        if evaluation.get("status") not in {"sufficiente", "insufficiente", "contradicts"}:
            raise ValueError(f"Invalid gap evaluation status: {evaluation.get('status')}")
    return {
        "overall_sufficient": bool(payload.get("overall_sufficient")),
        "evaluations": evaluations,
        "follow_ups": follow_ups,
    }


def _evaluate_answer_sufficiency(company, pre_dna, questions, plan_slug):
    """Run the Gap Engine over all answered questions."""
    prompt = _gap_engine_prompt(company, pre_dna, questions, plan_slug)
    client = get_llm_client()
    result, evaluation = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.4, 0.3, 0.2),
        parse=_parse_gap_evaluation,
        context="gap-engine",
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
    return evaluation


def _create_gap_followups(company, dna, followups_data, current_round, plan_slug):
    """Persist Gap Engine follow-up questions for the next round."""
    profile = QUESTION_GENERATION_PROFILES.get(plan_slug, QUESTION_GENERATION_PROFILES[Plan.SLUG_STARTER])
    existing_codes = set(dna.questions.values_list("code", flat=True))
    section_keys = set(LAYER_KEYS)
    created = []

    parent_map = {q.code: q for q in dna.questions.all()}

    for index, raw in enumerate(followups_data, start=1):
        target_code = str(raw.get("target_question_code") or "")
        section_key = str(raw.get("section_key") or DNA_GENERALE_FALLBACK_LAYER)
        if section_key not in section_keys:
            section_key = DNA_GENERALE_FALLBACK_LAYER
        code = _unique_question_code(f"F{index}", existing_codes, "F1")
        answer_depth = str(raw.get("answer_depth") or profile["answer_depth"])[:40]
        question = CompanyQuestion.objects.create(
            company=company,
            dna=dna,
            code=code,
            plan_slug=plan_slug,
            section_key=section_key,
            pool=CompanyQuestion.POOL_KB_ANCHORED,
            principle=str(raw.get("principle", "Approfondimento"))[:120],
            question=str(raw.get("question", "")).strip(),
            answer_depth=answer_depth,
            answer_guidance=str(raw.get("answer_guidance", "")).strip(),
            question_round=current_round + 1,
            parent_question=parent_map.get(target_code),
        )
        created.append(question)
    return created


def _round_questions(dna, round_number):
    return list(dna.questions.filter(question_round=round_number).order_by("id"))


def _process_answers_after_round(request, company, pre_dna, current_round):
    """Save answers, run Gap Engine, create follow-ups or trigger complete DNA."""
    plan_slug = _plan_slug_for_company(company)
    limits = _gap_engine_limits(plan_slug)

    # Collect all answered questions across rounds for the synthesis.
    answered_questions = list(
        pre_dna.questions.exclude(answer="").order_by("question_round", "id")
    )

    # max_rounds = number of follow-up rounds allowed (excluding round 1).
    # If current_round > max_rounds + 1, we have used all allowed follow-ups.
    if current_round > limits["max_rounds"] + 1:
        _trigger_complete_dna(request, company, pre_dna)
        return redirect("dna-generating")

    try:
        evaluation = _evaluate_answer_sufficiency(
            company, pre_dna, answered_questions, plan_slug
        )
    except Exception:
        logger.exception("Gap Engine evaluation failed for %s", company.schema_name)
        # Fail-safe: proceed with synthesis rather than blocking the user.
        _trigger_complete_dna(request, company, pre_dna)
        return redirect("dna-generating")

    followups = evaluation.get("follow_ups", [])[: limits["max_followups"]]
    if evaluation.get("overall_sufficient") or not followups:
        _trigger_complete_dna(request, company, pre_dna)
        return redirect("dna-generating")

    _create_gap_followups(company, pre_dna, followups, current_round, plan_slug)
    return redirect("dna-gap-questions", round_number=current_round + 1)


# --- Gap Engine for ProductDNA (Specialista) ---


def _gap_engine_product_limits(plan_slug):
    return GAP_ENGINE_PRODUCT_LIMITS.get(
        plan_slug, GAP_ENGINE_PRODUCT_LIMITS[Plan.SLUG_STARTER]
    )


def _gap_engine_product_prompt(product, pre_dna, questions, plan_slug):
    """Build the Gap Engine prompt for specialist answer sufficiency evaluation."""
    limits = _gap_engine_product_limits(plan_slug)
    content = json.dumps(_public_content(pre_dna.content), ensure_ascii=False, indent=2)
    qa_lines = []
    for question in questions:
        answer = (question.answer or "").strip()
        qa_lines.append(
            f"DOMANDA {question.code} [{question.section_key}] — {question.principle}\n"
            f"Q: {question.question}\n"
            f"A: {answer if answer else '[nessuna risposta]'}"
        )
    qa_block = "\n\n".join(qa_lines)

    return f"""
GAP_ENGINE_EVAL_SPECIALISTA

Sei ZEUS. Hai appena ricevuto le risposte del cliente alle domande di approfondimento
per il DNA Specialista di "{product.name}". Il tuo compito e valutare la QUALITA
cognitiva di ogni risposta, non la sua lunghezza.

PIANO: {plan_slug}
LIMITE FOLLOW-UP: massimo {limits['max_followups']} domande in questa tornata.

Valuta ogni risposta secondo questi criteri:
- sufficiente: la risposta chiarisce il punto e aggiunge giudizio tecnico specifico
  della famiglia prodotto, confini, logica decisionale o contesto reale.
- insufficiente: la risposta e troppo generica, vaga, puramente descrittiva o evita
  il punto. Serve un approfondimento mirato.
- contradicts: la risposta contradice il pre-DNA specialista, il DNA Generale,
  un'altra risposta o un documento. Segnala il conflitto.

Regole per i follow-up:
- Genera SOLO domande che valgono davvero la pena.
- Le domande follow-up devono nascere da lacune, contraddizioni o ambiguita reali
  specifiche della famiglia prodotto.
- Non ripetere domande gia poste.
- Massimo {limits['max_followups']} follow-up in questa tornata.

Output JSON esatto:
{{
  "evaluations": [
    {{
      "question_code": "D1",
      "status": "sufficiente|insufficiente|contradicts",
      "rationale": "1 frase di motivazione"
    }}
  ],
  "overall_sufficient": true|false,
  "follow_ups": [
    {{
      "target_question_code": "D1",
      "section_key": "identita_tecnica|architettura|specifiche|applicazione|vincoli|configurazione",
      "principle": "nome breve del principio",
      "question": "domanda follow-up mirata",
      "answer_depth": "generica|mirata|analitica",
      "answer_guidance": "che tipo di risposta ti aspetti"
    }}
  ]
}}

Se tutte le risposte sono sufficienti, "follow_ups" deve essere un array vuoto e
"overall_sufficient" true.

Rispondi SOLO con il JSON richiesto. Nessun preambolo, nessun markdown.

PRE-DNA SPECIALISTA:
{content}

DOMANDE E RISPOSTE CLIENTE:
{qa_block}
""".strip()


def _evaluate_product_answer_sufficiency(product, pre_dna, questions, plan_slug):
    """Run the Gap Engine over all answered specialist questions."""
    prompt = _gap_engine_product_prompt(product, pre_dna, questions, plan_slug)
    client = get_llm_client()
    result, evaluation = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.4, 0.3, 0.2),
        parse=_parse_gap_evaluation,
        context="product-gap-engine",
    )
    LLMCall.objects.create(
        company=product.company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
    )
    return evaluation


def _create_product_gap_followups(product, dna, followups_data, current_round, plan_slug):
    """Persist Gap Engine follow-up questions for the next specialist round."""
    profile = QUESTION_GENERATION_PROFILES.get(
        plan_slug, QUESTION_GENERATION_PROFILES[Plan.SLUG_STARTER]
    )
    existing_codes = set(dna.questions.values_list("code", flat=True))
    section_keys = set(PRODUCT_LAYER_KEYS)
    parent_map = {q.code: q for q in dna.questions.all()}

    for index, raw in enumerate(followups_data, start=1):
        target_code = str(raw.get("target_question_code") or "")
        section_key = str(raw.get("section_key") or DNA_GENERALE_FALLBACK_LAYER)
        if section_key not in section_keys:
            section_key = DNA_GENERALE_FALLBACK_LAYER
        code = _unique_question_code(f"F{index}", existing_codes, "F1")
        answer_depth = str(raw.get("answer_depth") or profile["answer_depth"])[:40]
        parent = parent_map.get(target_code)
        ProductQuestion.objects.create(
            product=product,
            dna=dna,
            code=code,
            plan_slug=plan_slug,
            section_key=section_key,
            principle=str(raw.get("principle", "Follow-up"))[:120],
            question=str(raw.get("question", "")).strip(),
            answer_depth=answer_depth,
            answer_guidance=str(raw.get("answer_guidance", "")).strip(),
            pool=ProductQuestion.POOL_KB_ANCHORED,
            question_round=current_round + 1,
            parent_question=parent,
        )
        existing_codes.add(code)


def _set_product_gap_processing(pre_dna, **data):
    content = dict(pre_dna.content) if isinstance(pre_dna.content, dict) else {}
    state = dict(content.get("_gap_processing") or {})
    state.update(data)
    state["updated_at"] = timezone.now().isoformat()
    content["_gap_processing"] = state
    pre_dna.content = content
    pre_dna.save(update_fields=["content"])
    return state


def _product_gap_processing_state(pre_dna):
    if not isinstance(pre_dna.content, dict):
        return {}
    state = pre_dna.content.get("_gap_processing") or {}
    return state if isinstance(state, dict) else {}


def _product_gap_progress_context(round_number, state):
    step_num = int(state.get("step_num") or 1)
    steps_total = int(state.get("steps_total") or 4)
    step_label = state.get("step_label") or f"Lettura risposte round {round_number}"
    phase_labels = [
        f"Lettura delle risposte del round {round_number}",
        "Verifica lacune, ambiguità e contraddizioni",
        "Generazione DNA Specialista completo",
        "Preparazione pagina revisione",
    ]
    phases = []
    for index, phase_label in enumerate(phase_labels, start=1):
        if index < step_num:
            status = "done"
        elif index == step_num:
            status = "active"
        else:
            status = "pending"
        phases.append({"label": phase_label, "status": status})
    return {
        "gap_phases": phases,
        "steps_total": steps_total,
        "current_step_num": step_num,
        "step_label": step_label,
        "progress_pct": min(int(step_num / steps_total * 100), 95),
    }


def _latest_unanswered_product_round(pre_dna, after_round=0):
    return (
        pre_dna.questions.filter(answer="", question_round__gt=after_round)
        .order_by("question_round")
        .values_list("question_round", flat=True)
        .first()
    )


def _start_product_gap_processing(request, product, pre_dna, current_round):
    latest_complete = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
    ).order_by("-version").first()
    expected_complete_version = (latest_complete.version + 1) if latest_complete else 1
    _set_product_gap_processing(
        pre_dna,
        round=current_round,
        status="running",
        expected_complete_version=expected_complete_version,
        step_num=1,
        steps_total=4,
        step_label=f"Lettura risposte round {current_round}",
        started_at=timezone.now().isoformat(),
        error="",
    )
    tenant_schema = getattr(request, "tenant", None)
    from apps.companies.tasks import process_product_gap_round_task
    process_product_gap_round_task.delay(
        product.id,
        pre_dna.id,
        current_round,
        request.user.id if request.user.is_authenticated else None,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )
    return redirect(
        "product-gap-processing",
        pk=product.id,
        round_number=current_round,
    )


def _process_product_answers_after_round(request, product, pre_dna, current_round):
    """Save specialist answers, run Gap Engine, create follow-ups or trigger complete DNA."""
    plan_slug = _plan_slug_for_company(product.company)
    limits = _gap_engine_product_limits(plan_slug)

    answered_questions = list(
        pre_dna.questions.exclude(answer="").order_by("question_round", "id")
    )

    # max_rounds = number of follow-up rounds allowed (excluding round 1).
    # If current_round > max_rounds + 1, we have used all allowed follow-ups.
    if current_round > limits["max_rounds"] + 1:
        _trigger_complete_product_dna(request, product, pre_dna)
        return redirect("product-review", pk=product.id)

    try:
        evaluation = _evaluate_product_answer_sufficiency(
            product, pre_dna, answered_questions, plan_slug
        )
    except Exception:
        logger.exception(
            "Gap Engine evaluation failed for product %s", product.name
        )
        _trigger_complete_product_dna(request, product, pre_dna)
        return redirect("product-review", pk=product.id)

    followups = evaluation.get("follow_ups", [])[: limits["max_followups"]]
    if evaluation.get("overall_sufficient") or not followups:
        _trigger_complete_product_dna(request, product, pre_dna)
        return redirect("product-review", pk=product.id)

    _create_product_gap_followups(product, pre_dna, followups, current_round, plan_slug)
    return redirect("product-gap-questions", pk=product.id, round_number=current_round + 1)


def _trigger_complete_product_dna(request, product, pre_dna):
    """Trigger async generation of the complete specialist DNA."""
    from apps.companies.tasks import generate_complete_product_dna

    latest_complete = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
    ).order_by("-version").first()
    _set_pending_complete_generation(
        request,
        (latest_complete.version + 1) if latest_complete else 1,
    )
    generate_complete_product_dna.delay(
        product.id,
        pre_dna.id,
        request.user.id if request.user.is_authenticated else None,
    )


def _trigger_complete_dna(request, company, pre_dna):
    from apps.companies.tasks import generate_complete_dna

    tenant_schema = getattr(request, "tenant", None)
    latest_complete = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
    ).order_by("-version").first()
    _set_pending_complete_generation(
        request,
        (latest_complete.version + 1) if latest_complete else 1,
        source_dna_id=pre_dna.pk,
    )
    _set_company_generation_progress(
        pre_dna,
        1,
        4,
        "Lettura risposte",
        status="running",
        flow="company_complete_dna",
    )
    generate_complete_dna.delay(
        company.id,
        pre_dna.id,
        request.user.id if request.user.is_authenticated else None,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )


def _parse_json_object(text):
    payload = _parse_llm_json(text, context="dna-json-object")
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON response must be an object")
    return payload


def _answers_by_section(questions, section_keys, fallback_section):
    answers = {key: [] for key in section_keys}
    for question in questions:
        answer = (question.answer or "").strip()
        if not answer:
            continue
        section_key = question.section_key if question.section_key in answers else fallback_section
        answers[section_key].append({
            "code": question.code,
            "principle": question.principle,
            "question": question.question,
            "answer": answer,
        })
    return answers


def _rewrite_sections_with_answers(company, content, answers_by_section, section_keys, marker):
    is_product = "PRODUCT" in marker
    entity_type = "prodotto" if is_product else "azienda"
    entity_label = "prodotto" if is_product else "azienda"

    client = get_llm_client()
    updated = dict(content)
    any_rewrite_done = False

    for section_key in section_keys:
        base_text = _as_text(content.get(section_key)).strip()
        section_answers = answers_by_section.get(section_key, [])

        prompt = f"""
RISCRIVI_SEZIONE_{section_key.upper()}_{marker}

Sei ZEUS, un filosofo tecnico e analista aziendale esperto nel settore manifatturiero.
Stai costruendo il DNA completo di un'{entity_label}. Devi riscrivere la sezione
"{section_key}" combinando due fonti:
1. Il pre-DNA generato dallo scraping del sito web e dei documenti
2. Le risposte del cliente a domande di approfondimento (conoscenza tacita)

Il risultato deve essere un testo interpretativo, filosofico e preciso: non una
scheda tecnica, non un riassunto di dati, non una brochure. Devi trasformare le
informazioni in postura, principi, confini, tensioni e logica decisionale.

LINGUA: Scrivi SEMPRE in italiano. Anche se il pre-DNA o le risposte cliente
contengono testo in inglese o altre lingue, traduci e riscrivi tutto in italiano.
Nessuna parola in inglese nel risultato finale.

ISTRUZIONI:
- Riscrivi completamente la sezione come un testo fluido e professionale.
- Usa il pre-DNA come base e le risposte cliente per arricchire, approfondire
  e dare contesto reale all'{entity_type}.
- Se una risposta corregge o contraddice il pre-DNA, dai priorita alla risposta
  del cliente (e la verita di chi conosce l'{entity_type}).
- Combina piu risposte in un unico discorso coerente.
- Puoi fare inferenze ragionevoli e collegamenti logici tra le informazioni, ma
  non inventare fatti non supportati dalle fonti.
- Non inserire numeri grezzi, percentuali, date, statistiche, quantita, KPI o
  metriche operative nel DNA Generale. Se compaiono nelle fonti, trasformali nel
  principio che rivelano.
- Se una parte resta dubbia, incompleta o contraddittoria, scrivi esplicitamente
  "Da chiarire in intervista: ..." invece di completare con finzione.
- Conserva i marker fonte gia presenti e aggiungi [SRC:answer] quando il contenuto
  nasce dalle risposte cliente. I marker servono all'enrichment interno e verranno
  rimossi dal documento pubblico.
- Non nominare mai le domande, i codici domanda (A1, D1, ecc.) ne usare frasi
  come "il cliente ha risposto", "secondo le risposte", "Approfondimenti cliente".
- Scrivi in terza persona, presente indicativo, tono tecnico ma accessibile.
- La sezione deve essere un paragrafo di 4-10 frasi che restituisca un'immagine
  ricca e professionale dell'{entity_type}.

{'- Restituisci un array JSON di 3-5 stringhe brevi (max 80 caratteri).'
  if section_key in ('modelli_mentali', 'valore')
  else '- Restituisci una singola stringa JSON.'}

Rispondi SOLO con il valore JSON (stringa o array), senza markdown,
senza chiavi, senza testo fuori dal JSON.

PRE_DNA_SEZIONE:
{base_text or 'Non disponibile'}

RISPOSTE_CLIENTE:
{json.dumps(
    section_answers, ensure_ascii=False, indent=2
) if section_answers else 'Nessuna risposta per questa sezione'}
""".strip()

        try:
            def parse_rewrite(text, key=section_key):
                value = _parse_rewrite_response(text, key)
                if value is None:
                    raise ValueError(f"LLM rewrite response invalid for {key}")
                return value

            result, rewritten_value = _generate_with_retry(
                client,
                prompt,
                model=LLM_MODEL,
                system_prompt=ZEUS_SYSTEM_PROMPT,
                temperatures=(0.5, 0.3, 0.2),
                parse=parse_rewrite,
                context=f"rewrite-{section_key}",
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
            if rewritten_value:
                updated[section_key] = rewritten_value
                any_rewrite_done = True
        except Exception:
            logger.exception(
                "DNA rewrite failed for company %s, section %s",
                company.schema_name, section_key,
            )

    if not any_rewrite_done:
        updated["rewrite_warning"] = (
            "Riformulazione LLM fallita; testo base preservato."
        )
    return updated


def _parse_rewrite_response(text, section_key):
    text = text.strip()
    if section_key in ("modelli_mentali", "valore"):
        try:
            payload = _parse_llm_json(text, context=f"rewrite-{section_key}")
        except ValueError:
            return None
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
        if isinstance(payload, str):
            return [s.strip() for s in payload.split(",") if s.strip()]
        return None
    try:
        payload = _parse_llm_json(text, context=f"rewrite-{section_key}")
    except ValueError:
        cleaned = text.strip().strip('"').strip()
        return cleaned if cleaned else None
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _generate_company_questions(company, dna):
    existing = list(dna.questions.all())
    if existing:
        return existing

    plan_slug = _plan_slug_for_company(company)
    profile = QUESTION_GENERATION_PROFILES[plan_slug]
    prompt = _question_generation_prompt(company, dna, plan_slug)
    client = get_llm_client()
    result, questions_data = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2),
        parse=_parse_question_generation,
        context="company-questions",
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

    section_keys = set(LAYER_KEYS)
    for index, raw_question in enumerate(questions_data):
        section_key = raw_question.get("section_key", DNA_GENERALE_FALLBACK_LAYER)
        if section_key not in section_keys:
            section_key = DNA_GENERALE_FALLBACK_LAYER
        code = f"A{index + 1}"
        CompanyQuestion.objects.update_or_create(
            dna=dna,
            code=code,
            defaults={
                "company": company,
                "plan_slug": plan_slug,
                "section_key": section_key,
                "pool": _question_pool(raw_question, index),
                "principle": str(raw_question.get("principle", "A1-A10"))[:120],
                "question": str(raw_question.get("question", "")).strip(),
                "answer_depth": str(
                    raw_question.get("answer_depth") or profile["answer_depth"]
                )[:40],
                "answer_guidance": str(raw_question.get("answer_guidance", "")).strip(),
                "question_round": 1,
            },
        )
    return list(dna.questions.filter(question_round=1).order_by("id"))


def _dna_in_safe_mode(dna) -> bool:
    """Return True only for approval-blocking CRITICAL conditions."""
    return bool(_safe_mode_flags(dna))


def _safe_mode_flags(dna) -> list:
    """Return CRITICAL flags that should block final approval.

    Older complete DNA versions may have a stale global schema-mismatch flag
    because their layers were rewritten as narrative paragraphs. If every
    reviewable layer has visible text, that flag is diagnostic noise, not a
    reason to block the client approval flow.
    """
    empty_layer_flags = [
        {
            "guard": "layer_completeness",
            "severity": "CRITICAL",
            "layer": section["key"],
            "message": f"Lo strato '{section['label']}' e vuoto.",
            "suggestion": "Modifica lo strato prima di approvare il DNA.",
        }
        for section in _dna_sections(dna.content)
        if not section["value"].strip()
    ]
    if empty_layer_flags:
        return empty_layer_flags

    enrichment = dna._enrichment or {}
    flags = enrichment.get("validation", {}).get("flags", [])
    return [
        f for f in flags
        if f.get("severity") == "CRITICAL"
        and not (
            f.get("guard") == "layer_completeness"
            and f.get("layer") == "global"
        )
    ]


def _format_qa_block(questions):
    lines = []
    for q in questions:
        answer = (q.answer or "").strip()
        if not answer:
            continue
        lines.append(f"DOMANDA {q.code} [{q.section_key}] — {q.principle}")
        lines.append(f"D: {q.question}")
        lines.append(f"R: {answer}")
        lines.append("")
    return "\n".join(lines) or "Nessuna risposta fornita."


def _global_dna_synthesis(company, pre_dna_content, questions):
    prev_content = _public_content(pre_dna_content)
    qa_block = _format_qa_block(questions)
    pre_dna_json = json.dumps(prev_content, ensure_ascii=False, indent=2)

    prompt = f"""SINTESI_GLOBALE_DNA

Hai un pre-DNA generato dalle fonti aziendali e le risposte del cliente a domande \
di approfondimento. Il tuo compito è LEGGERE, COMPRENDERE e RIGENERARE il DNA \
completo come documento cognitivo coerente.

REGOLE FONDAMENTALI:

1. LE RISPOSTE DEL CLIENTE SONO VINCOLANTI. Se il cliente chiarisce un punto, \
CHIUDI il dubbio. Non mantenere ambiguità precedente. Non scrivere "da chiarire" \
se il cliente ha già risposto in modo netto.

2. NON FARE PATCH. Non attaccare le risposte al pre-DNA. Leggi tutto, comprendi \
i concetti profondi, e rigenera ogni sezione come testo autonomo e coerente. \
Il risultato deve leggere come se un esperto avesse riscritto il DNA dopo aver \
studiato i materiali E intervistato il titolare.

3. SE UNA RISPOSTA CORREGGE IL PRE-DNA, la risposta del cliente prevale sempre. \
È la verità di chi conosce l'azienda.

4. NON ASSOLUTIZZARE. Mai "garantisce", "certezza assoluta", "risolve tutto". \
Ogni affermazione ha un confine di validità.

5. NON INVENTARE. Se qualcosa non è coperto né dal pre-DNA né dalle risposte, \
scrivi "Da chiarire in intervista: ..." in quel punto specifico.

6. MARCATORI FONTE: mantieni i marcatori [SRC:...] esistenti e aggiungi \
[SRC:answer] dove il contenuto nasce dalle risposte cliente. I marcatori \
NON vanno nella sintesi_cognitiva (documento pulito).

7. NUMERI E STATISTICHE: non inserire dati grezzi nel DNA. Se un numero \
appare nelle risposte, trasformalo nel principio che rivela.

OUTPUT: JSON completo con tutte le 6 sezioni cognitive + sintesi_cognitiva. \
Il formato target NON dipende dalla struttura del pre-DNA: anche se il pre-DNA \
contiene solo sintesi_cognitiva, devi produrre SEMPRE tutte le chiavi canoniche.

CHIAVI TOP-LEVEL OBBLIGATORIE, ESATTE E UNICHE:
1. sintesi_cognitiva
2. identita
3. modelli_mentali
4. nucleo_tecnico
5. confini
6. tono
7. logica_decisionale

VIETATI alias o nomi creativi: non usare identita_e_promessa, confini_produttivi, \
innovazione_e_sostenibilita, tono_comunicativo o altre varianti. Ogni sezione \
interna deve essere una stringa narrativa completa e autonoma.

REGOLA ASSOLUTA: il tuo output inizia con {{ e finisce con }}. Nessun preambolo, \
nessuna spiegazione, nessun markdown, nessun blocco ```json.

PRE-DNA COMPLETO:
{pre_dna_json}

RISPOSTE CLIENTE:
{qa_block}

Rispondi con SOLO il JSON, senza markdown, senza preambolo.""".strip()

    client = get_llm_client()
    try:
        result, rewritten = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.4, 0.3, 0.2),
            parse=_parse_json_object,
            context="global-synthesis",
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
        return _safe_merge_synthesis(prev_content, rewritten)
    except Exception:
        logger.exception(
            "Global DNA synthesis failed for %s; keeping pre-DNA",
            company.schema_name,
        )
        prev_content["rewrite_warning"] = (
            "Sintesi globale fallita; preservato il pre-DNA originale."
        )
        return prev_content


def _normalize_synthesis_layers(synthesis: dict, aliases=None) -> dict:
    """Map known LLM layer aliases to canonical 6-layer DNA keys."""
    if not isinstance(synthesis, dict):
        return {}
    normalized = dict(synthesis)
    for alias, canonical in (aliases or {}).items():
        if normalized.get(canonical):
            continue
        if normalized.get(alias):
            normalized[canonical] = normalized[alias]
            logger.warning(
                "Sintesi globale: alias '%s' normalizzato in '%s'.",
                alias,
                canonical,
            )
    return normalized


def _safe_merge_synthesis(
    original: dict,
    synthesis: dict,
    layer_keys=None,
    aliases=None,
) -> dict:
    """P5 — merge synthesis output without clobbering layers on partial output.

    Only updates a layer if the synthesis provides a non-empty value for it,
    so a partial LLM response cannot silently erase existing cognitive layers.
    """
    layer_keys = layer_keys or LAYER_KEYS
    aliases = SYNTHESIS_LAYER_ALIASES if aliases is None else aliases
    synthesis = _normalize_synthesis_layers(synthesis, aliases=aliases)
    merged = dict(original)
    missing = [key for key in layer_keys if not synthesis.get(key)]
    if missing:
        logger.warning(
            "Sintesi globale incompleta, sezioni mancanti o vuote: %s", missing,
        )
    for key in layer_keys:
        if synthesis.get(key):
            merged[key] = _sanitize_public_value(synthesis[key])
    if synthesis.get("sintesi_cognitiva"):
        merged["sintesi_cognitiva"] = _sanitize_public_value(synthesis["sintesi_cognitiva"])
    return merged


def _create_complete_dna(company, pre_dna, user):
    questions = list(pre_dna.questions.all())
    plan_slug = questions[0].plan_slug if questions else _plan_slug_for_company(company)
    content = _public_content(pre_dna.content)

    content = _global_dna_synthesis(company, content, questions)

    content["questionario_a1_a20"] = [
        {
            "code": question.code,
            "section_key": question.section_key,
            "principle": question.principle,
            "question": question.question,
            "answer": question.answer,
        }
        for question in questions
    ]
    content["profilo_questionario"] = {
        "plan": plan_slug,
        "plan_label": _question_plan_label(plan_slug),
        "starter_minimum_pages": 2 if plan_slug == Plan.SLUG_STARTER else None,
        "answer_depth": questions[0].answer_depth if questions else "generica",
    }

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    company.dna_versions.filter(is_current=True).update(is_current=False)
    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        dna_type=CompanyDNA.TYPE_COMPLETE,
        content=content,
        created_by=user if user and user.is_authenticated else None,
    )

    # PIANO 1.5 integration — self-critique + audit chain + enrichment.
    # The self-critique loop refines the 6 cognitive layers only; the extra
    # content keys (questionario, profilo) are preserved unchanged.
    _apply_self_critique(dna, company)
    _finalize_complete_dna(dna, pre_dna, company)

    # A1 — editorial leakage check on complete DNA (blocking: logs ERROR)
    try:
        from apps.companies.dna_validator import validate_no_editorial_leakage
        leaks = validate_no_editorial_leakage(dna.content)
        if leaks:
            logger.error(
                "COMPLETE DNA EDITORIAL LEAKAGE for %s: %d fragments. First: %s",
                company.schema_name, len(leaks), leaks[0][:200],
            )
    except Exception:
        logger.exception("Editorial leakage check failed for company %s", company.schema_name)

    return dna


def _apply_self_critique(dna, company):
    """Run the 2-pass self-critique loop on the 6 cognitive layers.

    Guarded: any failure falls back to the original DNA. The critique is a
    refinement, never a blocker — if the LLM hiccups, we keep the unrefined DNA.
    """
    try:
        from apps.companies.dna_critique import self_critique_dna
        from apps.companies.dna_schemas import LAYER_KEYS as LK
        from apps.companies.dna_schemas import DNAGeneraleSchema

        layer_content = {k: dna.content.get(k) for k in LK if k in dna.content}
        schema = DNAGeneraleSchema.model_validate(layer_content)
        refined, _report = self_critique_dna(schema, get_llm_client())
        # Re-apply only the 6 layers; keep the rest of content intact.
        new_content = dict(dna.content)
        new_content.update(refined.model_dump())
        # A2 — normalize punctuation on refined content
        from apps.companies.dna_validator import normalize_dna_punctuation
        new_content = normalize_dna_punctuation(new_content)
        dna.content = new_content
        dna.save(update_fields=["content"])
    except Exception:
        logger.exception("Self-critique loop failed; keeping unrefined DNA")


def _finalize_complete_dna(dna, pre_dna, company):
    """Compute enrichment + audit chain for a complete DNA (links to pre-DNA)."""
    from apps.companies.audit import compute_audit_hash
    from apps.companies.tasks import _compute_enrichment

    previous_hash = pre_dna.audit_hash or ""
    dna._enrichment = _compute_enrichment(dna.content, company, source=None)
    dna.audit_hash = compute_audit_hash(dna.content, previous_hash=previous_hash)
    dna.previous_hash = previous_hash
    dna.save(update_fields=["_enrichment", "audit_hash", "previous_hash"])


def _extract_company_file_text(uploaded_file):
    raw = uploaded_file.read()
    name = uploaded_file.name or "documento-azienda.txt"
    if name.lower().endswith(".pdf"):
        doc = fitz.open(stream=raw, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        # Fallback OCR per PDF scannerizzati
        if not text.strip():
            has_images = any(page.get_images() for page in doc)
            if has_images:
                try:
                    from pdf2image import convert_from_bytes
                    import pytesseract
                    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
                    images = convert_from_bytes(
                        raw, dpi=200, poppler_path="/usr/bin",
                        first_page=1, last_page=min(3, doc.page_count),
                    )
                    ocr_texts = []
                    for img in images:
                        ocr_texts.append(pytesseract.image_to_string(img, lang="ita+eng"))
                    text = "\n\n".join(ocr_texts)
                except Exception:
                    text = ""
        doc.close()
        return text[:30000].replace("\x00", ""), len(raw), name
    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return (
            f"Immagine caricata: {name}. Estrazione OCR/vision non ancora attiva in questo MVP.",
            len(raw),
            name,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="ignore")
    return text[:30000].replace("\x00", ""), len(raw), name


def _save_or_update_company_notes(company, notes, user):
    note = company.company_files.filter(
        original_name="note-azienda.txt",
    ).order_by("-created_at").first()
    encoded_size = len(notes.encode("utf-8"))
    if note:
        note.content_text = notes[:30000]
        note.file_size = encoded_size
        note.uploaded_by = user if user and user.is_authenticated else note.uploaded_by
        note.save(update_fields=["content_text", "file_size", "uploaded_by"])
        return
    if notes:
        CompanyFile.objects.create(
            company=company,
            original_name="note-azienda.txt",
            content_text=notes[:30000],
            file_size=encoded_size,
            uploaded_by=user if user and user.is_authenticated else None,
        )


def _save_company_file_from_request(company, request, *, replace_notes=False):
    notes = request.POST.get("company_notes", "").strip()
    uploaded_file = getattr(request, "FILES", {}).get("company_file")
    existing_note = company.company_files.filter(original_name="note-azienda.txt").exists()
    adds_new_note = bool(notes and (not replace_notes or not existing_note))
    adds_new_file = bool(uploaded_file)
    if not notes and not uploaded_file and not replace_notes:
        return None

    block_reason = _company_file_block_reason(company)
    if (adds_new_note or adds_new_file) and block_reason:
        return block_reason

    if uploaded_file:
        content_text, file_size, original_name = _extract_company_file_text(uploaded_file)
        if not content_text.strip():
            return "Il documento aziendale non contiene testo leggibile."
        CompanyFile.objects.create(
            company=company,
            original_name=original_name,
            content_text=content_text,
            file_size=file_size,
            uploaded_by=request.user if request.user.is_authenticated else None,
        )

    if replace_notes or notes:
        _save_or_update_company_notes(company, notes, request.user)

    subscription = _subscription_for_company(company)
    if subscription:
        subscription.company_files_bytes_used = _company_file_bytes_used(company)
        subscription.save(update_fields=["company_files_bytes_used"])
    return None


def _request_data(request):
    if (request.content_type or "").startswith("application/json"):
        try:
            return json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return {}
    return request.POST


def _unique_question_code(raw_code, used_codes, fallback):
    base_code = str(raw_code or fallback).strip()[:4] or fallback
    code = base_code
    counter = 2
    while code in used_codes:
        suffix = str(counter)
        code = f"{base_code[:max(1, 4 - len(suffix))]}{suffix}"[:4]
        counter += 1
    used_codes.add(code)
    return code


def _redirect_after_htmx_action(request, viewname, *args):
    url = reverse(viewname, args=args)
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def _set_pending_complete_generation(
    request,
    min_version,
    *,
    source_dna_id=None,
    return_product_id=None,
):
    if not hasattr(request, "session"):
        return
    request.session["pending_complete_min_version"] = min_version
    if source_dna_id:
        request.session["pending_complete_source_dna_id"] = source_dna_id
    if return_product_id:
        request.session["specialist_feedback_return_product_id"] = return_product_id
    if hasattr(request.session, "modified"):
        request.session.modified = True


def _pending_complete_min_version(request):
    if not hasattr(request, "session"):
        return None
    return request.session.get("pending_complete_min_version")


def _pending_complete_source_dna(request):
    if not hasattr(request, "session"):
        return None
    source_dna_id = request.session.get("pending_complete_source_dna_id")
    if not source_dna_id:
        return None
    return CompanyDNA.objects.filter(pk=source_dna_id).first()


def _clear_pending_complete_generation(request):
    if not hasattr(request, "session"):
        return
    if "pending_complete_min_version" in request.session:
        del request.session["pending_complete_min_version"]
    if "pending_complete_source_dna_id" in request.session:
        del request.session["pending_complete_source_dna_id"]
    if hasattr(request.session, "modified"):
        request.session.modified = True


def _specialist_feedback_return_product(request, company):
    if not hasattr(request, "session"):
        return None
    product_id = request.session.get("specialist_feedback_return_product_id")
    if not product_id:
        return None
    return Product.objects.filter(pk=product_id, company=company).first()


def _set_company_generation_progress(dna, step_num, steps_total, label, **extra):
    content = dict(dna.content) if isinstance(dna.content, dict) else {}
    state = dict(content.get("_complete_generation") or {})
    state.update(extra)
    state.update({
        "step_num": step_num,
        "steps_total": steps_total,
        "step_label": label,
        "updated_at": timezone.now().isoformat(),
    })
    content["_complete_generation"] = state
    dna.content = content
    dna.save(update_fields=["content"])
    return state


def _complete_generation_progress_context(source_dna):
    state = {}
    if source_dna and isinstance(source_dna.content, dict):
        raw_state = source_dna.content.get("_complete_generation") or {}
        state = raw_state if isinstance(raw_state, dict) else {}
    step_num = int(state.get("step_num") or 1)
    steps_total = int(state.get("steps_total") or 4)
    step_label = state.get("step_label") or "Lettura risposte"
    phase_labels = [
        "Lettura di tutte le risposte",
        "Sintesi cognitiva globale",
        "Riformulazione dei 6 layer",
        "Validazione e preparazione revisione",
    ]
    phases = []
    for index, phase_label in enumerate(phase_labels, start=1):
        if index < step_num:
            status = "done"
        elif index == step_num:
            status = "active"
        else:
            status = "pending"
        phases.append({"label": phase_label, "status": status})
    return {
        "task_status": "failed" if state.get("status") == "failed" else "running",
        "task_error": state.get("error", ""),
        "phases": phases,
        "steps_total": steps_total,
        "current_step_num": step_num,
        "step_label": step_label,
        "progress_pct": min(int(step_num / steps_total * 100), 95),
    }


def _clear_specialist_feedback_return(request):
    if not hasattr(request, "session"):
        return
    if "specialist_feedback_return_product_id" in request.session:
        del request.session["specialist_feedback_return_product_id"]
        if hasattr(request.session, "modified"):
            request.session.modified = True


def _action_error(message, status=400):
    return HttpResponse(message, status=status)


@login_required
def company_detail(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, created = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    return JsonResponse({
        "id": company.id,
        "name": company.name,
        "created_at": company.created_at.isoformat(),
    })


def onboarding_index(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return redirect_to_workspace_or_login(request)
    if not request.user.is_authenticated:
        return redirect("https://zeus.cais.uno/accounts/login/")

    context = _onboarding_context(request)
    if not context:
        return HttpResponse("No tenant", status=400)
    template_name = (
        "core/app_shell_onboarding.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/onboarding.html"
    )
    return render(request, template_name, context)


@login_required
@require_http_methods(["POST"])
def onboarding_source_create(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    is_htmx = request.headers.get("HX-Request") == "true"

    def _source_form_response(context, status=200):
        if is_htmx:
            return render(request, "core/onboarding/_source_form.html", context, status=status)
        page_context = _onboarding_context(request) or {"company": company, "step": 1}
        page_context.update(context)
        template_name = (
            "core/app_shell_onboarding.html"
            if settings.ZEUS_APP_SHELL_ENABLED
            else "core/onboarding.html"
        )
        return render(request, template_name, page_context, status=status)

    url = _normalize_source_url(request.POST.get("url", ""))
    if not url:
        return _source_form_response(
            _source_form_context(
                company,
                error="Inserisci un URL valido.",
                review_mode=company.dna_versions.exists(),
            ),
            status=400,
        )

    notes = request.POST.get("company_notes", "").strip()
    uploaded_file = getattr(request, "FILES", {}).get("company_file")
    has_existing_dna = company.dna_versions.exists()

    company.settore_primario = request.POST.get("settore_primario", "")
    company.prodotto_fisico = request.POST.get("prodotto_fisico") == "true"
    company.cliente_diretto = request.POST.get("cliente_diretto", "")
    company.custom_frequenza = request.POST.get("custom_frequenza", "")
    company.installatori_in_filiera = request.POST.get("installatori_in_filiera") == "true"
    company.settore_secondario = request.POST.get("settore_secondario", "").strip()
    company.contesto_libero = request.POST.get("contesto_libero", "").strip()
    company.save(update_fields=[
        "settore_primario", "prodotto_fisico", "cliente_diretto",
        "custom_frequenza", "installatori_in_filiera",
        "settore_secondario", "contesto_libero",
    ])
    if has_existing_dna and not _initial_info_changed(company, url, notes, uploaded_file):
        return _source_form_response(
            _source_form_context(
                company,
                notice=(
                    "Nessuna modifica rilevata nei dati iniziali. "
                    "Puoi continuare alle risposte senza rigenerare il pre-DNA."
                ),
                review_mode=True,
            ),
        )

    block_reason = _workspace_block_reason(company)
    if block_reason:
        return _source_form_response(
            _source_form_context(company, error=block_reason, review_mode=has_existing_dna),
            status=403,
        )

    file_error = _save_company_file_from_request(
        company,
        request,
        replace_notes=has_existing_dna,
    )
    if file_error:
        return _source_form_response(
            _source_form_context(company, error=file_error, review_mode=has_existing_dna),
            status=403,
        )

    source = Source.objects.create(company=company, url=url, status=Source.STATUS_PENDING)
    run = PipelineRun.objects.create(
        company=company,
        source=source,
        status=PipelineRun.STATUS_PENDING,
    )
    tenant_schema = getattr(request, "tenant", None)
    run_pipeline.delay(
        run.id,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )
    run.refresh_from_db()
    source.refresh_from_db()

    if not is_htmx:
        return redirect("onboarding-index")

    dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    if run.status == PipelineRun.STATUS_COMPLETED and dna:
        return render(
            request,
            "core/onboarding/_dna.html",
            _onboarding_dna_context(company, dna),
        )

    return render(request, "core/onboarding/_progress.html", {
        "run": run,
        "source": source,
    })


@login_required
def onboarding_status(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    run = PipelineRun.objects.filter(
        pk=pk, company=company,
    ).select_related("source").first()
    if not run:
        return JsonResponse({"error": "not found"}, status=404)
    dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    if request.headers.get("HX-Request") == "true":
        if run.status == PipelineRun.STATUS_COMPLETED and dna:
            return render(
                request,
                "core/onboarding/_dna.html",
                _onboarding_dna_context(company, dna),
            )
        return render(request, "core/onboarding/_progress.html", {
            "run": run,
            "source": run.source,
        })
    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "error_msg": run.error_msg,
        "source_status": run.source.status if run.source else None,
        "dna_id": dna.id if dna else None,
    })


def generation_progress(request, pk):
    """Shared HTMX progress endpoint for all generation tasks.
    
    Reads a PipelineRun by pk and returns _generation_progress.html partial.
    Expects current_step in format: \"step_num/total: Label\"
    e.g. \"3/10: Generazione layer cognitivo\"
    """
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    run = PipelineRun.objects.filter(
        pk=pk, company=company,
    ).first()
    if not run:
        return JsonResponse({"error": "not found"}, status=404)

    # Parse structured current_step
    step_num = None
    steps_total = None
    step_label = run.current_step or ""
    if ":" in step_label:
        prefix, step_label = step_label.split(":", 1)
        step_label = step_label.strip()
        if "/" in prefix:
            parts = prefix.split("/", 1)
            try:
                step_num = int(parts[0].strip())
                steps_total = int(parts[1].strip())
            except (ValueError, IndexError):
                pass

    progress_pct = 0
    if steps_total and steps_total > 0 and step_num:
        progress_pct = min(int(step_num / steps_total * 100), 100)
    elif run.status == PipelineRun.STATUS_COMPLETED:
        progress_pct = 100

    ctx = {
        "status": run.status,
        "error_msg": run.error_msg,
        "step_label": step_label,
        "progress_pct": progress_pct,
        "current_step_num": step_num,
        "steps_total": steps_total,
        "title": "Elaborazione in corso",
        "description": "ZEUS sta elaborando i dati.",
        "retry_url": request.path,
        "phases": None,
    }

    return render(request, "core/partials/_generation_progress.html", ctx)


@login_required
def onboarding_dna(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = CompanyDNA.objects.filter(
        pk=pk, company=company, is_current=True,
    ).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)
    return render(
        request,
        "core/onboarding/_dna.html",
        _onboarding_dna_context(company, dna),
    )


@login_required
@require_http_methods(["POST"])
def onboarding_dna_reset(request):
    """Cancella DNA, domande, file e source per ricominciare l'onboarding."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    company.dna_versions.all().delete()
    company.company_questions.all().delete()
    company.company_files.all().delete()
    company.pipeline_runs.all().delete()
    company.sources.all().delete()
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.company_files_bytes_used = 0
        subscription.save(update_fields=["company_files_bytes_used"])
    return redirect("onboarding-index")


@login_required
@require_http_methods(["POST"])
def onboarding_file_upload(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    uploaded_file = getattr(request, "FILES", {}).get("company_file")
    if not uploaded_file:
        return _company_files_response(request, company)

    file_size = uploaded_file.size

    block_reason = _company_file_block_reason(company)
    if block_reason:
        return _company_files_response(request, company, error=block_reason)

    content_text, extracted_size, original_name = _extract_company_file_text(uploaded_file)
    if not content_text.strip():
        return _company_files_response(
            request, company,
            error="Il documento non contiene testo leggibile.",
        )

    CompanyFile.objects.create(
        company=company,
        original_name=original_name,
        content_text=content_text,
        file_size=file_size,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.company_files_bytes_used = _company_file_bytes_used(company)
        subscription.save(update_fields=["company_files_bytes_used"])
    return _company_files_response(request, company)


def onboarding_file_delete(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    company_file = get_object_or_404(CompanyFile, pk=pk, company=company)
    company_file.delete()
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.company_files_bytes_used = _company_file_bytes_used(company)
        subscription.save(update_fields=["company_files_bytes_used"])
    if request.headers.get("HX-Request") == "true":
        return _company_files_response(request, company)
    return redirect("onboarding-index")


@login_required
def dna_current(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company = Company.objects.filter(schema_name=tenant.schema_name).first()
    if not company:
        return JsonResponse({"error": "company not found"}, status=404)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return JsonResponse({"error": "no DNA yet"}, status=404)
    return JsonResponse({
        "id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "created_at": dna.created_at.isoformat(),
        "created_by": dna.created_by.email if dna.created_by else None,
        "dna_type": dna.dna_type,
        "export_ready": dna.is_export_ready(),
    })


@login_required
def dna_history(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company = Company.objects.filter(schema_name=tenant.schema_name).first()
    if not company:
        return JsonResponse({"error": "company not found"}, status=404)
    versions = company.dna_versions.all().values(
        "id", "version", "dna_type", "is_current", "created_at", "created_by__email"
    )
    return JsonResponse(list(versions), safe=False)


@login_required
@require_http_methods(["GET", "POST"])
def source_list_create(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)

    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )

    if request.method == "GET":
        sources = company.sources.all().values(
            "id", "url", "status", "created_at", "updated_at"
        )
        return JsonResponse(list(sources), safe=False)

    block_reason = _workspace_block_reason(company)
    if block_reason:
        return JsonResponse({"error": block_reason}, status=403)

    body = json.loads(request.body)
    url = body.get("url")
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    source = Source.objects.create(
        company=company,
        url=url,
        status=Source.STATUS_PENDING,
    )

    tenant_schema = getattr(request, "tenant", None)
    scrape_source.delay(
        source.id,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )

    return JsonResponse({
        "id": source.id,
        "url": source.url,
        "status": source.status,
    }, status=201)


@login_required
def source_detail(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)

    source = Source.objects.filter(pk=pk, company__schema_name=tenant.schema_name).first()
    if not source:
        return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({
        "id": source.id,
        "url": source.url,
        "status": source.status,
        "scraped_data": source.scraped_data,
        "error_msg": source.error_msg,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
    })


@login_required
@require_http_methods(["POST"])
def dna_generate(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    block_reason = _workspace_block_reason(company)
    if block_reason:
        return JsonResponse({"error": block_reason}, status=403)

    body = json.loads(request.body)
    source_id = body.get("source_id")
    if not source_id:
        return JsonResponse({"error": "source_id is required"}, status=400)
    source = Source.objects.filter(pk=source_id, company=company).first()
    if not source:
        return JsonResponse({"error": "source not found"}, status=404)
    if source.status != Source.STATUS_SCRAPED or not source.scraped_data:
        return JsonResponse({"error": "source not scraped yet"}, status=400)

    dna, llm_call = _generate_dna(source, company)

    return JsonResponse({
        "dna_id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "dna_type": dna.dna_type,
        "export_ready": dna.is_export_ready(),
        "llm_call_id": llm_call.id,
        "tokens_in": llm_call.tokens_in,
        "tokens_out": llm_call.tokens_out,
        "cost_usd": llm_call.cost_usd,
    }, status=201)


@login_required
@require_http_methods(["POST"])
def pipeline_run_create(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    block_reason = _workspace_block_reason(company)
    if block_reason:
        return JsonResponse({"error": block_reason}, status=403)

    body = json.loads(request.body)
    source_id = body.get("source_id")
    if not source_id:
        return JsonResponse({"error": "source_id is required"}, status=400)
    source = Source.objects.filter(pk=source_id, company=company).first()
    if not source:
        return JsonResponse({"error": "source not found"}, status=404)

    run = PipelineRun.objects.create(
        company=company,
        source=source,
        status=PipelineRun.STATUS_PENDING,
    )
    tenant_schema = getattr(request, "tenant", None)
    run_pipeline.delay(
        run.id,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )

    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
    }, status=201)


@login_required
@require_http_methods(["POST"])
def dna_feedback(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    dna = CompanyDNA.objects.filter(
        pk=pk, company__schema_name=tenant.schema_name,
    ).first()
    if not dna:
        return JsonResponse({"error": "dna not found"}, status=404)

    body = json.loads(request.body)
    rating = body.get("rating")
    if not rating or not isinstance(rating, int) or rating < 1 or rating > 5:
        return JsonResponse({"error": "rating must be 1-5"}, status=400)

    feedback = DNAFeedback.objects.create(
        dna=dna,
        rating=rating,
        comment=body.get("comment", ""),
    )
    dna.confidence_score = CompanyDNA.recalculate_confidence(dna.id)
    dna.save(update_fields=["confidence_score"])

    return JsonResponse({
        "id": feedback.id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "confidence_score": dna.confidence_score,
    }, status=201)


@login_required
def pipeline_run_detail(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    run = PipelineRun.objects.filter(
        pk=pk, company__schema_name=tenant.schema_name,
    ).first()
    if not run:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "error_msg": run.error_msg,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    })


@login_required
@require_http_methods(["POST"])
def dna_create(request):

    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    block_reason = _workspace_block_reason(company)
    if block_reason:
        return JsonResponse({"error": block_reason}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    content = body.get("content")
    if not content:
        return JsonResponse({"error": "content is required"}, status=400)

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1

    # mark previous current as False
    company.dna_versions.filter(is_current=True).update(is_current=False)

    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        content=content,
        dna_type=body.get("dna_type", CompanyDNA.TYPE_PRE),
        created_by=request.user if request.user.is_authenticated else None,
    )
    return JsonResponse({
        "id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "dna_type": dna.dna_type,
        "created_at": dna.created_at.isoformat(),
    }, status=201)


@login_required
@require_http_methods(["GET", "POST"])
def dna_questions(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)

    pre_dna = company.dna_versions.filter(dna_type=CompanyDNA.TYPE_PRE).order_by("-version").first()
    complete_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not pre_dna:
        return HttpResponse("Pre-DNA not found", status=404)

    # If follow-up questions from a previous Gap Engine round are still waiting
    # for answers, send the user directly to the latest active round.
    latest_unanswered_round = (
        pre_dna.questions.filter(answer="")
        .order_by("-question_round")
        .values_list("question_round", flat=True)
        .first()
    )
    if latest_unanswered_round and latest_unanswered_round > 1:
        return redirect("dna-gap-questions", round_number=latest_unanswered_round)

    error = None
    try:
        questions = _generate_company_questions(company, pre_dna)
    except ValueError as exc:
        questions = []
        error = f"ZEUS non e riuscito a generare le domande: {exc}"
    if request.method == "POST" and not error:
        body = _request_data(request)
        missing = []
        answers_changed = False
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            if answer != question.answer:
                answers_changed = True
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande prima di generare il DNA completo."
        elif complete_dna and not answers_changed:
            return redirect("dna-review")
        else:
            return _process_answers_after_round(request, company, pre_dna, current_round=1)

    status_code = 400 if error else 200
    latest_run = company.pipeline_runs.order_by("-created_at").first()
    template_name = (
        "core/app_shell_dna_questions.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/dna_questions.html"
    )
    return render(request, template_name, {
        "company": company,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "error": error,
        "step": 2,
        "step_has_run": latest_run is not None,
        "step_has_dna": company.dna_versions.filter(is_current=True).exists(),
        "step_has_questions": True,
    }, status=status_code)


@login_required
@require_http_methods(["GET", "POST"])
def dna_gap_questions(request, round_number):
    """Round 2+ follow-up questions generated by the Gap Engine."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)

    pre_dna = company.dna_versions.filter(dna_type=CompanyDNA.TYPE_PRE).order_by("-version").first()
    if not pre_dna:
        return HttpResponse("Pre-DNA not found", status=404)

    questions = _round_questions(pre_dna, round_number)
    if not questions:
        # No follow-ups for this round; proceed to synthesis.
        return _process_answers_after_round(request, company, pre_dna, current_round=round_number)

    complete_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    error = None

    if request.method == "POST":
        body = _request_data(request)
        missing = []
        answers_changed = False
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            if answer != question.answer:
                answers_changed = True
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande di approfondimento prima di proseguire."
        elif complete_dna and not answers_changed:
            return redirect("dna-review")
        else:
            return _process_answers_after_round(request, company, pre_dna, current_round=round_number)

    status_code = 400 if error else 200
    latest_run = company.pipeline_runs.order_by("-created_at").first()
    template_name = (
        "core/app_shell_dna_gap_questions.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/dna_gap_questions.html"
    )
    return render(request, template_name, {
        "company": company,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "round_number": round_number,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "error": error,
        "step": 2,
        "step_has_run": latest_run is not None,
        "step_has_dna": company.dna_versions.filter(is_current=True).exists(),
        "step_has_questions": True,
    }, status=status_code)


@login_required
def dna_generating(request):
    """Pagina di attesa generazione DNA completo con polling HTMX."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    complete_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    min_complete_version = _pending_complete_min_version(request)
    source_dna = _pending_complete_source_dna(request)
    if min_complete_version:
        complete_dna = company.dna_versions.filter(
            dna_type=CompanyDNA.TYPE_COMPLETE,
            is_current=True,
            version__gte=min_complete_version,
        ).first()
    if request.headers.get("HX-Request") == "true":
        if complete_dna:
            _clear_pending_complete_generation(request)
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("dna-review")
            return response
        return HttpResponse(status=204)
    if complete_dna:
        _clear_pending_complete_generation(request)
        return redirect("dna-review")
    template_name = (
        "core/app_shell_dna_generating.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/dna_generating.html"
    )
    return render(request, template_name, {
        **_complete_generation_progress_context(source_dna),
        "review_url": reverse("dna-review"),
        "back_url": reverse("onboarding-index"),
    })


@login_required
def dna_review(request):
    """Pagina review DNA — mostra sezioni con stato approvazione."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)

    context = _dna_review_context(company, dna)
    context["return_product"] = _specialist_feedback_return_product(request, company)
    template_name = (
        "core/app_shell_dna_review.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/dna_review.html"
    )
    return render(request, template_name, context)


@login_required
def motore_b_report(request):
    """Dedicated Motore B page: cross-specialist consolidation."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not dna:
        return HttpResponse("DNA Generale non trovato", status=404)
    template_name = (
        "core/app_shell_motore_b.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/motore_b_legacy.html"
    )
    return render(request, template_name, {
        "company": company,
        "dna": dna,
        "cross_specialist": _cross_specialist_state(company, dna),
    })


@login_required
def dna_visualize(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)
    latest_run = company.pipeline_runs.order_by("-created_at").first()
    final_document = _dna_final_document(dna.content)
    template_name = (
        "core/app_shell_dna_visualize.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/dna_visualize.html"
    )
    return render(request, template_name, {
        "company": company,
        "dna": dna,
        "sections": _dna_sections(dna.content),
        "public_document": _dna_public_document(dna.content),
        "final_document": final_document,
        "final_paragraphs": _document_paragraphs(final_document),
        "company_name": company.name,
        "is_fully_approved": dna.is_fully_approved(),
        "step": 4,
        "step_has_run": latest_run is not None,
        "step_has_dna": True,
        "step_has_questions": company.company_questions.exists(),
    })


def _dna_review_context(company, dna):
    sections = _dna_sections(dna.content)
    missing_keys = dna.missing_sections()
    blocking_flags = []
    if not missing_keys and not dna.is_fully_approved():
        blocking_flags = _safe_mode_flags(dna)
    final_document = _dna_final_document(dna.content)
    latest_run = company.pipeline_runs.order_by("-created_at").first()
    cross_specialist = _cross_specialist_state(company, dna)

    return {
        "company": company,
        "dna": dna,
        "sections": sections,
        "public_document": _dna_public_document(dna.content),
        "public_paragraphs": _document_paragraphs(_dna_public_document(dna.content)),
        "final_document": final_document,
        "final_paragraphs": _document_paragraphs(final_document),
        "approved_keys": dna.approved_sections(),
        "missing_keys": missing_keys,
        "is_fully_approved": dna.is_fully_approved(),
        "is_export_ready": dna.is_export_ready(),
        "company_name": company.name,
        "blocking_flags": blocking_flags,
        "cross_specialist": cross_specialist,
        "consistency": _consistency_state(company),
        "step": 3,
        "step_has_run": latest_run is not None,
        "step_has_dna": True,
        "step_has_questions": company.company_questions.exists(),
    }


def _render_dna_review_fragment(request, company, dna, status=200):
    context = _dna_review_context(company, dna)
    context["return_product"] = _specialist_feedback_return_product(request, company)
    return render(
        request,
        "core/partials/dna_review_content.html",
        context,
        status=status,
    )


def _active_specialist_records(company):
    records = []
    products = company.products.filter(status=Product.STATUS_ATTIVO).order_by("name")
    for product in products:
        dna = product.dna_versions.filter(
            dna_type=ProductDNA.TYPE_COMPLETE,
            is_current=True,
        ).first()
        if not dna:
            continue
        content = dna.content if isinstance(dna.content, dict) else {}
        records.append({
            "product_id": product.pk,
            "product_name": product.name,
            "tipologia": product.tipologia or "",
            "codice": product.codice or "",
            "dna_id": dna.pk,
            "dna_version": dna.version,
            "layers": {
                key: _sanitize_public_value(content.get(key, ""))
                for key in PRODUCT_LAYER_KEYS
            },
        })
    return records


def _cross_specialist_state(company, dna):
    records = _active_specialist_records(company)
    source_dna_ids = sorted(record["dna_id"] for record in records)
    analysis = {}
    if isinstance(dna.content, dict):
        raw_analysis = dna.content.get("_cross_specialist") or {}
        analysis = raw_analysis if isinstance(raw_analysis, dict) else {}
    analysis_source_ids = sorted(analysis.get("source_dna_ids") or [])
    requires_refresh = bool(analysis and analysis_source_ids != source_dna_ids)
    proposals = analysis.get("consolidation_proposals") or []
    conflicts = analysis.get("conflicts") or []
    return {
        "active_count": len(records),
        "products": [
            {
                "product_id": record["product_id"],
                "product_name": record["product_name"],
                "dna_id": record["dna_id"],
                "dna_version": record["dna_version"],
            }
            for record in records
        ],
        "analysis": analysis,
        "has_analysis": bool(analysis),
        "requires_refresh": requires_refresh,
        "shared_patterns": analysis.get("shared_patterns") or [],
        "conflicts": conflicts,
        "has_conflicts": bool(conflicts),
        "proposals": proposals,
        "can_analyze": bool(records),
        "can_consolidate": bool(proposals) and not requires_refresh,
    }


def _consistency_state(company):
    open_issues = company.consistency_issues.filter(status=ConsistencyIssue.STATUS_OPEN)
    return {
        "open_count": open_issues.count(),
        "high_count": open_issues.filter(severity=ConsistencyIssue.SEVERITY_HIGH).count(),
        "latest": company.consistency_issues.order_by("-created_at").first(),
    }


def _mark_consistency_audit_pending(company_dna, scope, product=None):
    content = dict(company_dna.content) if isinstance(company_dna.content, dict) else {}
    content["_consistency_audit_pending"] = {
        "scope": scope,
        "requested_at": timezone.now().isoformat(),
        "product_id": product.pk if product else None,
        "product_name": product.name if product else "",
    }
    company_dna.content = content
    company_dna.save(update_fields=["content"])


def _consistency_periodic_threshold(company):
    plan_slug = _plan_slug_for_company(company)
    return {
        Plan.SLUG_STARTER: 3,
        Plan.SLUG_PROFESSIONAL: 2,
        Plan.SLUG_ENTERPRISE: 1,
    }.get(plan_slug, 3)


def _dispatch_specialist_consistency_audit(request, company, product):
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not company_dna:
        return False
    if not product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
    ).exists():
        return False

    from apps.companies.tasks import run_consistency_audit

    _mark_consistency_audit_pending(company_dna, ConsistencyIssue.SCOPE_SPECIALIST, product)
    tenant = getattr(request, "tenant", None)
    profile = _consistency_audit_profile(company)
    run_consistency_audit.delay(
        company.pk,
        scope=ConsistencyIssue.SCOPE_SPECIALIST,
        product_id=product.pk,
        tenant_schema=tenant.schema_name if tenant else None,
        max_issues=profile["max_issues"],
        depth_instruction=profile["depth_instruction"],
    )
    return True


def _maybe_trigger_product_upload_consistency_audit(request, company, product):
    if product.status != Product.STATUS_ATTIVO:
        return False
    product.status = Product.STATUS_UPDATING
    product.save(update_fields=["status"])
    try:
        return _dispatch_specialist_consistency_audit(request, company, product)
    except Exception:
        logger.exception("T2 consistency audit dispatch failed for product %s", product.pk)
        return False


def _normalize_cross_specialist_analysis(raw, company_dna, records):
    valid_layers = set(LAYER_KEYS)
    raw = raw if isinstance(raw, dict) else {}

    def _clean_items(key, max_items):
        items = raw.get(key) or []
        if not isinstance(items, list):
            return []
        return [item for item in items[:max_items] if isinstance(item, dict)]

    patterns = []
    for item in _clean_items("shared_patterns", 6):
        theme = _strip_instruction_prefixes(item.get("theme", ""))
        if not theme:
            continue
        patterns.append({
            "theme": theme,
            "evidence": _strip_instruction_prefixes(item.get("evidence", "")),
            "impact": _strip_instruction_prefixes(item.get("impact", "")),
            "source_products": item.get("source_products") or [],
        })

    conflicts = []
    for item in _clean_items("conflicts", 8):
        issue = _strip_instruction_prefixes(item.get("issue", ""))
        if not issue:
            continue
        severity = str(item.get("severity") or "medium").lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        conflicts.append({
            "severity": severity,
            "products": item.get("products") or item.get("source_products") or [],
            "issue": issue,
            "recommendation": _strip_instruction_prefixes(item.get("recommendation", "")),
        })

    proposals = []
    for item in _clean_items("consolidation_proposals", 8):
        target_layer = item.get("target_layer", "")
        proposed_value = _strip_instruction_prefixes(item.get("proposed_value", ""))
        if target_layer not in valid_layers or not proposed_value:
            continue
        proposals.append({
            "target_layer": target_layer,
            "title": _strip_instruction_prefixes(item.get("title", "")) or LAYER_TITLES[target_layer],
            "proposed_value": proposed_value,
            "rationale": _strip_instruction_prefixes(item.get("rationale", "")),
            "source_products": item.get("source_products") or [],
        })

    return {
        "generated_at": timezone.now().isoformat(),
        "company_dna_id": company_dna.pk,
        "company_dna_version": company_dna.version,
        "source_dna_ids": sorted(record["dna_id"] for record in records),
        "source_products": [
            {
                "product_id": record["product_id"],
                "product_name": record["product_name"],
                "dna_id": record["dna_id"],
                "dna_version": record["dna_version"],
            }
            for record in records
        ],
        "summary": _strip_instruction_prefixes(raw.get("summary", "")),
        "shared_patterns": patterns,
        "conflicts": conflicts,
        "consolidation_proposals": proposals,
        "status": "ready",
    }


def _fallback_cross_specialist_analysis(company_dna, records):
    proposals = []
    if records:
        product_names = [record["product_name"] for record in records]
        proposals.append({
            "target_layer": "nucleo_tecnico",
            "title": "Specialisti attivi come evidenza tecnica",
            "proposed_value": (
                "Gli specialisti attivi rendono piu esplicita la competenza tecnica "
                "dell'azienda: " + ", ".join(product_names) + "."
            ),
            "rationale": "Fallback controllato: l'analisi LLM non ha restituito proposte strutturate.",
            "source_products": product_names,
        })
    return _normalize_cross_specialist_analysis(
        {
            "summary": "Analisi specialisti costruita in modalita fallback.",
            "shared_patterns": [],
            "conflicts": [],
            "consolidation_proposals": proposals,
        },
        company_dna,
        records,
    )


def _generate_cross_specialist_analysis(company, company_dna, records):
    company_json = json.dumps(
        _sanitize_company_feedback_content(company_dna.content),
        ensure_ascii=False,
        indent=2,
    )
    specialists_json = json.dumps(records, ensure_ascii=False, indent=2)
    prompt = f"""
CROSS_SPECIALIST_ANALYSIS

Sei ZEUS. Analizza tutti i DNA Specialisti ATTIVI dell'azienda e confrontali con
il DNA Generale corrente. Questo e il Motore B: serve a capire cosa emerge solo
guardando gli specialisti insieme.

OBIETTIVI:
1. Trovare pattern comuni tra specialisti che meritano di diventare principi del DNA Generale.
2. Trovare conflitti o tensioni tra specialisti. I conflitti sono WARNING, non blocchi.
3. Proporre aggiornamenti al DNA Generale solo quando l'informazione e trasversale.

REGOLE:
- Non trasformare il DNA Generale in una scheda prodotto.
- Non proporre dettagli tecnici isolati validi per un solo specialista, salvo se rivelano una postura aziendale.
- Non usare prefissi come "Aggiungere:", "Integrare:" o "Proposta:" nel testo finale.
- Massimo 6 pattern, 8 conflitti, 8 proposte.
- Le proposte devono essere testo finale integrabile, non istruzioni operative.

DNA GENERALE CORRENTE:
{company_json}

DNA SPECIALISTI ATTIVI:
{specialists_json}

Output JSON esatto:
{{
  "summary": "sintesi breve del quadro cross-specialist",
  "shared_patterns": [
    {{
      "theme": "pattern trasversale",
      "evidence": "quali specialisti lo mostrano e come",
      "impact": "cosa cambia nel DNA Generale",
      "source_products": ["nome specialista"]
    }}
  ],
  "conflicts": [
    {{
      "severity": "low|medium|high",
      "products": ["nome specialista A", "nome specialista B"],
      "issue": "tensione o conflitto rilevato",
      "recommendation": "come segnalarlo o gestirlo senza bloccare"
    }}
  ],
  "consolidation_proposals": [
    {{
      "target_layer": "identita|modelli_mentali|nucleo_tecnico|confini|tono|logica_decisionale",
      "title": "titolo breve",
      "proposed_value": "testo finale da integrare nel DNA Generale",
      "rationale": "perche nasce dagli specialisti attivi",
      "source_products": ["nome specialista"]
    }}
  ]
}}

Rispondi SOLO JSON valido, senza markdown.
""".strip()

    client = get_llm_client()
    try:
        result, raw = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.35, 0.25, 0.15),
            parse=_parse_json_object,
            context="cross-specialist-analysis",
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
        analysis = _normalize_cross_specialist_analysis(raw, company_dna, records)
        if analysis["consolidation_proposals"] or analysis["conflicts"] or analysis["shared_patterns"]:
            return analysis
    except Exception:
        logger.exception("Cross-specialist analysis failed for company %s", company.schema_name)
    return _fallback_cross_specialist_analysis(company_dna, records)


def _selected_cross_specialist_proposals(analysis, selected_indices):
    proposals = analysis.get("consolidation_proposals") or []
    selected = []
    for idx in selected_indices:
        try:
            proposal = proposals[int(idx)]
        except (TypeError, ValueError, IndexError):
            continue
        target = proposal.get("target_layer", "")
        proposed = _strip_instruction_prefixes(proposal.get("proposed_value", ""))
        if target not in set(LAYER_KEYS) or not proposed:
            continue
        selected.append({
            "target_layer": target,
            "title": _strip_instruction_prefixes(proposal.get("title", "")),
            "proposed_value": proposed,
            "rationale": _strip_instruction_prefixes(proposal.get("rationale", "")),
            "source_products": proposal.get("source_products") or [],
        })
    return selected


def _fallback_apply_cross_specialist_proposals(current_content, selected_proposals):
    new_content = dict(current_content)
    for proposal in selected_proposals:
        target = proposal["target_layer"]
        current = _strip_instruction_prefixes(_as_text(new_content.get(target)))
        proposed = _strip_instruction_prefixes(proposal.get("proposed_value", ""))
        if not proposed:
            continue
        if proposed.lower() in current.lower():
            new_content[target] = current
        else:
            new_content[target] = f"{current}\n\n{proposed}".strip() if current else proposed
    return new_content


def _regenerate_company_dna_from_cross_specialists(
    company,
    company_dna,
    records,
    selected_proposals,
    analysis,
):
    current_content = _sanitize_company_feedback_content(company_dna.content)
    current_json = json.dumps(current_content, ensure_ascii=False, indent=2)
    specialists_json = json.dumps(records, ensure_ascii=False, indent=2)
    proposals_json = json.dumps(selected_proposals, ensure_ascii=False, indent=2)
    conflicts_json = json.dumps(analysis.get("conflicts") or [], ensure_ascii=False, indent=2)

    prompt = f"""
CONSOLIDA_DNA_GENERALE_DA_SPECIALISTI

Sei ZEUS. Devi rigenerare il DNA Generale integrando le proposte cross-specialist
approvate dall'utente. Usa i DNA Specialisti attivi come evidenza, ma il risultato
deve restare un DNA Generale: principi trasversali, non dettagli di catalogo.

REGOLE:
1. Non applicare patch testuali. Riscrivi le sezioni interessate in modo fluido.
2. Non usare mai etichette operative come "Aggiungere:", "Integrare:", "Proposta:".
3. I conflitti sono warning: non inventare una soluzione se richiede scelta umana.
4. Se una proposta vale solo per uno specialista, integrala solo come esempio di postura generale.
5. Mantieni le sezioni non toccate pulite e sostanzialmente invariate.

OUTPUT JSON completo con queste chiavi esatte:
- sintesi_cognitiva
- identita
- modelli_mentali
- nucleo_tecnico
- confini
- tono
- logica_decisionale

DNA GENERALE ATTUALE:
{current_json}

DNA SPECIALISTI ATTIVI:
{specialists_json}

PROPOSTE CROSS-SPECIALIST APPROVATE:
{proposals_json}

WARNING CONFLITTI DA NON NASCONDERE:
{conflicts_json}

Rispondi SOLO JSON valido, senza markdown.
""".strip()

    client = get_llm_client()
    try:
        result, rewritten = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.35, 0.25, 0.15),
            parse=_parse_json_object,
            context="cross-specialist-consolidation",
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
        merged = _safe_merge_synthesis(current_content, rewritten)
    except Exception:
        logger.exception(
            "Cross-specialist consolidation failed for company %s; applying cleaned fallback",
            company.schema_name,
        )
        merged = _fallback_apply_cross_specialist_proposals(current_content, selected_proposals)

    merged["_cross_specialist"] = {
        **analysis,
        "status": "applied",
        "applied_at": timezone.now().isoformat(),
        "applied_proposals": selected_proposals,
    }
    return merged


@login_required
@require_http_methods(["POST"])
def dna_cross_specialist_analyze(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not dna:
        return HttpResponse("DNA Generale non trovato", status=404)
    records = _active_specialist_records(company)
    # Decision 2B: cross-specialist consolidation (Motore B) requires at least
    # 2 active specialists. True cross-specialist insight emerges from
    # comparison; a single specialist is handled by the manual feedback flow.
    if len(records) < 2:
        messages.info(
            request,
            "Il Motore B richiede almeno 2 specialisti attivi per il "
            "consolidamento cross-specialist.",
        )
        return redirect("dna-review")

    analysis = _generate_cross_specialist_analysis(company, dna, records)
    content = dict(dna.content) if isinstance(dna.content, dict) else {}
    content["_cross_specialist"] = analysis
    dna.content = content
    try:
        from apps.companies.audit import compute_audit_hash
        dna.audit_hash = compute_audit_hash(content, dna.previous_hash or "")
        dna.save(update_fields=["content", "audit_hash"])
    except Exception:
        logger.exception("Cross-specialist audit refresh failed for DNA %s", dna.pk)
        dna.save(update_fields=["content"])
    return redirect("dna-review")


@login_required
@require_http_methods(["POST"])
def dna_cross_specialist_apply(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    old_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not old_dna:
        return HttpResponse("DNA Generale non trovato", status=404)

    analysis = (old_dna.content or {}).get("_cross_specialist") if isinstance(old_dna.content, dict) else None
    if not isinstance(analysis, dict) or not analysis.get("consolidation_proposals"):
        return redirect("dna-review")

    selected_proposals = _selected_cross_specialist_proposals(
        analysis,
        request.POST.getlist("selected_proposals"),
    )
    if not selected_proposals:
        return redirect("dna-review")

    records = _active_specialist_records(company)
    source_ids = sorted(record["dna_id"] for record in records)
    if source_ids != sorted(analysis.get("source_dna_ids") or []):
        return redirect("dna-review")

    new_content = _regenerate_company_dna_from_cross_specialists(
        company,
        old_dna,
        records,
        selected_proposals,
        analysis,
    )

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
        created_by=request.user if request.user.is_authenticated else None,
        previous_hash=old_dna.audit_hash or "",
    )
    from apps.companies.audit import compute_audit_hash
    from apps.companies.tasks import _compute_enrichment
    new_dna._enrichment = _compute_enrichment(new_content, company, source=None)
    new_dna.audit_hash = compute_audit_hash(new_content, new_dna.previous_hash or "")
    new_dna.save(update_fields=["_enrichment", "audit_hash"])
    return redirect("dna-review")


@login_required
def consistency_report(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    issues = company.consistency_issues.select_related(
        "company_dna", "product", "product_dna",
    )
    context = {
        "company": company,
        "company_dna": company_dna,
        "audit_pending": (
            company_dna.content.get("_consistency_audit_pending")
            if company_dna and isinstance(company_dna.content, dict)
            else None
        ),
        "open_issues": issues.filter(status=ConsistencyIssue.STATUS_OPEN).order_by("-created_at"),
        "closed_issues": issues.exclude(status=ConsistencyIssue.STATUS_OPEN).order_by("-created_at")[:30],
        "latest": company.consistency_issues.order_by("-created_at").first(),
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "core/partials/consistency_report_content.html", context)
    template_name = (
        "core/app_shell_consistency_report.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/consistency_report.html"
    )
    return render(request, template_name, context)


@login_required
@require_http_methods(["POST"])
def consistency_audit_run(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    company_dna = company.dna_versions.filter(dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True).first()
    if not company_dna:
        return HttpResponse("DNA Generale non trovato", status=404)
    from apps.companies.tasks import run_consistency_audit

    _mark_consistency_audit_pending(company_dna, ConsistencyIssue.SCOPE_PERIODIC)
    tenant = getattr(request, "tenant", None)
    profile = _consistency_audit_profile(company)
    run_consistency_audit.delay(
        company.pk,
        scope=ConsistencyIssue.SCOPE_PERIODIC,
        tenant_schema=tenant.schema_name if tenant else None,
        max_issues=profile["max_issues"],
        depth_instruction=profile["depth_instruction"],
    )
    return redirect("consistency-report")


@login_required
@require_http_methods(["POST"])
def consistency_issue_action(request, pk, action):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    issue = get_object_or_404(ConsistencyIssue, pk=pk, company=company)
    action_map = {
        "ignore": ConsistencyIssue.STATUS_IGNORED,
        "resolve": ConsistencyIssue.STATUS_RESOLVED,
        "archive": ConsistencyIssue.STATUS_ARCHIVED,
    }
    if action not in action_map:
        return HttpResponse("Azione non valida", status=400)
    issue.status = action_map[action]
    issue.resolved_at = timezone.now()
    issue.save(update_fields=["status", "resolved_at", "updated_at"])
    return redirect("consistency-report")


@login_required
@require_http_methods(["POST"])
def product_consistency_check(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    if not product.dna_versions.filter(dna_type=ProductDNA.TYPE_COMPLETE, is_current=True).exists():
        return HttpResponse("DNA specialista non trovato", status=404)
    if not _dispatch_specialist_consistency_audit(request, company, product):
        return HttpResponse("DNA Generale non trovato", status=404)
    return redirect("consistency-report")


@login_required
@require_http_methods(["POST"])
def dna_section_approve(request, pk, section_key):
    """Approva una sezione specifica del DNA."""
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    dna = CompanyDNA.objects.filter(pk=pk, company=company, is_current=True).first()
    if not dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in LAYER_KEYS:
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = _request_data(request)
    comment = body.get("comment", "")
    is_clarification = str(body.get("is_clarification", False)).lower() == "true"

    if is_clarification:
        SectionApproval.objects.create(
            dna=dna,
            section_key=section_key,
            approved_by=request.user,
            comment=comment,
            is_clarification=True,
        )
    else:
        SectionApproval.objects.update_or_create(
            dna=dna,
            section_key=section_key,
            is_clarification=False,
            defaults={
                "approved_by": request.user,
                "comment": comment,
            },
        )
        # Check if all sections approved
        dna.refresh_from_db()
        if not dna.missing_sections():
            from apps.companies.tasks import _compute_enrichment

            dna._enrichment = _compute_enrichment(dna.content, company, source=None)
            dna.save(update_fields=["_enrichment"])
            # PIANO 1.5: safe_mode blocks final approval. A DNA with a CRITICAL
            # validation flag (e.g. a whole layer empty) cannot be approved until
            # the issue is resolved — it must be edited first.
            if _dna_in_safe_mode(dna):
                if request.headers.get("HX-Request") == "true":
                    return _render_dna_review_fragment(request, company, dna)
                return JsonResponse({
                    "error": "safe_mode",
                    "detail": "Risolvi i flag CRITICAL prima di approvare il DNA.",
                    "flags": _safe_mode_flags(dna),
                }, status=409)
            dna.is_approved = timezone.now()
            dna.save(update_fields=["is_approved"])

    if is_clarification and request.headers.get("HX-Request"):
        return HttpResponse(
            '<span class="rounded-xl bg-amber-400/10 px-3 py-2 text-sm '
            'text-amber-300">Richiesta inviata ✓</span>'
        )

    if request.headers.get("HX-Request") == "true":
        return _render_dna_review_fragment(request, company, dna)

    return JsonResponse({
        "section_key": section_key,
        "is_clarification": is_clarification,
        "approved": not is_clarification,
        "is_fully_approved": dna.is_fully_approved(),
        "missing_sections": dna.missing_sections(),
    })


@login_required
@require_http_methods(["POST"])
def dna_section_edit(request, pk, section_key):
    """Modifica una sezione → nuovo DNA v+1 con approvazioni trasferite (opzione B)."""
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    old_dna = CompanyDNA.objects.filter(pk=pk, company=company, is_current=True).first()
    if not old_dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in LAYER_KEYS:
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = _request_data(request)
    new_text = body.get("text", "").strip()
    if not new_text:
        if request.headers.get("HX-Request") == "true":
            return _action_error("Testo sezione obbligatorio.", status=400)
        return JsonResponse({"error": "text is required"}, status=400)

    # Build new content with modified section
    content = dict(old_dna.content) if isinstance(old_dna.content, dict) else {}
    content[section_key] = new_text

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    # Mark old current as False
    company.dna_versions.filter(is_current=True).update(is_current=False)

    # Create new DNA v+1
    new_dna = CompanyDNA.objects.create(
        company=company,
        version=old_dna.version + 1,
        dna_type=old_dna.dna_type,
        content=content,
        created_by=request.user,
    )

    # PIANO 1.5: recompute enrichment + link audit chain to the previous version.
    from apps.companies.audit import compute_audit_hash
    from apps.companies.tasks import _compute_enrichment
    previous_hash = old_dna.audit_hash or ""
    new_dna._enrichment = _compute_enrichment(content, company, source=None)
    new_dna.audit_hash = compute_audit_hash(content, previous_hash=previous_hash)
    new_dna.previous_hash = previous_hash
    new_dna.save(update_fields=["_enrichment", "audit_hash", "previous_hash"])

    # Transfer approvals from old DNA to new DNA (Option B)
    for approval in old_dna.section_approvals.all():
        if approval.section_key != section_key:
            SectionApproval.objects.create(
                dna=new_dna,
                section_key=approval.section_key,
                approved_by=approval.approved_by,
                comment=approval.comment,
                is_clarification=approval.is_clarification,
            )

    if request.headers.get("HX-Request") == "true":
        return _render_dna_review_fragment(request, company, new_dna)

    return JsonResponse({
        "dna_id": new_dna.id,
        "version": new_dna.version,
        "section_key": section_key,
        "transferred_approvals": [
            {
                "section_key": a.section_key,
                "approved_by": a.approved_by.email if a.approved_by else None,
            }
            for a in new_dna.section_approvals.all()
        ],
        "missing_sections": new_dna.missing_sections(),
    })


@login_required
def dna_download_pdf(request):
    """Download PDF for the current DNA (pre-DNA or complete)."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)

    pdf_bytes = _render_dna_pdf(company, dna, _dna_final_document(dna.content))
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="DNA_Generale.pdf"'
    return response


def _render_dna_pdf(company, dna, final_document):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    margin = 54
    y = 54

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        y = 54

    def write(text, size=11, color=(0, 0, 0), gap=6, width=92):
        nonlocal y
        for line in textwrap.wrap(str(text), width=width) or [""]:
            if y > 780:
                new_page()
            page.insert_text((margin, y), line, fontsize=size, fontname="helv", color=color)
            y += size + 5
        y += gap

    def write_paragraphs(document, size=10.5, color=(0.08, 0.08, 0.08), width=88):
        nonlocal y
        paragraphs = _document_paragraphs(document) or ["Non disponibile"]
        paragraphs = [_strip_markdown(p) for p in paragraphs]
        for index, paragraph in enumerate(paragraphs):
            if index and y > 760:
                new_page()
            for line in textwrap.wrap(paragraph, width=width) or [""]:
                if y > 780:
                    new_page()
                page.insert_text((margin, y), line, fontsize=size, fontname="helv", color=color)
                y += size + 5
            y += 10

    write("DNA Aziendale", size=24, color=(0.02, 0.18, 0.32), gap=10, width=60)
    write(company.name, size=14, color=(0.18, 0.18, 0.18), gap=4)
    approved_at = dna.is_approved.strftime("%d/%m/%Y %H:%M") if dna.is_approved else "n/d"
    write(
        f"Versione {dna.version} · Approvato il {approved_at}",
        size=9,
        color=(0.35, 0.35, 0.35),
        gap=18,
    )

    write_paragraphs(final_document)

    return doc.tobytes()


def _product_dna_sections(content, old_content=None):
    sections = []
    # Pre-extract specs for all product layers
    all_specs = extract_all_layer_specs(content, PRODUCT_LAYER_KEYS) if isinstance(content, dict) else {}
    for key in PRODUCT_LAYER_KEYS:
        label = PRODUCT_LAYER_TITLES[key]
        raw_value = _as_text(content.get(key) if isinstance(content, dict) else None)
        value = _strip_source_markers(raw_value)
        old_value = None
        if old_content and isinstance(old_content, dict):
            old_value = _strip_source_markers(_as_text(old_content.get(key)))
        sections.append({
            "key": key,
            "label": label,
            "value": value or "",
            "raw_value": raw_value or "",
            "old_value": old_value or "",
            "changed": bool(old_value and old_value != value),
            "specs": all_specs.get(key, []),
        })
    return sections


def _product_document_context(product):
    snippets = []
    for product_file in product.product_files.all()[:3]:
        text = " ".join(product_file.content_text.split())[:220]
        if text:
            snippets.append(f"{product_file.original_name}: {text}")
    return " | ".join(snippets) or "Nessun documento prodotto caricato"


def _product_document_context_rich(product, max_files=5, max_chars=1200):
    """Richer document context for question generation.

    The question prompt needs enough source text to anchor kb_anchored questions
    to specific passages. The standard _product_document_context truncates too
    aggressively (3 files x 220 chars) for the LLM to spot gaps and tensions.
    """
    snippets = []
    for product_file in product.product_files.all()[:max_files]:
        text = " ".join(product_file.content_text.split())[:max_chars]
        if text:
            snippets.append(f"[{product_file.original_name}]\n{text}")
    return "\n\n---\n\n".join(snippets) or "Nessun documento prodotto caricato"


def _product_question_generation_prompt(product, dna, plan_slug):
    profile = QUESTION_GENERATION_PROFILES[plan_slug]
    content = json.dumps(dna.content, ensure_ascii=False, indent=2)
    documents = _product_document_context_rich(product)
    company = product.company
    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = ""
    if company_dna:
        company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2)
    archetype_context = get_archetype_context(company)

    return f"""
GENERA_DOMANDE_D1_D20

Sei ZEUS. Devi generare 10 domande per il cliente DOPO aver creato un pre-DNA specialista.
Le domande NON devono essere fisse o da template: devono nascere interpretando il
pre-DNA specialista, i file caricati e il DNA Generale di riferimento.

PIANO: {plan_slug}
PROFILO: {profile["label"]}
ISTRUZIONE DI PROFONDITA: {profile["instruction"]}

METODO COMBINAZIONALE (esegui prima di generare le domande):
Prima di scrivere le domande, ragiona cosi:

1. FATTI SPECIALISTA: Quali sono i fatti tecnici chiave del pre-DNA specialista?
   Quali lacune, ambiguita, o affermazioni generiche noti?

2. FATTI AZIENDA: Quali sono i principi, confini, e logiche del DNA Generale?

3. COMBINAZIONE: Dove si incrociano i due livelli?
   - Un vincolo tecnico dello specialista contraddice o arricchisce un confine aziendale?
   - La configurazione custom riflette la logica decisionale generale?
   - Le specifiche tecniche rivelano qualcosa sul nucleo tecnico aziendale?
   - Cosa sa chi lavora con questo prodotto che il DNA Generale non cattura?

4. GENERAZIONE: Usa le combinazioni identificate per generare domande che
   NON chiedono solo "qual e il valore" ma che rivelano conoscenza operativa
   che collega il prodotto specifico alla postura aziendale generale.

Regole obbligatorie:
- Genera esattamente 10 domande originali.
- Ogni domanda deve partire da una lacuna, ambiguita, affermazione o opportunita
  che noti nel pre-DNA specialista o nei documenti.
- Non fare domande generiche se il piano e Professional o Legacy.
- Per Legacy comportati da vero analista professionale: devi estrarre
  logica applicativa, vincoli tecnici, valore differenziante.
- Usa i 6 layer tecnici come assi di analisi (identita_tecnica, architettura,
  specifiche, applicazione, vincoli, configurazione), ma scegli tu i 10 piu utili.
- DUE POOL DI DOMANDE:
  - Pool "template": 5 domande ancorate ai 6 layer tecnici.
    Nascono dal pre-DNA e dal DNA Generale, non dai file specifici.
    Devono esplorare i collegamenti tra specialista e generale.
  - Pool "kb_anchored": 3 domande che nascono leggendo i file specifici
    della famiglia prodotto (brochure, disegni, manuali). Queste sono le
    piu preziose: cacciano giudizio tecnico che il sito non rivela.
  - Pool "meta": 2 domande ispirate alle DOMANDE META UNIVERSALI e alle
    CATEGORIE DI CONOSCENZA TACITA del settore rilevato. Queste domande
    cercano conoscenza operativa che NON compare nei documenti.
- Rispondi SOLO JSON valido, senza markdown.

Formato JSON:
{{
  "questions": [
    {{
      "code": "D1",
      "pool": "template|kb_anchored|meta",
      "section_key": "identita_tecnica|architettura|specifiche|applicazione|vincoli|configurazione",
      "principle": "nome breve del principio usato",
      "question": "domanda al cliente",
      "answer_depth": "generica|mirata|analitica",
      "answer_guidance": "che tipo di risposta ti aspetti dal cliente"
    }}
  ]
}}

PRE-DNA SPECIALISTA:
{content}

DOCUMENTI / NOTE SPECIALISTA:
{documents}

DNA GENERALE DI RIFERIMENTO (se disponibile):
{company_context}

CONTESTO SETTORIALE E CONOSCENZA TACITA:
{archetype_context}
""".strip()


def _parse_product_question_generation(text):
    payload = _parse_llm_json(text, context="product-question-generation")
    questions = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(questions, list) or len(questions) != 10:
        raise ValueError("LLM must return exactly 10 questions")
    return questions


def _generate_product_questions(product, dna):
    existing = list(dna.questions.all())
    if existing:
        return existing

    plan_slug = _plan_slug_for_company(product.company)
    profile = QUESTION_GENERATION_PROFILES[plan_slug]
    prompt = _product_question_generation_prompt(product, dna, plan_slug)
    client = get_llm_client()
    result, product_questions = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.5, 0.3, 0.2),
        parse=_parse_product_question_generation,
        context="product-questions",
    )
    LLMCall.objects.create(
        company=product.company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
    )

    with transaction.atomic():
        locked_dna = ProductDNA.objects.select_for_update().get(pk=dna.pk)
        existing = list(locked_dna.questions.all())
        if existing:
            return existing

        section_keys = set(PRODUCT_LAYER_KEYS)
        used_codes = set()
        for raw_question in product_questions:
            section_key = raw_question.get("section_key", "identita_tecnica")
            if section_key not in section_keys:
                section_key = "identita_tecnica"
            code = _unique_question_code(raw_question.get("code"), used_codes, "D?")
            pool = raw_question.get("pool", ProductQuestion.POOL_TEMPLATE)
            if pool not in (ProductQuestion.POOL_TEMPLATE, ProductQuestion.POOL_KB_ANCHORED):
                pool = ProductQuestion.POOL_TEMPLATE
            ProductQuestion.objects.create(
                dna=locked_dna,
                code=code,
                product=product,
                plan_slug=plan_slug,
                section_key=section_key,
                principle=str(raw_question.get("principle", "D1-D20"))[:120],
                question=str(raw_question.get("question", "")).strip(),
                answer_depth=str(
                    raw_question.get("answer_depth") or profile["answer_depth"]
                )[:40],
                answer_guidance=str(raw_question.get("answer_guidance", "")).strip(),
                pool=pool,
            )
        return list(locked_dna.questions.all())


def _global_product_dna_synthesis(product, pre_dna_content, questions):
    """Global synthesis for specialist DNA — rewrite all 6 layers with answers."""
    prev_content = _public_content(pre_dna_content)
    qa_block = _format_qa_block(questions)
    pre_dna_json = json.dumps(prev_content, ensure_ascii=False, indent=2)

    # Include CompanyDNA as context (eredita, non ripete)
    company_dna = product.company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_dna_json = ""
    if company_dna:
        company_dna_json = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    prompt = f"""SINTESI_GLOBALE_DNA_SPECIALISTA

Hai un pre-DNA specialista generato dalle fonti prodotto e le risposte del cliente.
Il tuo compito e LEGGERE, COMPRENDERE e RIGENERARE il DNA Specialista completo come
documento tecnico coerente per il prodotto "{product.name}".

REGOLE FONDAMENTALI:

1. LE RISPOSTE DEL CLIENTE SONO VINCOLANTI. Se il cliente chiarisce un punto,
   CHIUDI il dubbio.
2. NON FARE PATCH. Rigenera ogni sezione come testo autonomo e coerente.
3. SE UNA RISPOSTA CORREGGE IL PRE-DNA, la risposta prevale sempre.
4. NON ASSOLUTIZZARE. Mai "garantisce", "certezza assoluta".
5. NON INVENTARE. Se qualcosa non e coperto, scrivi "Da chiarire in intervista: ...".
6. EREDITA DAL DNA GENERALE: quando un principio del DNA Generale si applica
   a questo specialista, DICHIARALO esplicitamente e spiega COME si manifesta
   in questo prodotto specifico. Non ripetere il principio a vuoto: colleghialo
   a una specifica tecnica concreta. Aggiungi poi SOLO le specificita che il
   Generale non copre.

OUTPUT: JSON completo con ESATTAMENTE queste 6 chiavi tecniche.
Il formato target NON dipende dalla struttura del pre-DNA: devi produrre SEMPRE
tutte le chiavi canoniche.

CHIAVI TOP-LEVEL OBBLIGATORIE, ESATTE E UNICHE:
1. identita_tecnica
2. architettura
3. specifiche
4. applicazione
5. vincoli
6. configurazione

VIETATI alias o nomi creativi. Ogni sezione deve essere una stringa narrativa
tecnica completa e autonoma.

REGOLA ASSOLUTA: il tuo output inizia con {{ e finisce con }}. Nessun preambolo,
nessuna spiegazione, nessun markdown, nessun blocco ```json.

PRE-DNA SPECIALISTA:
{pre_dna_json}

RISPOSTE CLIENTE:
{qa_block}

DNA GENERALE DI RIFERIMENTO (principi trasversali — NON ripetere):
{company_dna_json or "Nessun DNA Generale disponibile."}

Rispondi con SOLO il JSON, senza markdown, senza preambolo.""".strip()

    client = get_llm_client()
    try:
        result, rewritten = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.4, 0.3, 0.2),
            parse=_parse_json_object,
            context="global-product-synthesis",
        )
        LLMCall.objects.create(
            company=product.company,
            model_name=LLM_MODEL_PRO,
            prompt_text=prompt,
            response_text=result.text,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost,
            latency_ms=result.latency_ms,
        )
        return _safe_merge_synthesis(
            prev_content,
            rewritten,
            layer_keys=PRODUCT_LAYER_KEYS,
            aliases={},
        )
    except Exception:
        logger.exception(
            "Global product DNA synthesis failed for %s; keeping pre-DNA",
            product.name,
        )
        prev_content["rewrite_warning"] = (
            "Sintesi globale fallita; preservato il pre-DNA originale."
        )
        return prev_content


def _apply_product_self_critique(dna, product):
    """Self-critique: LLM reviews specialist DNA and proposes improvements (accept/reject)."""
    import json as _json
    layers = {k: dna.content.get(k, "") for k in PRODUCT_LAYER_KEYS}
    layers_json = _json.dumps(layers, ensure_ascii=False, indent=2)

    source_snippets = []
    for pf in product.product_files.all()[:5]:
        snippet = pf.content_text[:800].replace("\x00", "")
        source_snippets.append(f"[{pf.original_name}]\n{snippet}")
    sources_text = "\n\n---\n\n".join(source_snippets) if source_snippets else "Nessun documento di origine disponibile."

    prompt = f"""SELF_CRITIQUE_SPECIALISTA

Sei ZEUS. Hai generato il DNA Specialista per "{product.name}".
Rivedi criticamente ogni sezione tecnica e proponi miglioramenti dove necessario.

PER OGNI SEZIONE VALUTA:
1. Vuota o "Da chiarire": contenuto insufficiente
2. Generica: mancano dati numerici, specifiche precise, quantificazioni
3. Contraddittoria: incoerenza tra questa sezione e le altre
4. Incompletezza: informazioni parziali che i documenti potrebbero completare

ANTI-MEMORIZATION (critico):
Per ogni sezione, confronta il testo del DNA con i DOCUMENTI DI ORIGINE.
- PARAFRASI: il DNA copia intere frasi del documento, mantiene lo stesso
  ordine delle informazioni e non aggiunge interpretazione. Se oltre meta
  del testo di una sezione riproduce frasi dei documenti quasi identiche,
  e parafrasi.
- SINTESI: il DNA riformula con parole proprie, collega dati di documenti
  diversi, estrae principi cognitivi, aggiunge prospettiva tecnica.

Se una sezione e parafrasi, proponi una riformulazione che SINTETIZZI:
riorganizza le informazioni, collega dati di documenti diversi, aggiungi
interpretazione tecnica. Segnala con "anti_memorization": true.

REGOLE:
- Proponi SOLO sezioni con problemi reali (massimo 4 proposte, le piu critiche).
- Non ripetere sezioni gia adequate.
- Se tutto e adeguato, ritorna array vuoto.
- La proposta deve integrare e migliorare, non inventare dati non presenti nei documenti.

DNA SPECIALISTA:
{layers_json}

DOCUMENTI DI ORIGINE:
{sources_text}

Output JSON:
{{
  "proposals": [
    {{
      "section_key": "identita_tecnica|architettura|specifiche|applicazione|vincoli|configurazione",
      "issue": "descrizione concisa del problema",
      "anti_memorization": false,
      "proposed_text": "testo migliorato completo per la sezione"
    }}
  ]
}}

Rispondi SOLO JSON, senza markdown.
""".strip()

    client = get_llm_client()

    def _parse_critique(text):
        data = _json.loads(text)
        raw = data.get("proposals", [])
        valid_keys = set(PRODUCT_LAYER_KEYS)
        return [
            p for p in raw
            if p.get("section_key") in valid_keys and p.get("proposed_text", "").strip()
        ]

    try:
        result, proposals = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.4, 0.3, 0.2),
            parse=_parse_critique,
            context="product-self-critique",
        )
    except Exception:
        logger.exception("Self-critique failed for product DNA %s", dna.id)
        return

    LLMCall.objects.create(
        company=product.company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
    )

    if proposals:
        dna.content["_critique"] = [
            {**p, "status": "pending"} for p in proposals
        ]
        dna.save(update_fields=["content"])


def _product_available_sources(product):
    """Build the available_sources dict for specialist evidence checks."""
    company = product.company
    has_scrape = company.sources.filter(status=Source.STATUS_SCRAPED).exists()
    has_note = product.product_files.exists()
    files = [pf.original_name for pf in product.product_files.all()[:10]]
    return {"scrape": has_scrape, "note": has_note, "files": files}


def _finalize_complete_product_dna(dna, pre_dna, product):
    """Compute audit chain + enrichment for specialist DNA."""
    try:
        from apps.companies.audit import compute_audit_hash
        from apps.companies.product_enrichment import build_product_enrichment

        prev = product.dna_versions.filter(
            dna_type=ProductDNA.TYPE_COMPLETE,
        ).exclude(id=dna.id).order_by("-version").first()

        dna.previous_hash = prev.audit_hash if prev else ""
        dna.audit_hash = compute_audit_hash(dna.content, dna.previous_hash or "")

        # Specialist enrichment: technical validation + scoring + evidence.
        # Uses dedicated guards (numeric density, boundary precision, etc.)
        # instead of the cognitive guards built for the DNA Generale.
        available = _product_available_sources(product)
        dna._enrichment = build_product_enrichment(dna.content, available_sources=available)
        dna.save(update_fields=["audit_hash", "previous_hash", "_enrichment"])
    except Exception:
        logger.exception("Audit/enrichment failed for product DNA %s", dna.id)


def _create_complete_product_dna(product, pre_dna, user):
    questions = list(pre_dna.questions.all())
    plan_slug = questions[0].plan_slug if questions else _plan_slug_for_company(product.company)
    content = _public_content(pre_dna.content)

    content = _global_product_dna_synthesis(product, content, questions)

    content["questionario_d1_d20"] = [
        {
            "code": question.code,
            "section_key": question.section_key,
            "principle": question.principle,
            "question": question.question,
            "answer": question.answer,
        }
        for question in questions
    ]
    content["profilo_questionario"] = {
        "plan": plan_slug,
        "plan_label": _question_plan_label(plan_slug),
        "answer_depth": questions[0].answer_depth if questions else "generica",
    }

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    product.dna_versions.filter(is_current=True).update(is_current=False)
    dna = ProductDNA.objects.create(
        product=product,
        version=next_version,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content=content,
        created_by=user if user and user.is_authenticated else None,
    )

    # PIANO 4 — self-critique + audit chain + enrichment (duplicato temporaneo)
    _apply_product_self_critique(dna, product)
    _finalize_complete_product_dna(dna, pre_dna, product)

    # A1 — editorial leakage check on complete product DNA
    try:
        from apps.companies.dna_validator import validate_no_editorial_leakage
        leaks = validate_no_editorial_leakage(dna.content)
        if leaks:
            logger.error(
                "COMPLETE PRODUCT DNA EDITORIAL LEAKAGE for %s (%s): %d fragments. First: %s",
                product.company.schema_name, product.name, len(leaks), leaks[0][:200],
            )
    except Exception:
        logger.exception("Editorial leakage check failed for product %s", product.pk)

    # Transition status to in_validazione (DNA complete, ready for review)
    product.status = Product.STATUS_IN_VALIDAZIONE
    product.save(update_fields=["status"])

    return dna


def _product_approval_block_reasons(dna):
    """Return CRITICAL enrichment flags that block specialist DNA approval."""
    empty_layer_flags = [
        {
            "guard": "layer_completeness",
            "severity": "CRITICAL",
            "layer": section["key"],
            "message": f"Lo strato '{section['label']}' e vuoto.",
            "suggestion": "Modifica lo strato prima di approvare il DNA.",
        }
        for section in _product_dna_sections(dna.content)
        if not section["value"].strip()
    ]
    if empty_layer_flags:
        return empty_layer_flags

    enrichment = dna._enrichment or {}
    flags = enrichment.get("validation", {}).get("flags", [])
    return [
        f for f in flags
        if f.get("severity") == "CRITICAL"
        and not (
            f.get("guard") == "layer_completeness"
            and f.get("layer") == "global"
        )
    ]


def _product_file_bytes_used(product):
    return sum(f.file_size or 0 for f in product.product_files.all())


def _product_file_block_reason(product):
    subscription = _subscription_for_company(product.company)
    if not subscription:
        return None
    if not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    return None


def _product_block_reason(company):
    subscription = _subscription_for_company(company)
    if not subscription:
        return None
    current_count = company.products.count()
    if subscription.product_dnas_used != current_count:
        subscription.product_dnas_used = current_count
        subscription.save(update_fields=["product_dnas_used"])
    if not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    if not subscription.can_add_product_dna():
        return "Limite prodotti raggiunto per il piano attuale."
    return None


@login_required
def product_list_create(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)

    template_name = (
        "core/app_shell_products.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_list.html"
    )
    products = company.products.all()

    if request.method == "POST":
        block_reason = _product_block_reason(company)
        if block_reason:
            return render(request, template_name, {
                "company": company,
                "products": products,
                "error": block_reason,
            }, status=403)

        name = request.POST.get("name", "").strip()
        if not name:
            return render(request, template_name, {
                "company": company,
                "products": products,
                "error": "Nome prodotto obbligatorio.",
            }, status=400)

        from django.utils.text import slugify
        slug = slugify(name)
        if Product.objects.filter(company=company, slug=slug).exists():
            return render(request, template_name, {
                "company": company,
                "products": products,
                "error": "Prodotto con questo nome gia esistente.",
            }, status=400)

        tipologia = request.POST.get("tipologia", "").strip()
        codice = request.POST.get("codice", "").strip()
        if codice and Product.objects.filter(company=company, codice=codice).exists():
            return render(request, template_name, {
                "company": company,
                "products": products,
                "error": "Codice gia in uso per un altro specialista.",
            }, status=400)

        Product.objects.create(
            company=company,
            name=name,
            slug=slug,
            tipologia=tipologia,
            codice=codice,
            status=Product.STATUS_BOZZA,
        )
        subscription = _subscription_for_company(company)
        if subscription:
            subscription.product_dnas_used = company.products.count()
            subscription.save(update_fields=["product_dnas_used"])
        products = company.products.all()

    return render(request, template_name, {
        "company": company,
        "products": products,
    })


@login_required
@require_http_methods(["POST"])
def product_delete(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Specialista non trovato", status=404)
    product_name = product.name
    product.delete()
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.product_dnas_used = company.products.count()
        subscription.save(update_fields=["product_dnas_used"])
    if not _wants_json(request):
        return redirect("product-list-create")
    return JsonResponse({"status": "ok", "deleted": product_name})


def _wants_json(request):
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _product_detail_context(product, error=None):
    dna = product.dna_versions.filter(is_current=True).first()
    sections = _product_dna_sections(dna.content) if dna else []
    product_files = list(product.product_files.all())
    company_dna = CompanyDNA.objects.filter(company=product.company, is_current=True).first()
    return {
        "product": product,
        "dna": dna,
        "sections": sections,
        "product_files": product_files,
        "product_files_count": len(product_files),
        "is_updating": product.status == Product.STATUS_UPDATING,
        "product_step": 1,
        "error": error,
        "company_dna": company_dna,
    }


def _product_generation_progress_context(product, pre_dna=None):
    step_label = product.generation_step or "1/5: Concept Map"
    step_num = 1
    steps_total = 5
    label = step_label
    if ":" in step_label:
        prefix, label = step_label.split(":", 1)
        label = label.strip()
        if "/" in prefix:
            try:
                raw_step, raw_total = prefix.split("/", 1)
                step_num = int(raw_step.strip())
                steps_total = int(raw_total.strip())
            except ValueError:
                pass
    progress_pct = min(int(step_num / steps_total * 100), 100) if steps_total else 0
    phase_labels = [
        "Concept Map — estrazione entità e parametri",
        "Multi-seed — 3 angoli di lettura paralleli",
        "Merge — unificazione delle varianti",
        "Refinement — 6 sezioni raffinate indipendentemente",
        "Domande — preparazione intervista tecnica",
    ]
    phases = []
    for index, phase_label in enumerate(phase_labels, start=1):
        if index < step_num:
            status = "done"
        elif index == step_num:
            status = "active"
        else:
            status = "pending"
        phases.append({"label": phase_label, "status": status})
    if pre_dna and pre_dna.questions.exists():
        phases = [{**phase, "status": "done"} for phase in phases]
        progress_pct = 100
        step_num = steps_total
        label = "Domande pronte"
    return {
        "product": product,
        "task_status": "running",
        "product_phases": phases,
        "steps_total": steps_total,
        "current_step_num": step_num,
        "step_label": label,
        "progress_pct": progress_pct,
    }


def _product_detail_template_name():
    return (
        "core/app_shell_product_detail.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_detail.html"
    )


@login_required
def product_detail(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    generating = request.GET.get("generating") == "1"
    pre_dna = product.dna_versions.filter(dna_type=ProductDNA.TYPE_PRE).order_by("-version").first()
    if generating and product.product_files.exists():
        if pre_dna and pre_dna.questions.exists():
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("product-questions", args=[product.pk])
                return response
            return redirect("product-questions", pk=product.pk)
        template_name = (
            "core/app_shell_product_dna_loading.html"
            if settings.ZEUS_APP_SHELL_ENABLED
            else "core/product_dna_loading.html"
        )
        return render(
            request,
            template_name,
            _product_generation_progress_context(product, pre_dna),
        )

    return render(request, _product_detail_template_name(), _product_detail_context(product))


@login_required
@require_http_methods(["POST"])
def product_file_upload(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    block_reason = _product_file_block_reason(product)
    if block_reason:
        if not _wants_json(request):
            return render(
                request,
                _product_detail_template_name(),
                _product_detail_context(product, block_reason),
                status=403,
            )
        return JsonResponse({"error": block_reason}, status=403)

    notes = request.POST.get("notes", "").strip()
    uploaded_file = request.FILES.get("file")

    if not notes and not uploaded_file:
        if not _wants_json(request):
            return render(
                request,
                _product_detail_template_name(),
                _product_detail_context(product, "File o note obbligatori."),
                status=400,
            )
        return JsonResponse({"error": "File o note obbligatori"}, status=400)

    if uploaded_file:
        content_text, file_size, original_name = _extract_company_file_text(uploaded_file)
        if notes:
            content_text = f"{content_text}\n\nNote aggiuntive:\n{notes}".strip()
    else:
        content_text = notes[:30000]
        file_size = len(content_text.encode("utf-8"))
        original_name = "note-prodotto.txt"

    if not content_text.strip():
        if not _wants_json(request):
            return render(
                request,
                _product_detail_template_name(),
                _product_detail_context(product, "Contenuto vuoto."),
                status=400,
            )
        return JsonResponse({"error": "Contenuto vuoto"}, status=400)

    subscription = _subscription_for_company(company)
    ProductFile.objects.create(
        product=product,
        original_name=original_name,
        content_text=content_text,
        file_size=file_size,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )

    if subscription:
        subscription.product_files_bytes_used = _product_file_bytes_used(product)
        subscription.save(update_fields=["product_files_bytes_used"])

    _maybe_trigger_product_upload_consistency_audit(request, company, product)

    if not _wants_json(request):
        return redirect("product-detail", pk=product.pk)
    return JsonResponse({"status": "ok", "files_count": product.product_files.count()})


@login_required
@require_http_methods(["POST"])
def product_file_delete(request, pk, file_pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    product_file = product.product_files.filter(pk=file_pk).first()
    if not product_file:
        return HttpResponse("File non trovato", status=404)
    product_file.delete()
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.product_files_bytes_used = _product_file_bytes_used(product)
        subscription.save(update_fields=["product_files_bytes_used"])
    if not _wants_json(request):
        return redirect("product-detail", pk=product.pk)
    return JsonResponse({"status": "ok", "files_count": product.product_files.count()})


@login_required
@require_http_methods(["POST"])
def product_dna_generate(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    block_reason = _workspace_block_reason(company)
    if block_reason:
        if not _wants_json(request):
            return render(
                request,
                _product_detail_template_name(),
                _product_detail_context(product, block_reason),
                status=403,
            )
        return JsonResponse({"error": block_reason}, status=403)

    if not product.product_files.exists():
        error = "Carica almeno un file o una nota prodotto prima di generare il pre-DNA."
        if not _wants_json(request):
            return render(
                request,
                _product_detail_template_name(),
                _product_detail_context(product, error),
                status=400,
            )
        return JsonResponse({"error": error}, status=400)

    from apps.companies.tasks import generate_product_dna_task
    tenant_schema = getattr(request, "tenant", None)
    product.status = Product.STATUS_IN_COSTRUZIONE
    product.generation_step = "1/5: Concept Map"
    product.save(update_fields=["status", "generation_step", "updated_at"])
    generate_product_dna_task.delay(
        product.id,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )

    if not _wants_json(request):
        return redirect(f"{reverse('product-detail', args=[product.pk])}?generating=1")
    return JsonResponse({
        "status": "generating",
        "product_id": product.pk,
    }, status=202)


@login_required
@require_http_methods(["GET", "POST"])
def product_questions(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    pre_dna = product.dna_versions.filter(dna_type=ProductDNA.TYPE_PRE).order_by("-version").first()
    complete_dna = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not pre_dna:
        return HttpResponse("Pre-DNA prodotto non trovato", status=404)

    # If follow-up questions from a previous Gap Engine round are still waiting
    # for answers, send the user directly to the latest active round.
    latest_unanswered_round = (
        pre_dna.questions.filter(answer="")
        .order_by("-question_round")
        .values_list("question_round", flat=True)
        .first()
    )
    if latest_unanswered_round and latest_unanswered_round > 1:
        return redirect("product-gap-questions", pk=product.id, round_number=latest_unanswered_round)

    error = None
    questions = list(pre_dna.questions.filter(question_round=1).order_by("id"))

    if not questions:
        template_name = (
            "core/app_shell_product_questions_loading.html"
            if settings.ZEUS_APP_SHELL_ENABLED
            else "core/product_questions_loading.html"
        )
        return render(request, template_name, {
            "product": product,
            "pre_dna": pre_dna,
            "product_step": 2,
        })

    if request.headers.get("HX-Request") == "true" and request.method == "GET":
        response = HttpResponse("")
        response["HX-Redirect"] = request.get_full_path()
        return response

    if request.method == "POST" and not error:
        body = _request_data(request)
        missing = []
        answers_changed = False
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            if answer != question.answer:
                answers_changed = True
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande prima di generare il DNA completo."
        elif complete_dna and not answers_changed:
            return redirect("product-review", pk=product.id)
        else:
            return _start_product_gap_processing(
                request, product, pre_dna, current_round=1
            )

    status_code = 400 if error else 200
    template_name = (
        "core/app_shell_product_questions.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_questions.html"
    )
    return render(request, template_name, {
        "product": product,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "product_step": 2,
        "error": error,
    }, status=status_code)


@login_required
def product_gap_questions(request, pk, round_number):
    """Round 2+ follow-up questions for specialist DNA (Gap Engine)."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    pre_dna = product.dna_versions.filter(dna_type=ProductDNA.TYPE_PRE).order_by("-version").first()
    if not pre_dna:
        return HttpResponse("Pre-DNA prodotto non trovato", status=404)

    questions = _round_questions(pre_dna, round_number)
    if not questions:
        return _start_product_gap_processing(
            request, product, pre_dna, current_round=round_number
        )

    complete_dna = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    error = None

    if request.method == "POST":
        body = _request_data(request)
        missing = []
        answers_changed = False
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            if answer != question.answer:
                answers_changed = True
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande di approfondimento prima di proseguire."
        elif complete_dna and not answers_changed:
            return redirect("product-review", pk=product.id)
        else:
            return _start_product_gap_processing(
                request, product, pre_dna, current_round=round_number
            )

    status_code = 400 if error else 200
    template_name = (
        "core/app_shell_product_gap_questions.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_gap_questions.html"
    )
    return render(request, template_name, {
        "product": product,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "round_number": round_number,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "error": error,
        "product_step": 2,
    }, status=status_code)


@login_required
def product_gap_processing(request, pk, round_number):
    """Wait for async specialist Gap Engine processing."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    pre_dna = product.dna_versions.filter(dna_type=ProductDNA.TYPE_PRE).order_by("-version").first()
    if not pre_dna:
        return HttpResponse("Pre-DNA prodotto non trovato", status=404)

    state = _product_gap_processing_state(pre_dna)
    next_round = _latest_unanswered_product_round(pre_dna, after_round=round_number)
    if next_round:
        target = reverse("product-gap-questions", args=[product.id, next_round])
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)

    expected_version = state.get("expected_complete_version")
    complete_qs = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
    )
    if expected_version:
        complete_qs = complete_qs.filter(version__gte=expected_version)
    complete_dna = complete_qs.first()
    if complete_dna:
        target = reverse("product-review", args=[product.id])
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)

    progress_context = _product_gap_progress_context(round_number, state)
    template_name = (
        "core/app_shell_product_gap_processing.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_gap_processing.html"
    )
    return render(request, template_name, {
        "product": product,
        "pre_dna": pre_dna,
        "round_number": round_number,
        "state": state,
        "product_step": 2,
        "task_status": "failed" if state.get("status") == "failed" else "running",
        "task_error": state.get("error"),
        **progress_context,
    })


@login_required
def product_review(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    dna = product.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA prodotto non trovato", status=404)
    critique_proposals = [
        (i, p) for i, p in enumerate(dna.content.get("_critique", []))
        if isinstance(p, dict) and p.get("status", "pending") == "pending"
    ]
    consistency_open_count = ConsistencyIssue.objects.filter(
        company=company,
        product=product,
        status=ConsistencyIssue.STATUS_OPEN,
    ).count()
    publications = product.publications.filter(status=ProductPublication.STATUS_PUBLISHED)
    template_name = (
        "core/app_shell_product_review.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_review.html"
    )
    return render(request, template_name, {
        "product": product,
        "dna": dna,
        "sections": _product_dna_sections(dna.content),
        "approved_keys": dna.approved_sections(),
        "missing_keys": dna.missing_sections(),
        "is_fully_approved": dna.is_fully_approved(),
        "critique_proposals": critique_proposals,
        "consistency_open_count": consistency_open_count,
        "publications": publications,
        "publication_channels": ProductPublication.CHANNEL_CHOICES,
        "product_step": 3,
    })


@login_required
@require_http_methods(["POST"])
def product_promote(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    if product.status != Product.STATUS_IN_VALIDAZIONE:
        return HttpResponse("Stato non valido per la promozione", status=400)
    product.status = Product.STATUS_ATTIVO
    product.save(update_fields=["status"])
    active_count = company.products.filter(status=Product.STATUS_ATTIVO).count()
    threshold = _consistency_periodic_threshold(company)
    if active_count and active_count % threshold == 0:
        from apps.companies.tasks import run_consistency_audit

        company_dna = company.dna_versions.filter(
            dna_type=CompanyDNA.TYPE_COMPLETE,
            is_current=True,
        ).first()
        if company_dna:
            _mark_consistency_audit_pending(company_dna, ConsistencyIssue.SCOPE_PERIODIC)
        tenant = getattr(request, "tenant", None)
        profile = _consistency_audit_profile(company)
        run_consistency_audit.delay(
            company.pk,
            scope=ConsistencyIssue.SCOPE_PERIODIC,
            tenant_schema=tenant.schema_name if tenant else None,
            max_issues=profile["max_issues"],
            depth_instruction=profile["depth_instruction"],
        )
    return redirect("product-detail", pk=product.pk)


@login_required
@require_http_methods(["POST"])
def product_publish(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    if product.status != Product.STATUS_ATTIVO:
        return HttpResponse("Pubblicazione disponibile solo per specialisti attivi", status=400)
    dna = product.dna_versions.filter(
        dna_type=ProductDNA.TYPE_COMPLETE,
        is_current=True,
    ).first()
    if not dna or not dna.is_fully_approved():
        return HttpResponse("DNA Specialista approvato non trovato", status=404)

    channel = request.POST.get("channel", ProductPublication.CHANNEL_WEBSITE)
    valid_channels = {value for value, _label in ProductPublication.CHANNEL_CHOICES}
    if channel not in valid_channels:
        return HttpResponse("Canale non valido", status=400)

    from apps.companies.dna_renderer import render_product_publication

    content_md = render_product_publication(product, dna, channel)
    with transaction.atomic():
        product.publications.filter(
            channel=channel,
            status=ProductPublication.STATUS_PUBLISHED,
        ).update(
            status=ProductPublication.STATUS_ARCHIVED,
            archived_at=timezone.now(),
        )
        ProductPublication.objects.create(
            product=product,
            product_dna=dna,
            channel=channel,
            content_md=content_md,
            created_by=request.user if request.user.is_authenticated else None,
        )
    return redirect("product-review", pk=product.pk)


@login_required
@require_http_methods(["POST"])
def product_publication_archive(request, pk, publication_pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    publication = get_object_or_404(
        ProductPublication,
        pk=publication_pk,
        product=product,
        status=ProductPublication.STATUS_PUBLISHED,
    )
    publication.status = ProductPublication.STATUS_ARCHIVED
    publication.archived_at = timezone.now()
    publication.save(update_fields=["status", "archived_at"])
    return redirect("product-review", pk=product.pk)


def _get_critique_proposal(dna, index):
    proposals = dna.content.get("_critique", [])
    if not isinstance(proposals, list) or index < 0 or index >= len(proposals):
        return None
    return proposals[index]


@login_required
@require_http_methods(["POST"])
def product_critique_accept(request, pk, proposal_index):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    dna = product.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA non trovato", status=404)
    proposal = _get_critique_proposal(dna, proposal_index)
    if not proposal:
        return HttpResponse("Proposta non trovata", status=404)
    section_key = proposal.get("section_key")
    proposed_text = proposal.get("proposed_text", "").strip()
    if section_key not in set(PRODUCT_LAYER_KEYS) or not proposed_text:
        return HttpResponse("Dati proposta non validi", status=400)

    dna.content[section_key] = proposed_text
    proposals = dna.content.get("_critique", [])
    proposals.pop(proposal_index)
    dna.content["_critique"] = proposals
    try:
        from apps.companies.audit import compute_audit_hash
        dna.audit_hash = compute_audit_hash(dna.content, dna.previous_hash or "")
    except Exception:
        pass
    dna.save(update_fields=["content", "audit_hash"])
    return redirect("product-review", pk=product.pk)


@login_required
@require_http_methods(["POST"])
def product_critique_reject(request, pk, proposal_index):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    dna = product.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA non trovato", status=404)
    proposal = _get_critique_proposal(dna, proposal_index)
    if not proposal:
        return HttpResponse("Proposta non trovata", status=404)
    proposals = dna.content.get("_critique", [])
    proposals.pop(proposal_index)
    dna.content["_critique"] = proposals
    dna.save(update_fields=["content"])
    return redirect("product-review", pk=product.pk)


@login_required
@require_http_methods(["POST"])
def product_section_approve(request, pk, section_key):
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return JsonResponse({"error": "product not found"}, status=404)
    dna = ProductDNA.objects.filter(product=product, is_current=True).first()
    if not dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in set(PRODUCT_LAYER_KEYS):
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = _request_data(request)
    comment = body.get("comment", "")
    is_clarification = str(body.get("is_clarification", False)).lower() == "true"

    if is_clarification:
        ProductSectionApproval.objects.create(
            dna=dna,
            section_key=section_key,
            approved_by=request.user,
            comment=comment,
            is_clarification=True,
        )
    else:
        # Safe_mode: block approval if CRITICAL flags exist
        block_reasons = _product_approval_block_reasons(dna)
        if block_reasons:
            messages = [
                f.get("message", "Problema rilevato")
                for f in block_reasons
                if f.get("layer") == section_key
            ]
            return JsonResponse(
                {"error": "; ".join(messages) or "Approvazione bloccata da safe_mode"},
                status=409,
            )
        ProductSectionApproval.objects.update_or_create(
            dna=dna,
            section_key=section_key,
            is_clarification=False,
            defaults={
                "approved_by": request.user,
                "comment": comment,
            },
        )
        dna.refresh_from_db()
        if not dna.missing_sections():
            dna.is_approved = timezone.now()
            dna.save(update_fields=["is_approved"])
            # Decision 1B: auto-promote product to in_validazione once the
            # specialist DNA is fully approved. The final attivo transition
            # stays manual (product_promote view) as the human gate.
            if product.status in {Product.STATUS_IN_COSTRUZIONE, Product.STATUS_UPDATING}:
                product.status = Product.STATUS_IN_VALIDAZIONE
                product.save(update_fields=["status"])

    if is_clarification and request.headers.get("HX-Request"):
        return HttpResponse(
            '<span class="rounded-xl bg-amber-400/10 px-3 py-2 text-sm '
            'text-amber-300">Richiesta inviata ✓</span>'
        )

    if request.headers.get("HX-Request") == "true":
        return _redirect_after_htmx_action(request, "product-review", product.pk)

    return JsonResponse({
        "section_key": section_key,
        "is_clarification": is_clarification,
        "approved": not is_clarification,
        "is_fully_approved": dna.is_fully_approved(),
        "missing_sections": dna.missing_sections(),
    })


@login_required
@require_http_methods(["POST"])
def product_section_edit(request, pk, section_key):
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return JsonResponse({"error": "product not found"}, status=404)
    old_dna = ProductDNA.objects.filter(product=product, is_current=True).first()
    if not old_dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in set(PRODUCT_LAYER_KEYS):
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = _request_data(request)
    new_text = body.get("text", "").strip()
    if not new_text:
        if request.headers.get("HX-Request") == "true":
            return _action_error("Testo sezione obbligatorio.", status=400)
        return JsonResponse({"error": "text is required"}, status=400)

    content = dict(old_dna.content) if isinstance(old_dna.content, dict) else {}
    content[section_key] = new_text

    # A2 — normalize punctuation before save
    from apps.companies.dna_validator import normalize_dna_punctuation
    content = normalize_dna_punctuation(content)

    product.dna_versions.filter(is_current=True).update(is_current=False)

    new_dna = ProductDNA.objects.create(
        product=product,
        version=old_dna.version + 1,
        dna_type=old_dna.dna_type,
        content=content,
        created_by=request.user,
    )

    for approval in old_dna.section_approvals.all():
        if approval.section_key != section_key:
            ProductSectionApproval.objects.create(
                dna=new_dna,
                section_key=approval.section_key,
                approved_by=approval.approved_by,
                comment=approval.comment,
                is_clarification=approval.is_clarification,
            )

    if request.headers.get("HX-Request") == "true":
        return _redirect_after_htmx_action(request, "product-review", product.pk)

    return JsonResponse({
        "dna_id": new_dna.id,
        "version": new_dna.version,
        "section_key": section_key,
        "missing_sections": new_dna.missing_sections(),
    })


@login_required
def product_dna_visualize(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    dna = product.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA non trovato", status=404)
    sections = _product_dna_sections(dna.content)
    template_name = (
        "core/app_shell_product_dna_visualize.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/product_dna_visualize.html"
    )
    return render(request, template_name, {
        "product": product,
        "dna": dna,
        "sections": sections,
        "product_step": 4,
        "feedback_available": bool(dna.is_approved or not dna.missing_sections()),
    })


@login_required
def product_dna_download_pdf(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)
    dna = product.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA non trovato", status=404)

    final_doc = "\n\n".join(
        f"## {s['label']}\n\n{s['value']}"
        for s in _product_dna_sections(dna.content)
        if s["value"]
    )
    pdf_bytes = _render_dna_pdf(company, dna, final_doc)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="DNA_{product.name.replace(" ", "_")}.pdf"'
    return response


def _generate_specialist_feedback_proposals(product, specialist_dna, company_dna):
    """Ask LLM: what does this Specialist DNA reveal that the Company DNA doesn't capture yet?"""
    specialist_json = json.dumps(
        {k: specialist_dna.content.get(k, "") for k in PRODUCT_LAYER_KEYS},
        ensure_ascii=False,
        indent=2,
    )
    company_json = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    prompt = f"""
FEEDBACK_SPECIALISTA_GENERALE

Sei ZEUS. Hai un DNA Specialista approvato per "{product.name}" e il DNA Generale dell'azienda.
Il tuo compito e identificare cosa il DNA Specialista rivela che il DNA Generale NON cattura ancora.

Per ogni proposta, indica:
- quale sezione del DNA Generale aggiornare (identita, modelli_mentali, nucleo_tecnico, confini, tono, logica_decisionale)
- il valore attuale
- il valore proposto (integra, non sostituire)
- la motivazione (cosa ha rivelato lo specialista)

Regole:
- Proponi SOLO aggiornamenti che aggiungono informazione nuova.
- Non ripetere cio che il DNA Generale contiene gia.
- "proposed_value" deve essere testo finale integrato, non una direttiva.
- Non usare prefissi come "Aggiungere:", "Integrare:" o "Proposta:".
- Massimo 5 proposte.
- Se non c'è nulla di nuovo da aggiungere, ritorna un array vuoto.

DNA SPECIALISTA ({product.name}):
{specialist_json}

DNA GENERALE:
{company_json}

Output JSON:
{{
  "proposals": [
    {{
      "target_layer": "nucleo_tecnico|confini|logica_decisionale|identita|modelli_mentali|tono",
      "current_value": "riassunto del valore attuale",
      "proposed_value": "nuovo valore integrato (testo completo)",
      "rationale": "perche questa proposta nasce dal DNA Specialista"
    }}
  ]
}}

Rispondi SOLO JSON, senza markdown.
""".strip()

    client = get_llm_client()
    result, content = _generate_with_retry(
        client,
        prompt,
        model=LLM_MODEL,
        system_prompt=ZEUS_SYSTEM_PROMPT,
        temperatures=(0.4, 0.3, 0.2),
        context="specialist-feedback",
    )

    LLMCall.objects.create(
        company=product.company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
    )

    return content.get("proposals", [])


def _feedback_session_key(product, specialist_dna, company_dna):
    return f"specialist_feedback:{product.pk}:{specialist_dna.pk}:{company_dna.pk}"


def _set_specialist_feedback_generation(specialist_dna, **data):
    content = dict(specialist_dna.content) if isinstance(specialist_dna.content, dict) else {}
    state = dict(content.get("_feedback_generation") or {})
    state.update(data)
    state["updated_at"] = timezone.now().isoformat()
    content["_feedback_generation"] = state
    specialist_dna.content = content
    specialist_dna.save(update_fields=["content"])
    return state


def _specialist_feedback_progress_context(specialist_dna):
    content = specialist_dna.content if isinstance(specialist_dna.content, dict) else {}
    state = content.get("_feedback_generation") or {}
    if not isinstance(state, dict):
        state = {}
    step_num = int(state.get("step_num") or 1)
    steps_total = int(state.get("steps_total") or 4)
    step_label = state.get("step_label") or "Lettura DNA Specialista"
    phase_labels = [
        "Lettura del DNA Specialista",
        "Confronto con il DNA Generale",
        "Identificazione delle novità emerse",
        "Preparazione delle proposte",
    ]
    phases = []
    for index, phase_label in enumerate(phase_labels, start=1):
        if index < step_num:
            status = "done"
        elif index == step_num:
            status = "active"
        else:
            status = "pending"
        phases.append({"label": phase_label, "status": status})
    return {
        "task_status": state.get("status") or "running",
        "task_error": state.get("error", ""),
        "feedback_phases": phases,
        "steps_total": steps_total,
        "current_step_num": step_num,
        "step_label": step_label,
        "progress_pct": min(int(step_num / steps_total * 100), 95),
    }


def _selected_specialist_feedback_proposals(proposals, selected_indices):
    selected = []
    valid_layers = set(LAYER_KEYS)
    for idx in selected_indices:
        try:
            proposal = proposals[int(idx)]
        except (TypeError, ValueError, IndexError):
            continue
        target = proposal.get("target_layer", "")
        proposed = _strip_instruction_prefixes(proposal.get("proposed_value", ""))
        if target not in valid_layers or not proposed:
            continue
        selected.append({
            "target_layer": target,
            "current_value": _strip_instruction_prefixes(proposal.get("current_value", "")),
            "proposed_value": proposed,
            "rationale": _strip_instruction_prefixes(proposal.get("rationale", "")),
        })
    return selected


def _sanitize_company_feedback_content(content):
    clean = _public_content(content)
    for key in ["sintesi_cognitiva", *LAYER_KEYS]:
        if key in clean:
            clean[key] = _sanitize_public_value(clean[key])
    return clean


def _fallback_apply_specialist_feedback(current_content, selected_proposals):
    new_content = dict(current_content)
    for proposal in selected_proposals:
        target = proposal["target_layer"]
        current = _strip_instruction_prefixes(_as_text(new_content.get(target)))
        proposed = _strip_instruction_prefixes(proposal.get("proposed_value", ""))
        if not proposed:
            continue
        if proposed.lower() in current.lower():
            new_content[target] = current
        else:
            new_content[target] = f"{current}\n\n{proposed}".strip() if current else proposed
    return new_content


def _regenerate_company_dna_from_specialist_feedback(
    company,
    product,
    company_dna,
    specialist_dna,
    selected_proposals,
):
    current_content = _sanitize_company_feedback_content(company_dna.content)
    current_json = json.dumps(current_content, ensure_ascii=False, indent=2)
    specialist_json = json.dumps(
        {k: specialist_dna.content.get(k, "") for k in PRODUCT_LAYER_KEYS},
        ensure_ascii=False,
        indent=2,
    )
    proposals_json = json.dumps(selected_proposals, ensure_ascii=False, indent=2)

    prompt = f"""
RIGENERA_DNA_GENERALE_DA_SPECIALISTA

Sei ZEUS. Devi rigenerare il DNA Generale dell'azienda integrando SOLO le proposte
specialistiche approvate dall'utente per il prodotto "{product.name}".

OBIETTIVO:
- Non applicare patch testuali.
- Non appendere istruzioni al testo esistente.
- Riscrivi le sezioni interessate come paragrafi coerenti e naturali.
- Preserva le parti valide del DNA Generale non toccate dalle proposte.

REGOLE OBBLIGATORIE:
1. Non scrivere mai etichette operative come "Aggiungere:", "Integrare:",
   "Proposta:", "Da aggiungere:" o simili nel testo finale.
2. Le proposte approvate sono input concettuali, non testo da incollare.
3. Integra il contributo specialista solo quando cambia davvero la comprensione
   trasversale dell'azienda; non trasformare il DNA Generale in scheda prodotto.
4. Mantieni tono tecnico, fluido, in terza persona, italiano naturale.
5. Se una sezione non e toccata dalle proposte, restituiscila pulita e invariata.
6. Rimuovi eventuali residui gia presenti nel DNA attuale come "Aggiungere:".

OUTPUT JSON completo con queste chiavi esatte:
- sintesi_cognitiva
- identita
- modelli_mentali
- nucleo_tecnico
- confini
- tono
- logica_decisionale

DNA GENERALE ATTUALE:
{current_json}

DNA SPECIALISTA DI RIFERIMENTO:
{specialist_json}

PROPOSTE APPROVATE:
{proposals_json}

Rispondi SOLO con JSON valido. Nessun markdown, nessuna spiegazione.
""".strip()

    client = get_llm_client()
    try:
        result, rewritten = _generate_with_retry(
            client,
            prompt,
            model=LLM_MODEL_PRO,
            system_prompt=ZEUS_SYSTEM_PROMPT,
            temperatures=(0.35, 0.25, 0.15),
            parse=_parse_json_object,
            context="specialist-feedback-regeneration",
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
        merged = _safe_merge_synthesis(current_content, rewritten)
    except Exception:
        logger.exception(
            "Specialist feedback regeneration failed for product %s; applying cleaned fallback",
            product.pk,
        )
        merged = _fallback_apply_specialist_feedback(current_content, selected_proposals)

    merged["_specialist_feedback"] = {
        "product_id": product.pk,
        "product_name": product.name,
        "specialist_dna_id": specialist_dna.pk,
        "approved_proposals": selected_proposals,
        "applied_at": timezone.now().isoformat(),
    }
    return merged


@login_required
def product_dna_feedback(request, pk):
    """Show proposals to update Company DNA based on approved Specialist DNA.

    Async flow: proposals are generated by a Celery task and persisted on the
    Specialist DNA content (``_feedback_proposals`` key). The GET view checks
    whether proposal generation has already been requested for this
    Specialist→General pair:

    - ``?=generating`` param + no proposals yet → render loading page with
      HTMX polling so the browser re-checks every few seconds.
    - Proposals present (even empty list) → render the feedback page.
    - First visit (no proposals, no ``generating``) → dispatch the task and
      redirect to the same URL with ``?generating=1``.
    """
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    specialist_dna = product.dna_versions.filter(
        is_current=True,
    ).first()
    if not specialist_dna:
        return HttpResponse("DNA specialista non trovato", status=404)
    if not specialist_dna.is_approved and specialist_dna.missing_sections():
        return HttpResponse(
            "DNA specialista non completamente approvato.",
            status=404,
        )

    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True,
    ).first()
    if not company_dna:
        return HttpResponse("DNA Generale non trovato", status=404)

    # Read proposals from the Specialist DNA content (set by the Celery task).
    # Three states:
    #   key absent      → first visit, dispatch task
    #   key present, None → task in flight, show loading
    #   key present, list → task done, show proposals
    has_key = "_feedback_proposals" in specialist_dna.content
    proposals = specialist_dna.content.get("_feedback_proposals")

    if isinstance(proposals, list):
        # Task completed: show the proposals (empty list = nothing to add).
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("product-dna-feedback", args=[product.pk])
            return response
        if hasattr(request, "session"):
            request.session[_feedback_session_key(product, specialist_dna, company_dna)] = proposals
            if hasattr(request.session, "modified"):
                request.session.modified = True
        template_name = (
            "core/app_shell_product_dna_feedback.html"
            if settings.ZEUS_APP_SHELL_ENABLED
            else "core/product_dna_feedback.html"
        )
        return render(request, template_name, {
            "product": product,
            "specialist_dna": specialist_dna,
            "company_dna": company_dna,
            "proposals": proposals,
            "product_step": 5,
        })

    if has_key:
        # Task in flight (proposals is None): render loading page with HTMX
        # polling. The browser polls this same URL; once the Celery task
        # completes and replaces None with a list, the branch above fires.
        template_name = (
            "core/app_shell_product_dna_feedback_loading.html"
            if settings.ZEUS_APP_SHELL_ENABLED
            else "core/product_dna_feedback_loading.html"
        )
        return render(request, template_name, {
            "product": product,
            "product_step": 5,
            **_specialist_feedback_progress_context(specialist_dna),
        })

    # First visit: dispatch the Celery task and mark the slot as pending.
    from apps.companies.tasks import generate_specialist_feedback_task
    content = dict(specialist_dna.content) if isinstance(specialist_dna.content, dict) else {}
    content["_feedback_proposals"] = None
    content["_feedback_generation"] = {
        "status": "running",
        "step_num": 1,
        "steps_total": 4,
        "step_label": "Lettura DNA Specialista",
        "started_at": timezone.now().isoformat(),
    }
    specialist_dna.content = content
    specialist_dna.save(update_fields=["content"])
    tenant_schema = getattr(request, "tenant", None)
    generate_specialist_feedback_task.delay(
        product.id, specialist_dna.id, company_dna.id,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )
    return redirect(
        f"{reverse('product-dna-feedback', args=[product.pk])}?generating=1"
    )


@login_required
@require_http_methods(["POST"])
def product_dna_feedback_apply(request, pk):
    """Apply selected proposals to Company DNA, creating a new version."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    specialist_dna = product.dna_versions.filter(
        is_current=True,
    ).first()
    if not specialist_dna:
        return HttpResponse("DNA specialista non trovato", status=404)
    if not specialist_dna.is_approved and specialist_dna.missing_sections():
        return HttpResponse(
            "DNA specialista non completamente approvato.",
            status=404,
        )

    company_dna = company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True,
    ).first()
    if not company_dna:
        return HttpResponse("DNA Generale non trovato", status=404)

    selected_indices = request.POST.getlist("selected_proposals")
    # Read proposals from the Specialist DNA content (set by the Celery task),
    # falling back to session for backward compatibility with in-flight sessions
    # created before the async migration.
    proposals = specialist_dna.content.get("_feedback_proposals")
    if not isinstance(proposals, list):
        session_key = _feedback_session_key(product, specialist_dna, company_dna)
        proposals = request.session.get(session_key)
    if not isinstance(proposals, list):
        proposals = []
    selected_proposals = _selected_specialist_feedback_proposals(
        proposals,
        selected_indices,
    )
    if not selected_proposals:
        return redirect("product-dna-feedback", pk=product.pk)

    content = dict(company_dna.content) if isinstance(company_dna.content, dict) else {}
    content["_pending_specialist_feedback"] = {
        "product_id": product.pk,
        "specialist_dna_id": specialist_dna.pk,
        "selected_proposals": selected_proposals,
    }
    company_dna.content = content
    company_dna.save(update_fields=["content"])

    last_version = company.dna_versions.order_by("-version").first()
    expected_version = (last_version.version + 1) if last_version else 1
    _set_pending_complete_generation(
        request,
        expected_version,
        source_dna_id=company_dna.pk,
        return_product_id=product.pk,
    )
    _set_company_generation_progress(
        company_dna,
        1,
        4,
        "Lettura feedback Specialista",
        status="running",
        flow="specialist_feedback",
        product_id=product.pk,
        product_name=product.name,
    )

    tenant_schema = getattr(request, "tenant", None)
    from apps.companies.tasks import apply_specialist_feedback_task
    apply_specialist_feedback_task.delay(
        company.pk,
        company_dna.pk,
        tenant_schema=tenant_schema.schema_name if tenant_schema else None,
    )

    # Clean up any session-stored proposals (legacy) and the processed slot
    # on the Specialist DNA so a future visit re-triggers the async flow.
    session_key = _feedback_session_key(product, specialist_dna, company_dna)
    request.session.pop(session_key, None)
    request.session.modified = True
    specialist_dna.content.pop("_feedback_proposals", None)
    specialist_dna.save(update_fields=["content"])

    return redirect("dna-generating")
