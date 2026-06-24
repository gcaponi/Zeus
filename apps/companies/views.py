import json
import logging
import re
import textwrap
from urllib.parse import urlparse

import fitz
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.companies.dna_schemas import LAYER_KEYS, LAYER_TITLES
from apps.companies.llm_client import LLM_MODEL, get_llm_client
from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    CompanyQuestion,
    DNAFeedback,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
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


def _company_file_block_reason(company):
    subscription = _subscription_for_company(company)
    if not subscription:
        return None
    current_count = company.company_files.count()
    if subscription.company_files_used != current_count:
        subscription.company_files_used = current_count
        subscription.save(update_fields=["company_files_used"])
    if not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    if not subscription.can_add_company_file():
        return "Limite file aziendali raggiunto per il piano attuale."
    return None


def _onboarding_context(request):
    company = _tenant_company(request)
    if not company:
        return None
    latest_source = company.sources.order_by("-created_at").first()
    latest_run = company.pipeline_runs.select_related("source").order_by("-created_at").first()
    latest_dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    sections = _dna_sections(latest_dna.content) if latest_dna else []
    step = 4 if latest_dna else 3 if latest_run else 2
    return {
        "company": company,
        "source": latest_source,
        "run": latest_run,
        "dna": latest_dna,
        "sections": sections,
        "step": step,
        "is_done": latest_dna is not None,
    }


def _dna_sections(content, old_content=None):
    sections = []
    for key in LAYER_KEYS:
        label = LAYER_TITLES[key]
        value = _as_text(content.get(key) if isinstance(content, dict) else None)
        old_value = None
        if old_content and isinstance(old_content, dict):
            old_value = _as_text(old_content.get(key))
        sections.append({
            "key": key,
            "label": label,
            "value": value or "",
            "old_value": old_value or "",
            "changed": bool(old_value and old_value != value),
        })
    return sections


def _dna_public_document(content):
    """Return the client-facing Sintesi Cognitiva, never the internal layer map."""
    if not isinstance(content, dict):
        return _as_text(content).strip()

    explicit = _as_text(content.get("sintesi_cognitiva")).strip()
    if explicit:
        return explicit

    # Fallback for DNA generated before the public renderer existed: keep the
    # internal order but hide layer labels so the client sees a continuous text.
    paragraphs = []
    for key in LAYER_KEYS:
        text = _as_text(content.get(key)).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _as_text(value):
    if isinstance(value, list):
        parts = [_as_text(item).strip() for item in value]
        return ", ".join(part for part in parts if part)
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
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _section_context(content, section_key):
    if not isinstance(content, dict):
        return "Non disponibile"
    text = _as_text(content.get(section_key)).strip()
    if not text:
        return "Non disponibile"
    return text[:240]


def _company_document_context(company):
    snippets = []
    for company_file in company.company_files.all()[:3]:
        text = " ".join(company_file.content_text.split())[:220]
        if text:
            snippets.append(f"{company_file.original_name}: {text}")
    return " | ".join(snippets) or "Nessun documento aziendale caricato"


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
- Ogni domanda deve partire da una lacuna, ambiguita, affermazione o opportunita
  che noti nel pre-DNA o nei documenti.
- Non fare domande generiche se il piano e Professional o Legacy.
- Per Legacy comportati da vero analista professionale: devi estrarre
  mentalita aziendale, filosofia decisionale e anti-deriva.
- Usa i principi A1-A10 come assi di analisi, ma scegli tu i 10 piu utili.
- Rispondi SOLO JSON valido, senza markdown.

