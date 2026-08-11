"""Test per la chat in-app "ZEUS Guide" (apps.companies.guide + views).

La guida NON ha gate: funziona sempre, anche prima dell'anagrafica e senza DNA.
Percorso e prossimo passo sono deterministici (stato DB); l'LLM spiega soltanto.
La conversazione NON e' persistita: storia nella Django session
(guide_chat_history); su DB resta solo LLMCall (audit costi).
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.companies import agent as agent_service
from apps.companies import guide as guide_service
from apps.companies import views
from apps.companies.llm_client import GUIDE_CHAT_MARKER, MockLLMClient
from apps.companies.models import (
    Company,
    CompanyContact,
    CompanyFile,
    CompanyQuestion,
    CompanySocial,
    DNAGenerale,
    LLMCall,
    ProductDNA,
    ProductFile,
    ProductQuestion,
    Specialista,
    Source,
)
from apps.core.middleware import ANAGRAFICA_ALLOWED_PATHS, CompanyAnagraficaRequiredMiddleware


# ---------------------------------------------------------------------------
# Factory helpers — fanno avanzare l'azienda di una fase alla volta
# ---------------------------------------------------------------------------

def _company(schema="test-tenant", name="Guida Co"):
    return Company.objects.create(schema_name=schema, name=name)


def _complete_anagrafica(company):
    company.nome_commerciale = "Guida Co SRL"
    company.partita_iva = "12345678901"
    company.codice_fiscale = "12345678901"
    company.rea = "MI-123456"
    company.pec = "guidaco@pec.it"
    company.email_contatto = "info@guidaco.it"
    company.sito_web = "https://guidaco.it"
    company.save()
    CompanyContact.objects.create(
        company=company, kind=CompanyContact.KIND_PHONE, value="0212345678",
    )
    CompanyContact.objects.create(
        company=company, kind=CompanyContact.KIND_WHATSAPP, value="3331234567",
    )
    CompanySocial.objects.create(
        company=company, network="instagram", url="https://instagram.com/guidaco",
    )
    return company


def _scraped_source(company):
    return Source.objects.create(
        company=company,
        url="https://guidaco.it",
        status=Source.STATUS_SCRAPED,
        scraped_data={"markdown": "sito aziendale"},
    )


def _company_file(company):
    return CompanyFile.objects.create(
        company=company, original_name="presentazione.txt", content_text="chi siamo",
    )


def _pre_dna(company, version=1):
    return DNAGenerale.objects.create(
        company=company,
        version=version,
        dna_type=DNAGenerale.TYPE_PRE,
        content={"sintesi_cognitiva": "analisi iniziale"},
    )


def _answer_company_questions(company, dna):
    return CompanyQuestion.objects.create(
        company=company,
        dna=dna,
        code="A1",
        principle="Identita'",
        question="Come lavorate i metalli?",
        answer="Con saldatura TIG controllata.",
        answered_at=timezone.now(),
    )


def _approved_dna(company, pre_dna, version=2):
    pre_dna.is_current = False
    pre_dna.save(update_fields=["is_current"])
    return DNAGenerale.objects.create(
        company=company,
        version=version,
        dna_type=DNAGenerale.TYPE_COMPLETE,
        content={"sintesi_cognitiva": "DNA completo"},
        is_approved=timezone.now(),
    )


def _product(company, name="Celle frigo", slug="celle-frigo", codice="CF-001"):
    return Specialista.objects.create(
        company=company,
        name=name,
        slug=slug,
        codice=codice,
        status=Specialista.STATUS_IN_COSTRUZIONE,
    )


def _product_docs_and_answers(product):
    ProductFile.objects.create(
        product=product, original_name="scheda-celle.txt", content_text="scheda tecnica celle",
    )
    pre = ProductDNA.objects.create(
        product=product,
        version=1,
        dna_type=ProductDNA.TYPE_PRE,
        content={"sintesi_cognitiva": "pre prodotto"},
    )
    ProductQuestion.objects.create(
        product=product,
        dna=pre,
        code="A1",
        principle="Tecnica",
        question="Che gas refrigerante usate?",
        answer="R290.",
        answered_at=timezone.now(),
    )
    return pre


def _approved_product_dna(product, pre_dna, version=2):
    pre_dna.is_current = False
    pre_dna.save(update_fields=["is_current"])
    return ProductDNA.objects.create(
        product=product,
        version=version,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content={"sintesi_cognitiva": "DNA specialista"},
        is_approved=timezone.now(),
    )


def _advance_to_agent_ready(company):
    """Porta l'azienda in fondo al percorso (tutte le 10 fasi completate)."""
    _complete_anagrafica(company)
    _scraped_source(company)
    _company_file(company)
    pre_dna = _pre_dna(company)
    _answer_company_questions(company, pre_dna)
    _approved_dna(company, pre_dna)
    product = _product(company)
    product_pre = _product_docs_and_answers(product)
    _approved_product_dna(product, product_pre)
    return product


