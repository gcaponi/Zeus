"""Test per la chat in-app "Testa il tuo agente" (apps.companies.agent + views).

La conversazione NON e' persistita: la storia vive nella Django session.
Su DB resta solo LLMCall (audit costi).
"""
import json
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies import agent as agent_service
from apps.companies import models as company_models
from apps.companies import views
from apps.companies.models import (
    Company,
    DNAGenerale,
    CompanyFile,
    LLMCall,
    Specialista,
    ProductDNA,
    ProductFile,
)


def _approved_company(schema="agent-tenant", name="Agent Co"):
    company = Company.objects.create(schema_name=schema, name=name)
    DNAGenerale.objects.create(
        company=company,
        version=1,
        dna_type=DNAGenerale.TYPE_COMPLETE,
        content={
            "sintesi_cognitiva": "Azienda di lavorazione metalli di precisione.",
            "identita": "Precisione e affidabilita.",
        },
        is_approved=timezone.now(),
    )
    return company


def _active_product(company, name="Celle frigo", slug="celle-frigo", codice="CF-001"):
    product = Specialista.objects.create(
        company=company,
        name=name,
        slug=slug,
        codice=codice,
        status=Specialista.STATUS_ATTIVO,
    )
    ProductDNA.objects.create(
        product=product,
        version=1,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content={
            "sintesi_cognitiva": f"Specialista {name} su misura.",
        },
        is_approved=timezone.now(),
    )
    return product


def _send(rf_with_tenant, text, session):
    request = rf_with_tenant(
        "post",
        reverse("agent-send"),
        data={"message": text},
        session=session,
    )
    request.META["HTTP_HX_REQUEST"] = "true"
    return views.agent_send(request)


@pytest.mark.django_db
class TestNoConversationPersistence:
    def test_conversation_models_removed(self):
        assert not hasattr(company_models, "AgentConversation")
        assert not hasattr(company_models, "AgentMessage")

    def test_send_writes_nothing_but_llm_call(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        session = SessionStore()
        response = _send(rf_with_tenant, "Che materiali lavorate?", session)
        assert response.status_code == 200

        # Nessuna tabella conversazioni: l'unica scrittura e' l'audit LLMCall.
        llm_call = LLMCall.objects.get(company=company)
        logged_prompt = json.loads(llm_call.prompt_text)
        assert logged_prompt[0]["role"] == "system"
        assert agent_service.AGENT_CHAT_MARKER in logged_prompt[0]["content"]
        assert logged_prompt[-1] == {
            "role": "user",
            "content": "Che materiali lavorate?",
        }

        history = session[agent_service.SESSION_HISTORY_KEY]
        assert [entry["role"] for entry in history] == ["user", "assistant"]
        assert history[0]["content"] == "Che materiali lavorate?"
        assert "Risposta di prova dell'agente" in history[1]["content"]


@pytest.mark.django_db
class TestBuildSystemPrompt:
    def test_contains_company_dna_and_rules(self):
        company = _approved_company()
        prompt = agent_service.build_system_prompt(company)
        assert prompt is not None
        assert "DNA Generale — Agent Co" in prompt
        assert "lavorazione metalli" in prompt
        assert "Regole di comportamento" in prompt
        assert "lingua in cui l'utente scrive" in prompt
        assert agent_service.AGENT_CHAT_MARKER in prompt

    def test_gate_returns_none_without_approved_dna(self):
        company = Company.objects.create(schema_name="no-dna", name="No DNA")
        assert agent_service.build_system_prompt(company) is None

        # DNA completo ma non approvato: il gate non si apre.
        DNAGenerale.objects.create(
            company=company,
            version=1,
            dna_type=DNAGenerale.TYPE_COMPLETE,
            content={"sintesi_cognitiva": "bozza"},
        )
        assert agent_service.build_system_prompt(company) is None

        # Pre-DNA approvato non basta: serve il tipo "complete".
        DNAGenerale.objects.filter(company=company).update(is_current=False)
        DNAGenerale.objects.create(
            company=company,
            version=2,
            dna_type=DNAGenerale.TYPE_PRE,
            content={"sintesi_cognitiva": "pre"},
            is_approved=timezone.now(),
        )
        assert agent_service.build_system_prompt(company) is None

    def test_includes_all_active_products_dna(self):
        company = _approved_company()
        _active_product(company, name="Celle frigo", slug="celle-frigo", codice="CF-001")
        _active_product(company, name="Banchi bar", slug="banchi-bar", codice="BB-001")
        prompt = agent_service.build_system_prompt(company)
        assert "DNA Specialista — Celle frigo" in prompt
        assert "DNA Specialista — Banchi bar" in prompt
        assert "Specialista Celle frigo su misura" in prompt
        assert "Specialista Banchi bar su misura" in prompt

    def test_skips_products_not_active_or_without_dna(self):
        company = _approved_company()
        draft = Specialista.objects.create(
            company=company,
            name="Bozza prodotto",
            slug="bozza-prodotto",
            codice="BP-001",
            status=Specialista.STATUS_BOZZA,
        )
        ProductDNA.objects.create(
            product=draft,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"sintesi_cognitiva": "DNA di un prodotto in bozza."},
            is_approved=timezone.now(),
        )
        Specialista.objects.create(
            company=company,
            name="Attivo senza DNA",
            slug="attivo-senza-dna",
            codice="AS-001",
            status=Specialista.STATUS_ATTIVO,
        )
        prompt = agent_service.build_system_prompt(company)
        assert "Bozza prodotto" not in prompt
        assert "Attivo senza DNA" not in prompt