Formato JSON:
{{
  "questions": [
    {{
      "code": "A1",
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

DOCUMENTI / NOTE AZIENDALI:
{documents}
""".strip()


def _parse_question_generation(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return JSON") from None
        payload = json.loads(match.group(1))

    questions = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(questions, list) or len(questions) != 10:
        raise ValueError("LLM must return exactly 10 questions")
    return questions


def _parse_json_object(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return JSON object") from None
        payload = json.loads(match.group(1))
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

Sei ZEUS, un analista aziendale esperto nel settore manifatturiero.
Stai costruendo il DNA completo di un'{entity_label}. Devi riscrivere la sezione
"{section_key}" combinando due fonti:
1. Il pre-DNA generato dallo scraping del sito web e dei documenti
2. Le risposte del cliente a domande di approfondimento (conoscenza tacita)

Il risultato deve essere un testo professionale, narrativo e coerente, come se
un consulente esperto avesse scritto il profilo dopo aver studiato i materiali
e intervistato il titolare.

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
- Puoi fare inferenze ragionevoli e collegamenti logici tra le informazioni
  (livello creativita 2/5: puoi aggiungere dettagli plausibili ma non inventare
  fatti non supportati dalle fonti).
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
            result = client.generate(prompt)
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
            rewritten_value = _parse_rewrite_response(result.text, section_key)
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
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if not match:
                return None
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
        if isinstance(payload, str):
            return [s.strip() for s in payload.split(",") if s.strip()]
        return None
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if not match:
                cleaned = text.strip().strip('"').strip()
                return cleaned if cleaned else None
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
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
    result = client.generate(prompt)
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
    questions_data = _parse_question_generation(result.text)
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
                "principle": str(raw_question.get("principle", "A1-A10"))[:120],
                "question": str(raw_question.get("question", "")).strip(),
                "answer_depth": str(
                    raw_question.get("answer_depth") or profile["answer_depth"]
                )[:40],
                "answer_guidance": str(raw_question.get("answer_guidance", "")).strip(),
            },
        )
    return list(dna.questions.all())


def _dna_in_safe_mode(dna) -> bool:
    """Return True if the DNA's enrichment bundle reports safe_mode (CRITICAL)."""
    enrichment = dna._enrichment or {}
    return bool(enrichment.get("validation", {}).get("safe_mode"))


def _safe_mode_flags(dna) -> list:
    """Return the CRITICAL flags from the enrichment bundle, for display."""
    enrichment = dna._enrichment or {}
    flags = enrichment.get("validation", {}).get("flags", [])
    return [f for f in flags if f.get("severity") == "CRITICAL"]


def _create_complete_dna(company, pre_dna, user):
    questions = list(pre_dna.questions.all())
    plan_slug = questions[0].plan_slug if questions else _plan_slug_for_company(company)
    content = dict(pre_dna.content) if isinstance(pre_dna.content, dict) else {}
    section_keys = list(LAYER_KEYS)
    answers_by_section = _answers_by_section(
        questions,
        section_keys,
        DNA_GENERALE_FALLBACK_LAYER,
    )
    content = _rewrite_sections_with_answers(
        company,
        content,
        answers_by_section,
        section_keys,
        "RIFORMULA_DNA_CON_RISPOSTE",
    )

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

    return dna


def _apply_self_critique(dna, company):
    """Run the 2-pass self-critique loop on the 6 cognitive layers.

    Guarded: any failure falls back to the original DNA. The critique is a
    refinement, never a blocker — if the LLM hiccups, we keep the unrefined DNA.
    """
    try:
        from apps.companies.dna_critique import self_critique_dna
        from apps.companies.dna_schemas import DNAGeneraleSchema, LAYER_KEYS as LK

        layer_content = {k: dna.content.get(k) for k in LK if k in dna.content}
        schema = DNAGeneraleSchema.model_validate(layer_content)
        refined, _report = self_critique_dna(schema, get_llm_client())
        # Re-apply only the 6 layers; keep the rest of content intact.
        new_content = dict(dna.content)
        new_content.update(refined.model_dump())
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
        return "\n".join(page.get_text() for page in doc)[:30000], len(raw), name
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
    return text[:30000], len(raw), name


def _save_company_file_from_request(company, request):
    notes = request.POST.get("company_notes", "").strip()
    uploaded_file = getattr(request, "FILES", {}).get("company_file")
    if not notes and not uploaded_file:
        return None

    block_reason = _company_file_block_reason(company)
    if block_reason:
        return block_reason

    if uploaded_file:
        content_text, file_size, original_name = _extract_company_file_text(uploaded_file)
        if notes:
            content_text = f"{content_text}\n\nNote aggiuntive:\n{notes}".strip()
    else:
        content_text = notes[:30000]
        file_size = len(content_text.encode("utf-8"))
        original_name = "note-azienda.txt"

    if not content_text.strip():
        return "Il documento aziendale non contiene testo leggibile."

    CompanyFile.objects.create(
        company=company,
        original_name=original_name,
        content_text=content_text,
        file_size=file_size,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    subscription = _subscription_for_company(company)
    if subscription:
        subscription.company_files_used = company.company_files.count()
        subscription.save(update_fields=["company_files_used"])
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
    return render(request, "core/onboarding.html", context)


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
        page_context = _onboarding_context(request) or {"company": company, "step": 2}
        page_context.update(context)
        return render(request, "core/onboarding.html", page_context, status=status)

    url = _normalize_source_url(request.POST.get("url", ""))
    if not url:
        return _source_form_response({
            "error": "Inserisci un URL valido.",
        }, status=400)

    block_reason = _workspace_block_reason(company)
    if block_reason:
        return _source_form_response({
            "error": block_reason,
        }, status=403)

    file_error = _save_company_file_from_request(company, request)
    if file_error:
        return _source_form_response({
            "error": file_error,
        }, status=403)

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
        return render(request, "core/onboarding/_dna.html", {
            "dna": dna,
            "sections": _dna_sections(dna.content),
        })

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
            return render(request, "core/onboarding/_dna.html", {
                "dna": dna,
                "sections": _dna_sections(dna.content),
            })
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
    return render(request, "core/onboarding/_dna.html", {
        "dna": dna,
        "sections": _dna_sections(dna.content),
    })


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
        subscription.company_files_used = 0
        subscription.save(update_fields=["company_files_used"])
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

    error = None
    try:
        questions = _generate_company_questions(company, pre_dna)
    except ValueError as exc:
        questions = []
        error = f"ZEUS non e riuscito a generare le domande: {exc}"
    if request.method == "POST" and not error:
        body = _request_data(request)
        missing = []
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande prima di generare il DNA completo."
        else:
            from apps.companies.tasks import generate_complete_dna
            tenant_schema = getattr(request, "tenant", None)
            generate_complete_dna.delay(
                company.id,
                pre_dna.id,
                request.user.id if request.user.is_authenticated else None,
                tenant_schema=tenant_schema.schema_name if tenant_schema else None,
            )
            return redirect("dna-generating")

    status_code = 400 if error else 200
    return render(request, "core/dna_questions.html", {
        "company": company,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "error": error,
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
    if request.headers.get("HX-Request") == "true":
        if complete_dna:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("dna-review")
            return response
        return HttpResponse(status=204)
    if complete_dna:
        return redirect("dna-review")
    return render(request, "core/dna_generating.html")


@login_required
def dna_review(request):
    """Pagina review DNA — mostra sezioni con stato approvazione."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)
    return render(request, "core/dna_review.html", {
        "dna": dna,
        "sections": _dna_sections(dna.content),
        "public_document": _dna_public_document(dna.content),
        "approved_keys": dna.approved_sections(),
        "missing_keys": dna.missing_sections(),
        "is_fully_approved": dna.is_fully_approved(),
        "is_export_ready": dna.is_export_ready(),
        "company_name": company.name,
    })


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
            # PIANO 1.5: safe_mode blocks final approval. A DNA with a CRITICAL
            # validation flag (e.g. a whole layer empty) cannot be approved until
            # the issue is resolved — it must be edited first.
            if _dna_in_safe_mode(dna):
                if request.headers.get("HX-Request") == "true":
                    return _action_error(
                        "DNA in safe mode: risolvi i flag CRITICAL (strati vuoti) "
                        "prima di approvare.", status=409,
                    )
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
        return _redirect_after_htmx_action(request, "dna-review")

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
        return _redirect_after_htmx_action(request, "dna-review")

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

    pdf_bytes = _render_dna_pdf(company, dna, _dna_public_document(dna.content))
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="DNA_aziendale.pdf"'
    return response


def _render_dna_pdf(company, dna, public_document):
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

    write("DNA Aziendale", size=24, color=(0.02, 0.18, 0.32), gap=10, width=60)
    write(company.name, size=14, color=(0.18, 0.18, 0.18), gap=4)
    approved_at = dna.is_approved.strftime("%d/%m/%Y %H:%M") if dna.is_approved else "n/d"
    write(
        f"Versione {dna.version} · Approvato il {approved_at}",
        size=9,
        color=(0.35, 0.35, 0.35),
        gap=18,
    )

    write(public_document or "Non disponibile", size=10.5, color=(0.08, 0.08, 0.08), gap=16)

    return doc.tobytes()


def _product_dna_sections(content, old_content=None):
    labels = {
        "descrizione": "Descrizione",
        "applicazione": "Applicazione",
        "specifiche": "Specifiche",
        "vincoli": "Vincoli",
        "valore": "Valore",
    }
    sections = []
    for key, label in labels.items():
        value = _as_text(content.get(key) if isinstance(content, dict) else None)
        old_value = None
        if old_content and isinstance(old_content, dict):
            old_value = _as_text(old_content.get(key))
        sections.append({
            "key": key,
            "label": label,
            "value": value or "",
            "old_value": old_value or "",
            "changed": bool(old_value and old_value != value),
        })
    return sections


def _product_document_context(product):
    snippets = []
    for product_file in product.product_files.all()[:3]:
        text = " ".join(product_file.content_text.split())[:220]
        if text:
            snippets.append(f"{product_file.original_name}: {text}")
    return " | ".join(snippets) or "Nessun documento prodotto caricato"


def _product_question_generation_prompt(product, dna, plan_slug):
    profile = QUESTION_GENERATION_PROFILES[plan_slug]
    content = json.dumps(dna.content, ensure_ascii=False, indent=2)
    documents = _product_document_context(product)
    company_dna = product.company.dna_versions.filter(
        dna_type=CompanyDNA.TYPE_COMPLETE, is_current=True
    ).first()
    company_context = ""
    if company_dna:
        company_context = json.dumps(company_dna.content, ensure_ascii=False, indent=2)

    return f"""
GENERA_DOMANDE_D1_D20

Sei ZEUS. Devi generare 10 domande per il cliente DOPO aver creato un pre-DNA prodotto.
Le domande NON devono essere fisse o da template: devono nascere interpretando il
pre-DNA prodotto, i file caricati e il DNA aziendale.

PIANO: {plan_slug}
PROFILO: {profile["label"]}
ISTRUZIONE DI PROFONDITA: {profile["instruction"]}

Regole obbligatorie:
- Genera esattamente 10 domande originali.
- Ogni domanda deve partire da una lacuna, ambiguita, affermazione o opportunita
  che noti nel pre-DNA prodotto o nei documenti.
- Non fare domande generiche se il piano e Professional o Legacy.
- Per Legacy comportati da vero analista professionale: devi estrarre
  logica applicativa, vincoli tecnici, valore differenziante.
- Usa i principi D1-D20 come assi di analisi, ma scegli tu i 10 piu utili.
- Rispondi SOLO JSON valido, senza markdown.

Formato JSON:
{{
  "questions": [
    {{
      "code": "D1",
      "section_key": "descrizione|applicazione|specifiche|vincoli|valore",
      "principle": "nome breve del principio usato",
      "question": "domanda al cliente",
      "answer_depth": "generica|mirata|analitica",
      "answer_guidance": "che tipo di risposta ti aspetti dal cliente"
    }}
  ]
}}

PRE-DNA PRODOTTO:
{content}

DOCUMENTI / NOTE PRODOTTO:
{documents}

DNA AZIENDALE (se disponibile):
{company_context}
""".strip()


def _parse_product_question_generation(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return JSON") from None
        payload = json.loads(match.group(1))

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
    result = client.generate(prompt)
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

    section_keys = {"descrizione", "applicazione", "specifiche", "vincoli", "valore"}
    used_codes = set(dna.questions.values_list("code", flat=True))
    for raw_question in _parse_product_question_generation(result.text):
        section_key = raw_question.get("section_key", "valore")
        if section_key not in section_keys:
            section_key = "valore"
        code = _unique_question_code(raw_question.get("code"), used_codes, "D?")
        ProductQuestion.objects.update_or_create(
            dna=dna,
            code=code,
            defaults={
                "product": product,
                "plan_slug": plan_slug,
                "section_key": section_key,
                "principle": str(raw_question.get("principle", "D1-D20"))[:120],
                "question": str(raw_question.get("question", "")).strip(),
                "answer_depth": str(
                    raw_question.get("answer_depth") or profile["answer_depth"]
                )[:40],
                "answer_guidance": str(raw_question.get("answer_guidance", "")).strip(),
            },
        )
    return list(dna.questions.all())


def _create_complete_product_dna(product, pre_dna, user):
    questions = list(pre_dna.questions.all())
    plan_slug = questions[0].plan_slug if questions else _plan_slug_for_company(product.company)
    content = dict(pre_dna.content) if isinstance(pre_dna.content, dict) else {}
    section_keys = ["descrizione", "applicazione", "specifiche", "vincoli", "valore"]
    answers_by_section = _answers_by_section(questions, section_keys, "valore")
    content = _rewrite_sections_with_answers(
        product.company,
        content,
        answers_by_section,
        section_keys,
        "RIFORMULA_PRODUCT_DNA_CON_RISPOSTE",
    )

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

    last_version = product.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    product.dna_versions.filter(is_current=True).update(is_current=False)
    return ProductDNA.objects.create(
        product=product,
        version=next_version,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content=content,
        created_by=user if user and user.is_authenticated else None,
    )


def _product_file_block_reason(product):
    subscription = _subscription_for_company(product.company)
    if not subscription:
        return None
    current_count = product.product_files.count()
    if not subscription.can_use_workspace():
        return "Workspace sospeso. Contatta l'amministratore ZEUS."
    if not subscription.can_add_product_file(current_count):
        return "Limite file per prodotto raggiunto per il piano attuale."
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

    products = company.products.all()

    if request.method == "POST":
        block_reason = _product_block_reason(company)
        if block_reason:
            return render(request, "core/product_list.html", {
                "company": company,
                "products": products,
                "error": block_reason,
            }, status=403)

        name = request.POST.get("name", "").strip()
        if not name:
            return render(request, "core/product_list.html", {
                "company": company,
                "products": products,
                "error": "Nome prodotto obbligatorio.",
            }, status=400)

        from django.utils.text import slugify
        slug = slugify(name)
        if Product.objects.filter(company=company, slug=slug).exists():
            return render(request, "core/product_list.html", {
                "company": company,
                "products": products,
                "error": "Prodotto con questo nome gia esistente.",
            }, status=400)

        Product.objects.create(company=company, name=name, slug=slug)
        subscription = _subscription_for_company(company)
        if subscription:
            subscription.product_dnas_used = company.products.count()
            subscription.save(update_fields=["product_dnas_used"])
        products = company.products.all()

    return render(request, "core/product_list.html", {
        "company": company,
        "products": products,
    })


def _wants_json(request):
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _product_detail_context(product, error=None):
    dna = product.dna_versions.filter(is_current=True).first()
    sections = _product_dna_sections(dna.content) if dna else []
    product_files = list(product.product_files.all())
    subscription = _subscription_for_company(product.company)
    product_file_limit = None
    if subscription and subscription.plan and not subscription.plan.unlimited_files_per_product:
        product_file_limit = subscription.plan.max_files_per_product
    return {
        "product": product,
        "dna": dna,
        "sections": sections,
        "product_files": product_files,
        "product_files_count": len(product_files),
        "product_file_limit": product_file_limit,
        "error": error,
    }


@login_required
def product_detail(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    product = Product.objects.filter(pk=pk, company=company).first()
    if not product:
        return HttpResponse("Prodotto non trovato", status=404)

    return render(request, "core/product_detail.html", _product_detail_context(product))


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
                "core/product_detail.html",
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
                "core/product_detail.html",
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
                "core/product_detail.html",
                _product_detail_context(product, "Contenuto vuoto."),
                status=400,
            )
        return JsonResponse({"error": "Contenuto vuoto"}, status=400)

    ProductFile.objects.create(
        product=product,
        original_name=original_name,
        content_text=content_text,
        file_size=file_size,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )

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
                "core/product_detail.html",
                _product_detail_context(product, block_reason),
                status=403,
            )
        return JsonResponse({"error": block_reason}, status=403)

    if not product.product_files.exists():
        error = "Carica almeno un file o una nota prodotto prima di generare il pre-DNA."
        if not _wants_json(request):
            return render(
                request,
                "core/product_detail.html",
                _product_detail_context(product, error),
                status=400,
            )
        return JsonResponse({"error": error}, status=400)

    from apps.companies.tasks import _generate_product_dna
    dna, _llm_call = _generate_product_dna(product, company)

    if not _wants_json(request):
        return redirect("product-detail", pk=product.pk)
    return JsonResponse({
        "dna_id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "dna_type": dna.dna_type,
    }, status=201)


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

    error = None
    try:
        questions = _generate_product_questions(product, pre_dna)
    except ValueError as exc:
        questions = []
        error = f"ZEUS non e riuscito a generare le domande: {exc}"

    if request.method == "POST" and not error:
        body = _request_data(request)
        missing = []
        for question in questions:
            answer = body.get(f"answer_{question.id}", "").strip()
            if not answer:
                missing.append(question.code)
                continue
            question.answer = answer
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        if missing:
            error = "Rispondi a tutte le domande prima di generare il DNA completo."
        else:
            complete_dna = _create_complete_product_dna(product, pre_dna, request.user)

    status_code = 400 if error else 200
    return render(request, "core/product_questions.html", {
        "product": product,
        "pre_dna": pre_dna,
        "complete_dna": complete_dna,
        "questions": questions,
        "plan_slug": questions[0].plan_slug if questions else _plan_slug_for_company(company),
        "plan_label": _question_plan_label(
            questions[0].plan_slug if questions else _plan_slug_for_company(company)
        ),
        "error": error,
    }, status=status_code)


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
    return render(request, "core/product_review.html", {
        "product": product,
        "dna": dna,
        "sections": _product_dna_sections(dna.content),
        "approved_keys": dna.approved_sections(),
        "missing_keys": dna.missing_sections(),
        "is_fully_approved": dna.is_fully_approved(),
    })


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
    if section_key not in {"descrizione", "applicazione", "specifiche", "vincoli", "valore"}:
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
    if section_key not in {"descrizione", "applicazione", "specifiche", "vincoli", "valore"}:
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = _request_data(request)
    new_text = body.get("text", "").strip()
    if not new_text:
        if request.headers.get("HX-Request") == "true":
            return _action_error("Testo sezione obbligatorio.", status=400)
        return JsonResponse({"error": "text is required"}, status=400)

    content = dict(old_dna.content) if isinstance(old_dna.content, dict) else {}
    content[section_key] = new_text

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