# ---------------------------------------------------------------------------
# compute_progress — transizioni fase per fase
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestComputeProgress:
    def test_fresh_company_starts_at_anagrafica(self):
        company = _company()
        progress = guide_service.compute_progress(company)
        assert progress["total_count"] == 10
        assert progress["done_count"] == 0
        assert progress["current"]["id"] == "anagrafica"
        statuses = [phase["status"] for phase in progress["phases"]]
        assert statuses[0] == "current"
        assert set(statuses[1:]) == {"todo"}

    def test_phase_entries_expose_widget_contract(self):
        company = _company()
        phase = guide_service.compute_progress(company)["current"]
        assert phase["title"] == "Anagrafica aziendale"
        assert phase["why"]
        assert phase["tips"]
        assert phase["cta_url"] == "/company/anagrafica/"
        assert phase["cta_label"] == "Compila l'anagrafica"

    def test_full_progression_phase_by_phase(self):
        company = _company()
        progress = guide_service.compute_progress(company)
        assert progress["current"]["id"] == "anagrafica"

        _complete_anagrafica(company)
        assert guide_service.compute_progress(company)["current"]["id"] == "sito_web"

        _scraped_source(company)
        assert guide_service.compute_progress(company)["current"]["id"] == "documenti_aziendali"

        _company_file(company)
        assert guide_service.compute_progress(company)["current"]["id"] == "pre_dna"

        pre_dna = _pre_dna(company)
        assert guide_service.compute_progress(company)["current"]["id"] == "domande_dna"

        _answer_company_questions(company, pre_dna)
        assert guide_service.compute_progress(company)["current"]["id"] == "dna_generale"

        _approved_dna(company, pre_dna)
        assert guide_service.compute_progress(company)["current"]["id"] == "specialista"

        product = _product(company)
        assert guide_service.compute_progress(company)["current"]["id"] == "domande_specialista"

        product_pre = _product_docs_and_answers(product)
        assert guide_service.compute_progress(company)["current"]["id"] == "dna_specialista"

        _approved_product_dna(product, product_pre)
        progress = guide_service.compute_progress(company)
        assert progress["current"] is None
        assert progress["done_count"] == progress["total_count"] == 10
        assert {phase["status"] for phase in progress["phases"]} == {"done"}

    def test_dna_questions_phase_requires_all_answered(self):
        company = _company()
        _complete_anagrafica(company)
        _scraped_source(company)
        _company_file(company)
        pre_dna = _pre_dna(company)
        _answer_company_questions(company, pre_dna)
        CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="A2",
            principle="Confini",
            question="Cosa non fate mai?",
        )
        # Una domanda ancora senza risposta: la fase non avanza.
        assert guide_service.compute_progress(company)["current"]["id"] == "domande_dna"

        CompanyQuestion.objects.filter(company=company, code="A2").update(
            answer="Lavorazioni senza disegno tecnico.",
            answered_at=timezone.now(),
        )
        assert guide_service.compute_progress(company)["current"]["id"] == "dna_generale"

    def test_specialist_phase_requires_files_and_answers(self):
        company = _company()
        _complete_anagrafica(company)
        _scraped_source(company)
        _company_file(company)
        pre_dna = _pre_dna(company)
        _answer_company_questions(company, pre_dna)
        _approved_dna(company, pre_dna)
        product = _product(company)

        # Domande risposte ma nessun documento caricato: fase non completata.
        pre = ProductDNA.objects.create(
            product=product, version=1, dna_type=ProductDNA.TYPE_PRE, content={},
        )
        ProductQuestion.objects.create(
            product=product,
            dna=pre,
            code="A1",
            principle="Tecnica",
            question="Tolleranze?",
            answer="+/- 0.1 mm",
            answered_at=timezone.now(),
        )
        assert guide_service.compute_progress(company)["current"]["id"] == "domande_specialista"

        ProductFile.objects.create(
            product=product, original_name="scheda.txt", content_text="scheda",
        )
        assert guide_service.compute_progress(company)["current"]["id"] == "dna_specialista"

    def test_unapproved_dna_does_not_complete_phase(self):
        company = _company()
        _complete_anagrafica(company)
        _scraped_source(company)
        _company_file(company)
        pre_dna = _pre_dna(company)
        _answer_company_questions(company, pre_dna)
        # DNA completo generato ma NON approvato: la fase resta corrente.
        # (Il complete diventa la versione corrente, come nel flusso reale.)
        pre_dna.is_current = False
        pre_dna.save(update_fields=["is_current"])
        DNAGenerale.objects.create(
            company=company,
            version=2,
            dna_type=DNAGenerale.TYPE_COMPLETE,
            content={"sintesi_cognitiva": "bozza"},
        )
        assert guide_service.compute_progress(company)["current"]["id"] == "dna_generale"


