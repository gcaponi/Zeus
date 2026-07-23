"""Test per la chat in-app "Testa il tuo agente" (apps.companies.agent + views)."""
import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.companies import agent as agent_service
from apps.companies import views
from apps.companies.models import (
    AgentConversation,
    AgentMessage,
    Company,
    CompanyDNA,
    CompanyFile,
    LLMCall,
    Product,
    ProductDNA,
    ProductFile,
)


def _approved_company(schema="agent-tenant", name="Agent Co"):
    company = Company.objects.create(schema_name=schema, name=name)
    CompanyDNA.objects.create(
        company=company,
        version=1,
        dna_type=CompanyDNA.TYPE_COMPLETE,
        content={
            "sintesi_cognitiva": "Azienda di lavorazione metalli di precisione.",
            "identita": "Precisione e affidabilita.",
        },
        is_approved=timezone.now(),
    )
    return company


def _active_product(company, name="Celle frigo"):
    product = Product.objects.create(
        company=company,
        name=name,
        slug="celle-frigo",
        codice="CF-001",
        status=Product.STATUS_ATTIVO,
    )
    ProductDNA.objects.create(
        product=product,
        version=1,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content={
            "sintesi_cognitiva": "Specialista celle frigorifere su misura.",
        },
        is_approved=timezone.now(),
    )
    return product


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
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"sintesi_cognitiva": "bozza"},
        )
        assert agent_service.build_system_prompt(company) is None

        # Pre-DNA approvato non basta: serve il tipo "complete".
        CompanyDNA.objects.filter(company=company).update(is_current=False)
        CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"sintesi_cognitiva": "pre"},
            is_approved=timezone.now(),
        )
        assert agent_service.build_system_prompt(company) is None

    def test_includes_product_dna_when_selected(self):
        company = _approved_company()
        product = _active_product(company)
        prompt = agent_service.build_system_prompt(company, product)
        assert "DNA Specialista — Celle frigo" in prompt
        assert "celle frigorifere su misura" in prompt

    def test_product_without_dna_falls_back_to_general(self):
        company = _approved_company()
        product = Product.objects.create(
            company=company,
            name="Senza DNA",
            slug="senza-dna",
            status=Product.STATUS_ATTIVO,
        )
        prompt = agent_service.build_system_prompt(company, product)
        assert "non ha ancora un DNA Specialista completo" in prompt


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
        excerpts = agent_service.retrieve_context(company, "saldatura")
        assert all(excerpt["source"] != "segreto-altri.txt" for excerpt in excerpts)

    def test_product_scope_includes_product_and_company_files(self):
        company = _approved_company()
        product = _active_product(company)
        other_product = Product.objects.create(
            company=company,
            name="Altro prodotto",
            slug="altro-prodotto",
            codice="AP-001",
            status=Product.STATUS_ATTIVO,
        )
        CompanyFile.objects.create(
            company=company,
            original_name="azienda.txt",
            content_text="verniciatura epossidica aziendale",
        )
        ProductFile.objects.create(
            product=product,
            original_name="scheda-celle.txt",
            content_text="verniciatura celle frigo scheda tecnica",
        )
        ProductFile.objects.create(
            product=other_product,
            original_name="altro-prodotto.txt",
            content_text="verniciatura prodotto non selezionato",
        )
        excerpts = agent_service.retrieve_context(
            company, "verniciatura", product=product
        )
        sources = {excerpt["source"] for excerpt in excerpts}
        assert "azienda.txt" in sources
        assert "scheda-celle.txt" in sources
        assert "altro-prodotto.txt" not in sources

    def test_no_product_excludes_product_files(self):
        company = _approved_company()
        product = _active_product(company)
        ProductFile.objects.create(
            product=product,
            original_name="scheda-celle.txt",
            content_text="verniciatura celle frigo scheda tecnica",
        )
        excerpts = agent_service.retrieve_context(company, "verniciatura")
        assert excerpts == []

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
        company = _approved_company()
        conversation = AgentConversation.objects.create(company=company)
        for index in range(14):
            AgentMessage.objects.create(
                conversation=conversation,
                role=AgentMessage.ROLE_USER,
                content=f"domanda {index}",
            )
        messages = agent_service.build_messages(conversation, "SYSTEM")
        assert messages[0] == {"role": "system", "content": "SYSTEM"}
        history = messages[1:]
        assert len(history) == agent_service.HISTORY_MAX_MESSAGES
        assert history[0]["content"] == "domanda 4"
        assert history[-1]["content"] == "domanda 13"


