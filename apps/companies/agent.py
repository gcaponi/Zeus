"""Chat in-app "Testa il tuo agente" — system prompt + retrieval KB.

L'agente risponde come il tecnico dell'azienda tenant usando come knowledge base:
- DNA Generale (DNAGenerale completo, corrente, approvato) renderizzato in Markdown
- DNA Specialistici correnti di TUTTI i prodotti con status "attivo"
- Estratti rilevanti dai file caricati (CompanyFile + tutti i ProductFile del tenant)

La conversazione NON e' persistita: la storia vive nella Django session
(chiave SESSION_HISTORY_KEY) ed e' un banco di prova, non un archivio.

Il retrieval usa PostgreSQL full-text search in produzione; su altri backend
(SQLite, usato nei test) ricade su uno scoring lessicale equivalente.
"""

import logging
import re

from django.db import connection

from apps.companies.dna_renderer import render_sintesi_cognitiva
from apps.companies.llm_client import AGENT_CHAT_MARKER
from apps.companies.models import DNAGenerale, CompanyFile, Specialista, ProductDNA, ProductFile

logger = logging.getLogger(__name__)

RETRIEVAL_MAX_CHUNKS = 5
RETRIEVAL_MAX_CHARS = 8000
RETRIEVAL_CHUNK_CHARS = 3000
HISTORY_MAX_MESSAGES = 10

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# Chiave di sessione che contiene la storia chat: lista di dict {role, content}.
SESSION_HISTORY_KEY = "agent_chat_history"

CHAT_RULES = """## Regole di comportamento
- Rispondi sempre nella lingua in cui l'utente scrive.
- Resta dentro i confini del DNA: non promettere tempistiche, prezzi o lavorazioni che il DNA non prevede.
- RAGIONA su quello che sai prima di negare: se dalla scheda operativa, dal DNA o dai documenti puoi dedurre la risposta con un'inferenza diretta (esempio: l'azienda distribuisce prodotti e ha un sito e-commerce → "puoi acquistare sul nostro sito"), dai la risposta spiegando da quale elemento la deduci. Non rispondere "non e' nella mia knowledge base" quando le informazioni disponibili permettono una risposta utile.
- Se anche ragionando l'informazione manca davvero, dillo chiaramente e proponi di mettere l'utente in contatto con un tecnico umano.
- Quando usi informazioni tratte dai documenti, cita la fonte (nome del file o del sito).
- Tono: professionale, diretto, concreto. Niente frasi di circostanza."""


def _operational_profile_block(company):
    """Scheda operativa del tenant: fatti strutturati raccolti in onboarding.

    Sono dati dichiarati dal cliente (settore, canali, sito) che il DNA
    narrativo puo' non ripetere esplicitamente — ma che l'agente deve
    conoscere per ragionare (es. "vendiamo online sul nostro sito").
    """
    facts = []
    site = company.sources.filter(status="scraped").values_list("url", flat=True).first()
    if not site:
        site = company.sources.values_list("url", flat=True).first()
    if site:
        facts.append(f"- Sito web aziendale: {site}")
    if company.settore_primario:
        facts.append(f"- Attivita': {company.get_settore_primario_display()}")
    if company.prodotto_fisico is not None:
        facts.append(
            "- Componente fisica del prodotto/servizio: "
            + ("si'" if company.prodotto_fisico else "no")
        )
    if company.cliente_diretto:
        facts.append(f"- Cliente diretto: {company.get_cliente_diretto_display()}")
    if company.custom_frequenza:
        facts.append(f"- Lavori su specifica/personalizzati: {company.get_custom_frequenza_display()}")
    if company.installatori_in_filiera is not None:
        facts.append(
            "- Installatori/posatori in filiera: "
            + ("si'" if company.installatori_in_filiera else "no")
        )
    if company.settore_secondario:
        facts.append(f"- Settore secondario/nicchia: {company.settore_secondario}")
    if company.contesto_libero:
        facts.append(f"- Nota del fondatore: {company.contesto_libero}")
    if not facts:
        return ""
    return "## Scheda operativa (fatti dichiarati dal cliente)\n" + "\n".join(facts)