@pytest.mark.django_db
class TestRetrieval:
    def test_ranking_most_relevant_first(self):
        company = _approved_company()
        CompanyFile.objects.create(
            company=company,
            original_name="generico.txt",
            content_text="Note varie sull'officina e la logistica.",
        )
        CompanyFile.objects.create(
            company=company,
            original_name="saldatura.txt",
            content_text="La saldatura TIG e la saldatura MIG: saldatura controllata.",
        )
        excerpts = agent_service.retrieve_context(company, "saldatura certificata")
        assert excerpts
        assert excerpts[0]["source"] == "saldatura.txt"

    def test_cap_total_chars(self):
        company = _approved_company()
        for index in range(4):
            CompanyFile.objects.create(
                company=company,
                original_name=f"doc-{index}.txt",
                content_text=f"acciaio {index} " + ("acciaio inox " * 500),
            )
        excerpts = agent_service.retrieve_context(company, "acciaio")
        total = sum(len(excerpt["text"]) for excerpt in excerpts)
        assert total <= agent_service.RETRIEVAL_MAX_CHARS
        assert len(excerpts) <= agent_service.RETRIEVAL_MAX_CHUNKS

    def test_isolation_between_companies(self):
        company = _approved_company()
        other = Company.objects.create(schema_name="other-tenant", name="Other")
        CompanyFile.objects.create(
            company=other,
            original_name="segreto-altri.txt",
            content_text="saldatura segretissima di un altro tenant",
        )
        other_product = _active_product(other, name="Prodotto altrui", slug="pa", codice="PA-1")
        ProductFile.objects.create(
            product=other_product,
            original_name="scheda-altrui.txt",
            content_text="saldatura scheda tecnica di un altro tenant",
        )
        excerpts = agent_service.retrieve_context(company, "saldatura")
        sources = {excerpt["source"] for excerpt in excerpts}
        assert "segreto-altri.txt" not in sources
        assert "scheda-altrui.txt" not in sources

    def test_includes_files_of_all_company_products(self):
        company = _approved_company()
        product_a = _active_product(company, name="Celle frigo", slug="celle-frigo", codice="CF-001")
        product_b = _active_product(company, name="Banchi bar", slug="banchi-bar", codice="BB-001")
        CompanyFile.objects.create(
            company=company,
            original_name="azienda.txt",
            content_text="verniciatura epossidica aziendale",
        )
        ProductFile.objects.create(
            product=product_a,
            original_name="scheda-celle.txt",
            content_text="verniciatura celle frigo scheda tecnica",
        )
        ProductFile.objects.create(
            product=product_b,
            original_name="scheda-banchi.txt",
            content_text="verniciatura banchi bar scheda tecnica",
        )
        excerpts = agent_service.retrieve_context(company, "verniciatura")
        sources = {excerpt["source"] for excerpt in excerpts}
        assert "azienda.txt" in sources
        assert "scheda-celle.txt" in sources
        assert "scheda-banchi.txt" in sources

    def test_empty_query_returns_nothing(self):
        company = _approved_company()
        CompanyFile.objects.create(
            company=company, original_name="a.txt", content_text="contenuto",
        )
        assert agent_service.retrieve_context(company, "a e o") == []
        assert agent_service.retrieve_context(company, "") == []


@pytest.mark.django_db
class TestBuildMessages:
    def test_history_capped_at_last_10(self):
        history = [
            {"role": "user", "content": f"domanda {index}"} for index in range(14)
        ]
        messages = agent_service.build_messages(history, "SYSTEM")
        assert messages[0] == {"role": "system", "content": "SYSTEM"}
        capped = messages[1:]
        assert len(capped) == agent_service.HISTORY_MAX_MESSAGES
        assert capped[0]["content"] == "domanda 4"
        assert capped[-1]["content"] == "domanda 13"


