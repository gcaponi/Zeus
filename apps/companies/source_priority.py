"""Deterministic source ordering for DNA generation prompts."""

from apps.companies.sector_archetypes import get_archetype_context


DNA_SOURCE_PRIORITY_RULES = """
GERARCHIA VINCOLANTE DELLE FONTI

Quando due informazioni differiscono, usa SEMPRE questa priorita:
1. Risposte esplicite del cliente [SRC:answer].
2. Dati e note dichiarati direttamente dal cliente [SRC:note].
3. Documenti caricati dal cliente [SRC:file:nome-file].
4. Contenuto del sito web [SRC:scrape].
5. Inferenze del modello [hypothesis], mai presentate come fatti.

REGOLE DI CONFLITTO:
- Una fonte inferiore non puo correggere, attenuare o sovrascrivere una fonte superiore.
- Se due fonti dello stesso livello si contraddicono, scrivi "Da chiarire in intervista: ...".
- Non fondere due valori incompatibili in una formulazione ambigua.
- Conserva il marcatore della fonte che sostiene ogni claim interno.
- La sintesi pubblica resta pulita: nessun marcatore [SRC:...] nel testo visibile.
""".strip()


def has_declared_user_context(company) -> bool:
    """Whether client-declared, non-sensitive context is available to the LLM."""
    return bool(
        company.name
        or company.nome_commerciale
        or company.sito_web
        or company.settore_primario
        or company.prodotto_fisico is not None
        or company.cliente_diretto
        or company.custom_frequenza
        or company.installatori_in_filiera is not None
        or company.settore_secondario
        or company.contesto_libero
        or company.company_files.filter(original_name="note-azienda.txt").exists()
    )


def _compact(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _selected_source(company, source=None):
    if source is not None and getattr(source, "scraped_data", None):
        return source
    return (
        company.sources.filter(status="scraped", scraped_data__isnull=False)
        .order_by("-created_at")
        .first()
    )


def _declared_block(company):
    facts = [f"Ragione sociale: {company.name}"]
    if company.nome_commerciale:
        facts.append(f"Nome commerciale: {company.nome_commerciale}")
    if company.sito_web:
        facts.append(f"Sito dichiarato: {company.sito_web}")

    operational = get_archetype_context(company)
    if operational:
        facts.append(operational)
    if company.contesto_libero and company.contesto_libero not in operational:
        facts.append(
            "Contesto libero dichiarato dal cliente:\n"
            f"{_compact(company.contesto_libero, 6000)}"
        )

    note = (
        company.company_files.filter(original_name="note-azienda.txt")
        .order_by("-created_at")
        .first()
    )
    if note and note.content_text.strip():
        facts.append(f"Note dirette del cliente:\n{_compact(note.content_text, 6000)}")
    return "\n".join(facts)


def build_company_generation_context(company, source=None, max_chars=40000):
    """Build source blocks in descending priority without exposing legal contacts."""
    blocks = [
        "## PRIORITA 2 — INFORMAZIONI DICHIARATE DAL CLIENTE [SRC:note]\n"
        + _declared_block(company)
    ]
    remaining = max_chars - len(blocks[0])

    document_blocks = []
    for company_file in company.company_files.exclude(
        original_name="note-azienda.txt"
    ).order_by("-created_at")[:10]:
        if remaining <= 0:
            break
        text = _compact(company_file.content_text, min(6000, remaining))
        if text:
            document_blocks.append(f"### {company_file.original_name}\n{text}")
            remaining -= len(text)
    if document_blocks:
        blocks.append(
            "## PRIORITA 3 — DOCUMENTI CARICATI [SRC:file:nome-file]\n"
            + "\n\n".join(document_blocks)
        )

    selected_source = _selected_source(company, source)
    if selected_source and selected_source.scraped_data and remaining > 0:
        website = _compact(
            selected_source.scraped_data.get("markdown", ""),
            min(12000, remaining),
        )
        if website:
            blocks.append(
                "## PRIORITA 4 — SITO WEB [SRC:scrape]\n"
                f"URL: {selected_source.url}\n{website}"
            )
    return "\n\n".join(blocks)


def build_specialist_generation_context(product, source=None, max_chars=50000):
    """Build specialist raw sources: declarations, files, then website."""
    company = product.company
    blocks = [
        "## PRIORITA 2 — INFORMAZIONI DICHIARATE DAL CLIENTE [SRC:note]\n"
        + _declared_block(company)
    ]
    remaining = max_chars - len(blocks[0])
    document_blocks = []

    for product_file in product.product_files.order_by("-created_at")[:10]:
        if remaining <= 0:
            break
        text = _compact(product_file.content_text, min(7000, remaining))
        if text:
            document_blocks.append(
                f"### File Specialista — {product_file.original_name}\n{text}"
            )
            remaining -= len(text)

    for company_file in company.company_files.exclude(
        original_name="note-azienda.txt"
    ).order_by("-created_at")[:6]:
        if remaining <= 0:
            break
        text = _compact(company_file.content_text, min(4000, remaining))
        if text:
            document_blocks.append(
                f"### File aziendale — {company_file.original_name}\n{text}"
            )
            remaining -= len(text)

    if document_blocks:
        blocks.append(
            "## PRIORITA 3 — DOCUMENTI CARICATI [SRC:file:nome-file]\n"
            + "\n\n".join(document_blocks)
        )

    selected_source = _selected_source(company, source)
    if selected_source and selected_source.scraped_data and remaining > 0:
        website = _compact(
            selected_source.scraped_data.get("markdown", ""),
            min(10000, remaining),
        )
        if website:
            blocks.append(
                "## PRIORITA 4 — SITO WEB [SRC:scrape]\n"
                f"URL: {selected_source.url}\n{website}"
            )
    return "\n\n".join(blocks)