def get_approved_company_dna(company):
    """DNA Generale completo, corrente e approvato — il gate della chat."""
    return company.dna_versions.filter(
        dna_type=DNAGenerale.TYPE_COMPLETE,
        is_current=True,
        is_approved__isnull=False,
    ).first()


def build_system_prompt(company):
    """System prompt dell'agente: DNA Generale + TUTTI i DNA Specialistici attivi.

    Ritorna None se il gate non e' superato (nessun DNA completo approvato).
    """
    dna = get_approved_company_dna(company)
    if dna is None:
        return None

    sections = [
        f"[{AGENT_CHAT_MARKER}]",
        (
            f"Sei il tecnico virtuale di {company.name}. Rispondi ai clienti come "
            "farebbe il miglior tecnico-commerciale dell'azienda, usando solo la "
            "conoscenza riportata qui sotto. Hai a disposizione la scheda operativa "
            "dell'azienda, il DNA Generale e i DNA Specialistici di tutti i prodotti "
            "attivi: se una domanda riguarda un prodotto specifico, rispondi usando "
            "il DNA Specialistico di quel prodotto."
        ),
    ]
    profile_block = _operational_profile_block(company)
    if profile_block:
        sections.append(profile_block)
    sections.append(render_sintesi_cognitiva(dna.content or {}, f"DNA Generale — {company.name}"))

    pre_dna = company.dna_versions.filter(
        dna_type=DNAGenerale.TYPE_PRE,
    ).order_by("-version").first()
    if pre_dna is not None:
        sections.append(
            render_sintesi_cognitiva(
                pre_dna.content or {},
                f"Analisi iniziale del sito (pre-DNA) — {company.name}",
            )
        )

    active_products = company.products.filter(status=Specialista.STATUS_ATTIVO)
    for product in active_products:
        product_dna = product.dna_versions.filter(
            dna_type=ProductDNA.TYPE_COMPLETE,
            is_current=True,
        ).first()
        if product_dna is not None:
            sections.append(
                render_sintesi_cognitiva(
                    product_dna.content or {},
                    f"DNA Specialista — {product.name}",
                    product=True,
                )
            )

    sections.append(CHAT_RULES)
    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def _query_terms(query):
    return [t for t in re.findall(r"\w+", (query or "").lower()) if len(t) > 2]


