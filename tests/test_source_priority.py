from types import SimpleNamespace

import pytest

from apps.companies.models import (
    Company,
    CompanyFile,
    LLMCall,
    ProductFile,
    Source,
    Specialista,
)
from apps.companies.source_priority import (
    DNA_SOURCE_PRIORITY_RULES,
    build_company_generation_context,
    build_specialist_generation_context,
)
from apps.companies.views import (
    _global_dna_synthesis,
    _global_product_dna_synthesis,
)


@pytest.fixture
def company_with_ranked_sources():
    company = Company.objects.create(
        schema_name="fonti",
        name="Azienda Fonti Srl",
        nome_commerciale="Fonti",
        partita_iva="12345678901",
        codice_fiscale="ABCDEF12G34H567I",
        rea="MI-1234567",
        pec="pec-segreta@example.test",
        email_contatto="contatto-segreto@example.test",
        sito_web="https://example.test",
        contesto_libero="NOTA DIRETTA DEL CLIENTE",
    )
    CompanyFile.objects.create(
        company=company,
        original_name="catalogo.pdf",
        content_text="DOCUMENTO UFFICIALE CARICATO",
        file_size=100,
    )
    Source.objects.create(
        company=company,
        url="https://example.test",
        status=Source.STATUS_SCRAPED,
        scraped_data={"markdown": "CONTENUTO PUBBLICO DEL SITO"},
    )
    return company


@pytest.mark.django_db
def test_company_context_orders_sources_and_excludes_legal_contacts(
    company_with_ranked_sources,
):
    context = build_company_generation_context(company_with_ranked_sources)

    assert context.index("NOTA DIRETTA DEL CLIENTE") < context.index(
        "DOCUMENTO UFFICIALE CARICATO"
    )
    assert context.index("DOCUMENTO UFFICIALE CARICATO") < context.index(
        "CONTENUTO PUBBLICO DEL SITO"
    )
    assert "12345678901" not in context
    assert "ABCDEF12G34H567I" not in context
    assert "pec-segreta@example.test" not in context
    assert "contatto-segreto@example.test" not in context


@pytest.mark.django_db
def test_specialist_context_places_product_files_before_website(
    company_with_ranked_sources,
):
    product = Specialista.objects.create(
        company=company_with_ranked_sources,
        name="Prodotto prioritario",
        slug="prodotto-prioritario",
        codice="PR-1",
    )
    ProductFile.objects.create(
        product=product,
        original_name="scheda-tecnica.pdf",
        content_text="SPECIFICA TECNICA CARICATA",
        file_size=100,
    )

    context = build_specialist_generation_context(product)

    assert context.index("NOTA DIRETTA DEL CLIENTE") < context.index(
        "SPECIFICA TECNICA CARICATA"
    )
    assert context.index("SPECIFICA TECNICA CARICATA") < context.index(
        "CONTENUTO PUBBLICO DEL SITO"
    )


@pytest.mark.django_db
def test_global_general_synthesis_prompt_enforces_priority(
    company_with_ranked_sources,
):
    questions = [
        SimpleNamespace(
            answer="RISPOSTA ESPLICITA VINCOLANTE",
            code="A1",
            section_key="identita",
            principle="priorita",
            question="Qual e la verita del cliente?",
        )
    ]

    result = _global_dna_synthesis(
        company_with_ranked_sources,
        {"sintesi_cognitiva": "Pre DNA"},
        questions,
    )
    prompt = LLMCall.objects.filter(company=company_with_ranked_sources).latest(
        "created_at"
    ).prompt_text

    assert "rewrite_warning" not in result
    assert DNA_SOURCE_PRIORITY_RULES in prompt
    assert 'scrivi "Da chiarire in intervista: ..."' in prompt
    assert prompt.index("RISPOSTA ESPLICITA VINCOLANTE") < prompt.index(
        "NOTA DIRETTA DEL CLIENTE"
    )
    assert prompt.index("NOTA DIRETTA DEL CLIENTE") < prompt.index(
        "DOCUMENTO UFFICIALE CARICATO"
    )
    assert prompt.index("DOCUMENTO UFFICIALE CARICATO") < prompt.index(
        "CONTENUTO PUBBLICO DEL SITO"
    )


@pytest.mark.django_db
def test_global_specialist_synthesis_prompt_enforces_priority(
    company_with_ranked_sources,
):
    product = Specialista.objects.create(
        company=company_with_ranked_sources,
        name="Prodotto globale",
        slug="prodotto-globale",
        codice="PG-1",
    )
    ProductFile.objects.create(
        product=product,
        original_name="manuale.pdf",
        content_text="FILE SPECIALISTA PRIORITARIO",
        file_size=100,
    )
    questions = [
        SimpleNamespace(
            answer="RISPOSTA SPECIALISTA VINCOLANTE",
            code="D1",
            section_key="specifiche",
            principle="specifica",
            question="Qual e la specifica corretta?",
        )
    ]

    result = _global_product_dna_synthesis(
        product,
        {"identita_tecnica": "Pre DNA specialista"},
        questions,
    )
    prompt = LLMCall.objects.filter(company=company_with_ranked_sources).latest(
        "created_at"
    ).prompt_text

    assert "rewrite_warning" not in result
    assert DNA_SOURCE_PRIORITY_RULES in prompt
    assert prompt.index("RISPOSTA SPECIALISTA VINCOLANTE") < prompt.index(
        "NOTA DIRETTA DEL CLIENTE"
    )
    assert prompt.index("NOTA DIRETTA DEL CLIENTE") < prompt.index(
        "FILE SPECIALISTA PRIORITARIO"
    )
    assert prompt.index("FILE SPECIALISTA PRIORITARIO") < prompt.index(
        "CONTENUTO PUBBLICO DEL SITO"
    )