# ---------------------------------------------------------------------------
# build_guide_system_prompt
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBuildGuideSystemPrompt:
    def test_contains_marker_role_checklist_and_rules(self):
        company = _company()
        prompt = guide_service.build_guide_system_prompt(company)
        assert GUIDE_CHAT_MARKER in prompt
        assert "ZEUS Guide" in prompt
        assert "Guida Co" in prompt
        assert "Checklist del percorso" in prompt
        assert "Anagrafica aziendale" in prompt
        assert "passo corrente" in prompt
        assert "Non inventare mai il percorso" in prompt
        assert "lingua in cui l'utente scrive" in prompt
        assert "/company/anagrafica/" in prompt

    def test_snapshot_reflects_db_state(self):
        company = _company()
        _complete_anagrafica(company)
        prompt = guide_service.build_guide_system_prompt(company)
        assert "[x] Anagrafica aziendale" in prompt
        assert "[>] Sito web collegato  <- passo corrente" in prompt
        assert "Collega il tuo sito" in prompt

    def test_completed_path_has_no_current_phase(self):
        company = _company()
        _advance_to_agent_ready(company)
        prompt = guide_service.build_guide_system_prompt(company)
        assert "Percorso completato" in prompt
        assert "passo corrente" not in prompt


# ---------------------------------------------------------------------------
# Quick-action "draft" — bozza grounded sui documenti
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDraftBlock:
    def test_empty_when_no_unanswered_question(self):
        company = _company()
        assert guide_service.build_draft_block(company) == ""

    def test_grounded_in_uploaded_docs(self):
        company = _company()
        pre_dna = _pre_dna(company)
        CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="A1",
            principle="Tecnica",
            question="Che materiali lavorate?",
        )
        CompanyFile.objects.create(
            company=company,
            original_name="lavorazioni.txt",
            content_text="Lavoriamo acciaio inox. I materiali principali sono acciaio e alluminio.",
        )
        block = guide_service.build_draft_block(company)
        assert "Che materiali lavorate?" in block
        assert "lavorazioni.txt" in block
        assert "materiali" in block
        assert "[da completare]" in block

    def test_falls_back_to_product_questions(self):
        company = _company()
        product = _product(company)
        pre = ProductDNA.objects.create(
            product=product, version=1, dna_type=ProductDNA.TYPE_PRE, content={},
        )
        ProductQuestion.objects.create(
            product=product,
            dna=pre,
            code="A1",
            principle="Tecnica",
            question="Che temperature raggiungono le celle?",
        )
        assert guide_service.get_current_unanswered_question(company) == (
            "Che temperature raggiungono le celle?"
        )


# ---------------------------------------------------------------------------
# Views: guide_send / guide_clear / guide_state
# ---------------------------------------------------------------------------

def _send(rf_with_tenant, text, session, action=None):
    data = {"message": text}
    if action is not None:
        data["action"] = action
    request = rf_with_tenant("post", reverse("guide-send"), data=data, session=session)
    request.META["HTTP_HX_REQUEST"] = "true"
    return views.guide_send(request)