def _excerpt_around(text, terms, limit=RETRIEVAL_CHUNK_CHARS):
    """Slice di `text` centrato sulla prima occorrenza di un termine."""
    if len(text) <= limit:
        return text.strip()
    lower = text.lower()
    positions = [lower.find(t) for t in terms if lower.find(t) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - limit // 4)
    chunk = text[start : start + limit].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + limit < len(text) else ""
    return prefix + chunk + suffix


def _fts_candidates(company, query):
    """Ranking via PostgreSQL full-text search (config 'italian')."""
    from django.contrib.postgres.search import (
        SearchHeadline,
        SearchQuery,
        SearchRank,
        SearchVector,
    )
    from django.db.models import TextField
    from django.db.models.functions import Cast

    search_query = SearchQuery(query, config="italian", search_type="websearch")

    file_vector = SearchVector("content_text", config="italian")
    file_querysets = [
        CompanyFile.objects.filter(company=company),
        ProductFile.objects.filter(product__company=company),
    ]

    candidates = []
    for queryset in file_querysets:
        rows = (
            queryset.annotate(
                rank=SearchRank(file_vector, search_query),
                headline=SearchHeadline(
                    "content_text",
                    search_query,
                    config="italian",
                    max_words=80,
                    min_words=40,
                ),
            )
            .filter(rank__gt=0.01)
            .order_by("-rank")[:RETRIEVAL_MAX_CHUNKS]
        )
        for row in rows:
            candidates.append(
                {
                    "rank": row.rank,
                    "source": row.original_name,
                    "text": (row.headline or "").strip()
                    or (row.content_text or "")[:RETRIEVAL_CHUNK_CHARS],
                }
            )

    # Sorgenti web scrapate (contenuto del sito, es. catalogo e-commerce)
    scraped_text = Cast("scraped_data", output_field=TextField())
    source_vector = SearchVector(scraped_text, config="italian")
    source_rows = (
        company.sources.filter(status="scraped", scraped_data__isnull=False)
        .annotate(
            rank=SearchRank(source_vector, search_query),
            headline=SearchHeadline(
                scraped_text,
                search_query,
                config="italian",
                max_words=80,
                min_words=40,
            ),
        )
        .filter(rank__gt=0.01)
        .order_by("-rank")[:RETRIEVAL_MAX_CHUNKS]
    )
    for row in source_rows:
        candidates.append(
            {
                "rank": row.rank,
                "source": row.url,
                "text": (row.headline or "").strip()
                or str(row.scraped_data or "")[:RETRIEVAL_CHUNK_CHARS],
            }
        )
    return candidates


def _source_markdown(source):
    """Testo markdown estratto dallo scraping, se disponibile."""
    data = source.scraped_data or {}
    if isinstance(data, dict):
        return str(data.get("markdown") or "")
    return str(data)


def _fallback_candidates(company, terms):
    """Scoring lessicale per backend senza FTS (SQLite nei test)."""
    files = list(CompanyFile.objects.filter(company=company))
    files += list(ProductFile.objects.filter(product__company=company))

    candidates = []
    for file_obj in files:
        text = file_obj.content_text or ""
        lower = text.lower()
        score = sum(lower.count(term) for term in terms)
        if score:
            candidates.append(
                {
                    "rank": float(score),
                    "source": file_obj.original_name,
                    "text": _excerpt_around(text, terms),
                }
            )
    for source in company.sources.filter(status="scraped"):
        text = _source_markdown(source)
        lower = text.lower()
        score = sum(lower.count(term) for term in terms)
        if score:
            candidates.append(
                {
                    "rank": float(score),
                    "source": source.url,
                    "text": _excerpt_around(text, terms),
                }
            )
    return candidates


def retrieve_context(company, query):
    """Top excerpt dalla KB del tenant, rankati per rilevanza.

    Cerca nei CompanyFile, in TUTTI i ProductFile della company e nelle
    sorgenti web scrapate (contenuto del sito).
    Ritorna una lista di dict {"source": original_name, "text": excerpt},
    max RETRIEVAL_MAX_CHUNKS elementi e RETRIEVAL_MAX_CHARS caratteri totali.
    I file di altre company sono esclusi per costruzione.
    """
    if not _query_terms(query):
        return []

    if connection.vendor == "postgresql":
        candidates = _fts_candidates(company, query)
    else:
        candidates = _fallback_candidates(company, _query_terms(query))

    candidates.sort(key=lambda item: item["rank"], reverse=True)

    excerpts = []
    total_chars = 0
    for candidate in candidates[:RETRIEVAL_MAX_CHUNKS]:
        remaining = RETRIEVAL_MAX_CHARS - total_chars
        if remaining <= 0:
            break
        text = candidate["text"][:remaining]
        if not text:
            continue
        excerpts.append({"source": candidate["source"], "text": text})
        total_chars += len(text)
    return excerpts


def format_retrieval_block(excerpts):
    """Blocco Markdown con gli excerpt citati, da accodare al system prompt."""
    if not excerpts:
        return ""
    parts = ["## Estratti dai documenti aziendali (knowledge base)"]
    for excerpt in excerpts:
        parts.append(f"### Fonte: {excerpt['source']}\n{excerpt['text']}")
    parts.append("Quando usi queste informazioni, cita sempre la fonte.")
    return "\n\n".join(parts)


def build_messages(history, system_prompt):
    """Messages per il client LLM: system + ultimi HISTORY_MAX_MESSAGES.

    `history` e' la lista di dict {role, content} tenuta in sessione.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": entry["role"], "content": entry["content"]}
        for entry in history[-HISTORY_MAX_MESSAGES:]
        if entry.get("role") in (ROLE_USER, ROLE_ASSISTANT)
    )
    return messages