@pytest.mark.django_db
class TestAgentViews:
    def test_multi_turn_history_in_session_reaches_prompt(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        session = SessionStore()
        for text in ("prima domanda", "seconda domanda"):
            response = _send(rf_with_tenant, text, session)
            assert response.status_code == 200

        history = session[agent_service.SESSION_HISTORY_KEY]
        assert len(history) == 4  # 2 domande + 2 risposte
        assert history[0]["content"] == "prima domanda"
        assert history[2]["content"] == "seconda domanda"

        # Il secondo prompt all'LLM contiene tutta la storia precedente.
        last_call = LLMCall.objects.filter(company=company).latest("id")
        logged_prompt = json.loads(last_call.prompt_text)
        contents = [message["content"] for message in logged_prompt]
        assert "prima domanda" in contents
        assert "seconda domanda" in contents
        assert any("Risposta di prova dell'agente" in c for c in contents)

    def test_history_capped_at_10_messages_in_session(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        for index in range(7):  # 7 turni = 14 messaggi
            response = _send(rf_with_tenant, f"domanda {index}", session)
            assert response.status_code == 200

        history = session[agent_service.SESSION_HISTORY_KEY]
        assert len(history) == agent_service.HISTORY_MAX_MESSAGES
        assert history[-1]["role"] == "assistant"

        last_call = LLMCall.objects.latest("id")
        logged_history = json.loads(last_call.prompt_text)[1:]
        assert len(logged_history) <= agent_service.HISTORY_MAX_MESSAGES

    def test_new_session_starts_empty(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        _send(rf_with_tenant, "prima domanda", session)
        assert session.get(agent_service.SESSION_HISTORY_KEY)

        # Sessione nuova (es. dopo logout): la chat riparte vuota.
        fresh = SessionStore()
        request = rf_with_tenant("get", reverse("agent-chat"), session=fresh)
        with override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls"):
            response = views.agent_chat(request)
        assert response.status_code == 200
        assert "prima domanda" not in response.content.decode()

    def test_agent_clear_empties_history(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        _send(rf_with_tenant, "domanda da dimenticare", session)
        assert session.get(agent_service.SESSION_HISTORY_KEY)

        request = rf_with_tenant(
            "post", reverse("agent-clear"), session=session,
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        response = views.agent_clear(request)
        assert response.status_code == 200
        assert agent_service.SESSION_HISTORY_KEY not in session
        assert "domanda da dimenticare" not in response.content.decode()

    def test_agent_clear_requires_post(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        request = rf_with_tenant("get", reverse("agent-clear"))
        response = views.agent_clear(request)
        assert response.status_code == 405

    def test_send_blocked_without_approved_dna(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="T")
        session = SessionStore()
        response = _send(rf_with_tenant, "ciao", session)
        assert response.status_code == 403
        assert LLMCall.objects.count() == 0
        assert agent_service.SESSION_HISTORY_KEY not in session

    def test_send_requires_message(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        response = _send(rf_with_tenant, "   ", session)
        assert response.status_code == 400
        assert agent_service.SESSION_HISTORY_KEY not in session

    def test_system_prompt_sent_includes_all_active_products(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        _active_product(company, name="Celle frigo", slug="celle-frigo", codice="CF-001")
        _active_product(company, name="Banchi bar", slug="banchi-bar", codice="BB-001")
        session = SessionStore()
        response = _send(rf_with_tenant, "Dimmi dei prodotti", session)
        assert response.status_code == 200
        llm_call = LLMCall.objects.get(company=company)
        system_prompt = json.loads(llm_call.prompt_text)[0]["content"]
        assert "DNA Specialista — Celle frigo" in system_prompt
        assert "DNA Specialista — Banchi bar" in system_prompt

    def test_llm_failure_returns_502(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        request = rf_with_tenant(
            "post",
            reverse("agent-send"),
            data={"message": "ciao"},
            session=session,
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        with patch(
            "apps.companies.views.get_llm_client",
            side_effect=RuntimeError("LLM down"),
        ):
            response = views.agent_send(request)
        assert response.status_code == 502
        assert LLMCall.objects.count() == 0

    @override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls")
    def test_chat_page_gate_empty_state(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="T")
        request = rf_with_tenant("get", reverse("agent-chat"))
        response = views.agent_chat(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Completa prima il DNA" in content

    @override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls")
    def test_chat_page_renders_history_and_clear_button(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        session = SessionStore()
        _send(rf_with_tenant, "vecchia domanda", session)

        request = rf_with_tenant("get", reverse("agent-chat"), session=session)
        response = views.agent_chat(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "agent-chat-log" in content
        assert "vecchia domanda" in content
        assert "Nuova chat" in content
        # Il selettore "Specialista" non esiste piu'.
        assert "agent-product-select" not in content