@pytest.mark.django_db
class TestGuideSend:
    def test_send_always_works_without_anagrafica_and_dna(self, rf_with_tenant):
        # Nessun gate: azienda appena creata, senza anagrafica ne' DNA.
        company = _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "Da dove comincio?", session)
        assert response.status_code == 200

        history = session[guide_service.SESSION_HISTORY_KEY]
        assert [entry["role"] for entry in history] == ["user", "assistant"]
        assert history[0]["content"] == "Da dove comincio?"
        assert "Risposta di prova della guida" in history[1]["content"]

        # Su DB resta solo l'audit LLMCall; il prompt porta il marker guida.
        llm_call = LLMCall.objects.get(company=company)
        logged_prompt = json.loads(llm_call.prompt_text)
        assert logged_prompt[0]["role"] == "system"
        assert GUIDE_CHAT_MARKER in logged_prompt[0]["content"]
        assert "Anagrafica aziendale" in logged_prompt[0]["content"]
        assert logged_prompt[-1] == {"role": "user", "content": "Da dove comincio?"}

        content = response.content.decode()
        assert "guida-message" in content
        assert "Risposta di prova della guida" in content

    def test_history_is_separate_from_agent_chat(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "ciao guida", session)
        assert response.status_code == 200
        assert guide_service.SESSION_HISTORY_KEY == "guide_chat_history"
        assert agent_service.SESSION_HISTORY_KEY not in session

    def test_send_requires_message(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "   ", session)
        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "empty_message"
        assert guide_service.SESSION_HISTORY_KEY not in session
        assert LLMCall.objects.count() == 0

    def test_send_no_tenant(self, rf_with_tenant):
        session = SessionStore()
        request = rf_with_tenant(
            "post", reverse("guide-send"), data={"message": "ciao"}, session=session,
        )
        request.tenant.schema_name = "public"
        response = views.guide_send(request)
        assert response.status_code == 400
        assert b"No tenant" in response.content

    def test_history_capped_at_10_messages_in_session(self, rf_with_tenant):
        _company()
        session = SessionStore()
        for index in range(7):  # 7 turni = 14 messaggi
            response = _send(rf_with_tenant, f"domanda {index}", session)
            assert response.status_code == 200

        history = session[guide_service.SESSION_HISTORY_KEY]
        assert len(history) == agent_service.HISTORY_MAX_MESSAGES
        assert history[-1]["role"] == "assistant"

        last_call = LLMCall.objects.latest("id")
        logged_history = json.loads(last_call.prompt_text)[1:]
        assert len(logged_history) <= agent_service.HISTORY_MAX_MESSAGES

    def test_llm_failure_returns_502(self, rf_with_tenant):
        _company()
        session = SessionStore()
        request = rf_with_tenant(
            "post", reverse("guide-send"), data={"message": "ciao"}, session=session,
        )
        request.META["HTTP_HX_REQUEST"] = "true"
        with patch(
            "apps.companies.views.get_llm_client",
            side_effect=RuntimeError("LLM down"),
        ):
            response = views.guide_send(request)
        assert response.status_code == 502
        assert json.loads(response.content)["error"] == "llm_error"
        assert LLMCall.objects.count() == 0

    def test_non_htmx_redirects_to_referer(self, rf_with_tenant):
        _company()
        session = SessionStore()
        request = rf_with_tenant(
            "post", reverse("guide-send"), data={"message": "ciao"}, session=session,
        )
        request.META["HTTP_REFERER"] = "https://testserver/company/dna/"
        response = views.guide_send(request)
        assert response.status_code == 302
        assert response.url == "https://testserver/company/dna/"

    def test_non_htmx_without_referer_redirects_to_root(self, rf_with_tenant):
        _company()
        session = SessionStore()
        request = rf_with_tenant(
            "post", reverse("guide-send"), data={"message": "ciao"}, session=session,
        )
        response = views.guide_send(request)
        assert response.status_code == 302
        assert response.url == "/"


@pytest.mark.django_db
class TestGuideQuickActions:
    def test_missing_action_without_message(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "", session, action="missing")
        assert response.status_code == 200
        history = session[guide_service.SESSION_HISTORY_KEY]
        assert history[0]["content"] == guide_service.QUICK_ACTION_PROMPTS["missing"]

    def test_why_action_without_message(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "", session, action="why")
        assert response.status_code == 200
        history = session[guide_service.SESSION_HISTORY_KEY]
        assert history[0]["content"] == guide_service.QUICK_ACTION_PROMPTS["why"]

    def test_draft_action_adds_grounded_context(self, rf_with_tenant):
        company = _company()
        pre_dna = _pre_dna(company)
        CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="A1",
            principle="Tecnica",
            question="Che materiali lavorate?",
        )
        CompanyFile.objects.create(
            company=company,
            original_name="materiali.txt",
            content_text="I materiali lavorati sono acciaio inox e alluminio.",
        )
        session = SessionStore()
        response = _send(rf_with_tenant, "", session, action="draft")
        assert response.status_code == 200

        llm_call = LLMCall.objects.get(company=company)
        system_prompt = json.loads(llm_call.prompt_text)[0]["content"]
        assert "Bozza guidata" in system_prompt
        assert "Che materiali lavorate?" in system_prompt
        assert "materiali.txt" in system_prompt

    def test_draft_action_without_open_questions_still_answers(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "", session, action="draft")
        assert response.status_code == 200
        llm_call = LLMCall.objects.latest("id")
        system_prompt = json.loads(llm_call.prompt_text)[0]["content"]
        assert "Bozza guidata" not in system_prompt

    def test_unknown_action_treated_as_normal_message(self, rf_with_tenant):
        _company()
        session = SessionStore()
        response = _send(rf_with_tenant, "domanda libera", session, action="inventata")
        assert response.status_code == 200
        history = session[guide_service.SESSION_HISTORY_KEY]
        assert history[0]["content"] == "domanda libera"