@pytest.mark.django_db
class TestAgentViews:
    def test_send_creates_messages_and_llm_call(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        request = rf_with_tenant(
            "post",
            reverse("agent-send"),
            data={"message": "Che materiali lavorate?"},
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        response = views.agent_send(request)
        assert response.status_code == 200

        conversation = AgentConversation.objects.get(company=company)
        messages = list(conversation.messages.all())
        assert len(messages) == 2
        assert messages[0].role == AgentMessage.ROLE_USER
        assert messages[0].content == "Che materiali lavorate?"
        assert messages[1].role == AgentMessage.ROLE_ASSISTANT
        assert "Risposta di prova dell'agente" in messages[1].content

        llm_call = LLMCall.objects.get(company=company)
        assert messages[1].llm_call == llm_call
        logged_prompt = json.loads(llm_call.prompt_text)
        assert logged_prompt[0]["role"] == "system"
        assert agent_service.AGENT_CHAT_MARKER in logged_prompt[0]["content"]
        assert logged_prompt[-1] == {
            "role": "user",
            "content": "Che materiali lavorate?",
        }

    def test_send_blocked_without_approved_dna(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="T")
        request = rf_with_tenant(
            "post", reverse("agent-send"), data={"message": "ciao"},
        )
        response = views.agent_send(request)
        assert response.status_code == 403
        assert AgentMessage.objects.count() == 0
        assert LLMCall.objects.count() == 0

    def test_send_requires_message(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        request = rf_with_tenant(
            "post", reverse("agent-send"), data={"message": "   "},
        )
        response = views.agent_send(request)
        assert response.status_code == 400
        assert AgentMessage.objects.count() == 0

    def test_send_with_product_scopes_conversation(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        product = _active_product(company)
        request = rf_with_tenant(
            "post",
            reverse("agent-send"),
            data={"message": "Dimmi delle celle", "product_id": product.pk},
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        response = views.agent_send(request)
        assert response.status_code == 200
        conversation = AgentConversation.objects.get(company=company)
        assert conversation.product == product
        llm_call = LLMCall.objects.get(company=company)
        system_prompt = json.loads(llm_call.prompt_text)[0]["content"]
        assert "DNA Specialista — Celle frigo" in system_prompt

    def test_tenant_isolation_other_product_not_selectable(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        other_company = _approved_company(schema="other-tenant", name="Other")
        foreign_product = _active_product(other_company, name="Prodotto altrui")
        request = rf_with_tenant(
            "post",
            reverse("agent-send"),
            data={"message": "ciao", "product_id": foreign_product.pk},
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        response = views.agent_send(request)
        assert response.status_code == 200
        conversation = AgentConversation.objects.get(company=company)
        assert conversation.product is None

    def test_second_message_reuses_conversation_and_sends_history(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        for text in ("prima domanda", "seconda domanda"):
            request = rf_with_tenant(
                "post", reverse("agent-send"), data={"message": text},
            )
            request.META["HTTP_HX_REQUEST"] = "true"
            views.agent_send(request)
        assert AgentConversation.objects.filter(company=company).count() == 1
        last_call = LLMCall.objects.filter(company=company).first()
        logged_prompt = json.loads(last_call.prompt_text)
        contents = [message["content"] for message in logged_prompt]
        assert "prima domanda" in contents
        assert "seconda domanda" in contents

    def test_llm_failure_leaves_user_message(self, rf_with_tenant):
        _approved_company(schema="test-tenant")
        request = rf_with_tenant(
            "post", reverse("agent-send"), data={"message": "ciao"},
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        with patch(
            "apps.companies.views.get_llm_client",
            side_effect=RuntimeError("LLM down"),
        ):
            response = views.agent_send(request)
        assert response.status_code == 502

    @override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls")
    def test_chat_page_gate_empty_state(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="T")
        request = rf_with_tenant("get", reverse("agent-chat"))
        response = views.agent_chat(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Completa prima il DNA" in content

    @override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls")
    def test_chat_page_renders_with_approved_dna(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        _active_product(company)
        conversation = AgentConversation.objects.create(company=company)
        AgentMessage.objects.create(
            conversation=conversation,
            role=AgentMessage.ROLE_USER,
            content="vecchia domanda",
        )
        request = rf_with_tenant("get", reverse("agent-chat"))
        response = views.agent_chat(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "agent-chat-log" in content
        assert "vecchia domanda" in content
        assert "Celle frigo" in content

    @override_settings(ZEUS_APP_SHELL_ENABLED=True, ROOT_URLCONF="config.urls")
    def test_chat_page_product_filter(self, rf_with_tenant):
        company = _approved_company(schema="test-tenant")
        product = _active_product(company)
        conversation = AgentConversation.objects.create(
            company=company, product=product,
        )
        AgentMessage.objects.create(
            conversation=conversation,
            role=AgentMessage.ROLE_USER,
            content="domanda sul prodotto",
        )
        request = rf_with_tenant("get", reverse("agent-chat"))
        request.GET = {"product": str(product.pk)}
        response = views.agent_chat(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "domanda sul prodotto" in content
