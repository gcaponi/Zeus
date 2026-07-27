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
- Se un'informazione non e' nel DNA ne' nei documenti forniti, dillo chiaramente ("questa informazione non e' nella mia knowledge base") e proponi di mettere l'utente in contatto con un tecnico umano.
- Quando usi informazioni tratte dai documenti, cita la fonte (nome del file).
- Tono: professionale, diretto, concreto. Niente frasi di circostanza."""


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
            "conoscenza riportata qui sotto. Hai a disposizione il DNA Generale "
            "dell'azienda e i DNA Specialistici di tutti i prodotti attivi: se una "
            "domanda riguarda un prodotto specifico, rispondi usando il DNA "
            "Specialistico di quel prodotto."
        ),
        render_sintesi_cognitiva(dna.content or {}, f"DNA Generale — {company.name}"),
    ]

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

    vector = SearchVector("content_text", config="italian")
    search_query = SearchQuery(query, config="italian", search_type="websearch")

    querysets = [
        CompanyFile.objects.filter(company=company),
        ProductFile.objects.filter(product__company=company),
    ]

    candidates = []
    for queryset in querysets:
        rows = (
            queryset.annotate(
                rank=SearchRank(vector, search_query),
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
    return candidates


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
    return candidates


def retrieve_context(company, query):
    """Top excerpt dalla KB del tenant, rankati per rilevanza.

    Cerca nei CompanyFile e in TUTTI i ProductFile della company.
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