@pytest.mark.django_db
class TestGuideClear:
    def test_clear_empties_history(self, rf_with_tenant):
        _company()
        session = SessionStore()
        _send(rf_with_tenant, "domanda da dimenticare", session)
        assert session.get(guide_service.SESSION_HISTORY_KEY)

        request = rf_with_tenant("post", reverse("guide-clear"), session=session)
        request.META["HTTP_HX_REQUEST"] = "true"
        response = views.guide_clear(request)
        assert response.status_code == 200
        assert guide_service.SESSION_HISTORY_KEY not in session
        content = response.content.decode()
        assert "domanda da dimenticare" not in content
        assert "guida-chat-hint" in content

    def test_clear_requires_post(self, rf_with_tenant):
        _company()
        request = rf_with_tenant("get", reverse("guide-clear"))
        response = views.guide_clear(request)
        assert response.status_code == 405

    def test_clear_no_tenant(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("guide-clear"))
        request.tenant.schema_name = "public"
        response = views.guide_clear(request)
        assert response.status_code == 400


@pytest.mark.django_db
class TestGuideState:
    def test_state_partial_shows_current_phase_and_checklist(self, rf_with_tenant):
        _company()
        request = rf_with_tenant("get", reverse("guide-state"))
        response = views.guide_state(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Anagrafica aziendale" in content
        assert "guida-state-cta" in content
        assert "/company/anagrafica/" in content
        assert "guida-checklist-item--current" in content
        assert "guida-checklist-item--todo" in content
        # Nessuna chiamata LLM per l'opener contestuale.
        assert LLMCall.objects.count() == 0

    def test_state_partial_completed_path(self, rf_with_tenant):
        company = _company()
        _advance_to_agent_ready(company)
        request = rf_with_tenant("get", reverse("guide-state"))
        response = views.guide_state(request)
        assert response.status_code == 200
        content = response.content.decode()
        assert "completato" in content
        assert "guida-checklist-item--done" in content

    def test_state_no_tenant(self, rf_with_tenant):
        request = rf_with_tenant("get", reverse("guide-state"))
        request.tenant.schema_name = "public"
        response = views.guide_state(request)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Mock LLM marker
# ---------------------------------------------------------------------------

class TestGuideMockMarker:
    def test_mock_client_recognises_guide_marker(self):
        result = MockLLMClient().generate(
            messages=[{"role": "system", "content": f"[{GUIDE_CHAT_MARKER}] prompt guida"}],
        )
        assert "guida" in result.text.lower()

    def test_guide_marker_distinct_from_agent_marker(self):
        result = MockLLMClient().generate(
            messages=[{"role": "system", "content": f"[{GUIDE_CHAT_MARKER}] prompt"}],
        )
        assert "Risposta di prova dell'agente" not in result.text


# ---------------------------------------------------------------------------
# Esenzione dal gate anagrafica
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAnagraficaGateExemption:
    def test_guide_path_in_allowed_paths(self):
        assert "/guide/" in ANAGRAFICA_ALLOWED_PATHS

    def test_guide_route_allowed_before_anagrafica(self):
        # Nessuna Company con anagrafica completa: la guida passa comunque.
        Company.objects.create(schema_name="acme", name="Acme")
        middleware = CompanyAnagraficaRequiredMiddleware(lambda req: HttpResponse("ok"))
        request = RequestFactory().get("/guide/state/")
        request.tenant = SimpleNamespace(schema_name="acme")
        request.user = SimpleNamespace(is_authenticated=True)
        response = middleware(request)
        assert response.status_code == 200

    def test_other_routes_still_gated(self):
        Company.objects.create(schema_name="acme", name="Acme")  # anagrafica incompleta
        middleware = CompanyAnagraficaRequiredMiddleware(lambda req: HttpResponse("ok"))
        request = RequestFactory().get("/dashboard/")
        request.tenant = SimpleNamespace(schema_name="acme")
        request.user = SimpleNamespace(is_authenticated=True)
        response = middleware(request)
        assert response.status_code == 302
        assert "/company/anagrafica/" in response.url
