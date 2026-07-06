import json
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as TestClient
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.companies import tasks, views
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
from apps.core.models import Client, Domain, Plan, WorkspaceSubscription
from apps.core.views import WORKSPACE_COOKIE

User = get_user_model()


@pytest.mark.django_db
class TestCompanyModel:
    def test_company_creation(self):
        company = Company.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli SRL",
        )
        assert company.name == "Rossi Metalli SRL"
        assert str(company) == "Rossi Metalli SRL"
        assert company.schema_name == "rossi-metalli"

    def test_unique_schema_name(self):
        Company.objects.create(schema_name="unico", name="A")
        with pytest.raises(Exception):
            Company.objects.create(schema_name="unico", name="B")


@pytest.mark.django_db
class TestQuestionContext:
    def test_company_document_context_uses_site_notes_and_all_documents(self):
        company = Company.objects.create(schema_name="ctxco", name="Context Co")
        Source.objects.create(
            company=company,
            url="https://ctx.example",
            status=Source.STATUS_SCRAPED,
            scraped_data={"markdown": "Sito: filosofia produttiva e valore tecnico."},
        )
        CompanyFile.objects.create(
            company=company,
            original_name="note-azienda.txt",
            content_text="Nota cliente: non vendiamo cataloghi, decidiamo con metodo.",
            file_size=64,
        )
        for index in range(5):
            CompanyFile.objects.create(
                company=company,
                original_name=f"documento-{index}.txt",
                content_text=f"Documento {index}: processo, confini e cultura produttiva.",
                file_size=64,
            )

        context = views._company_document_context(company)

        assert "Sito web scrapato" in context
        assert "Note dirette del cliente" in context
        assert "Documento: documento-0.txt" in context
        assert "Documento: documento-4.txt" in context


@pytest.mark.django_db
class TestCompanyDNAModel:
    def test_dna_creation(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            content={"mission": "test"},
            created_by=user,
        )
        assert dna.is_current is True
        assert dna.version == 1
        assert str(dna) == "TestCo v1"

    def test_unique_current_constraint(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        CompanyDNA.objects.create(
            company=company,
            version=1,
            content={"v": 1},
            is_current=False,
            created_by=user,
        )
        dna2 = CompanyDNA.objects.create(company=company, version=2, content={"v": 2}, created_by=user)
        assert dna2.is_current is True
        # only 1 current per company
        assert CompanyDNA.objects.filter(company=company, is_current=True).count() == 1
        # append-only pattern: mark old as False, create new
        CompanyDNA.objects.filter(company=company, is_current=True).update(is_current=False)
        dna3 = CompanyDNA.objects.create(company=company, version=3, content={"v": 3}, created_by=user)
        assert dna3.is_current is True
        assert CompanyDNA.objects.filter(company=company, is_current=True).count() == 1

    def test_dna_ordering(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        for v in range(1, 4):
            # mark previous as not current before inserting new
            company.dna_versions.filter(is_current=True).update(is_current=False)
            CompanyDNA.objects.create(company=company, version=v, content={"v": v}, created_by=user)
        versions = list(company.dna_versions.all())
        assert versions[0].version == 3
        assert versions[-1].version == 1


@pytest.mark.django_db
class TestCompanyViews:
    def test_company_detail_no_tenant(self):
        """Without request.tenant, returns 400."""
        user = User.objects.create_user(username="x", email="x@x.it", password="pw")
        client = TestClient()
        client.force_login(user)
        response = client.get(reverse("company-detail"))
        assert response.status_code == 400
        assert response.json()["error"] == "no tenant"

    def test_dna_current_creates_company_on_first_call(self, rf_with_tenant):
        request = rf_with_tenant("get", reverse("company-detail"))
        response = views.company_detail(request)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "Test Tenant"
        assert Company.objects.filter(schema_name="test-tenant").exists()

    def test_dna_create_requires_login(self):
        client = TestClient()
        response = client.post(reverse("dna-create"), content_type="application/json")
        assert response.status_code == 302

    def test_dna_create_and_read(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("dna-create"), data={"content": {"mission": "lead"}})
        response = views.dna_create(request)
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["version"] == 1
        assert data["content"]["mission"] == "lead"

        read_req = rf_with_tenant("get", reverse("dna-current"))
        read_resp = views.dna_current(read_req)
        assert read_resp.status_code == 200
        assert json.loads(read_resp.content)["version"] == 1

    def test_dna_append_only(self, rf_with_tenant):
        for v in range(1, 3):
            req = rf_with_tenant("post", reverse("dna-create"), data={"content": {"v": v}})
            views.dna_create(req)

        hist_req = rf_with_tenant("get", reverse("dna-history"))
        hist_resp = views.dna_history(hist_req)
        assert hist_resp.status_code == 200
        assert len(json.loads(hist_resp.content)) == 2

    def test_dna_create_requires_content(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("dna-create"), data={})
        response = views.dna_create(request)
        assert response.status_code == 400

    def test_dna_history_ordered(self, rf_with_tenant):
        for v in range(1, 4):
            req = rf_with_tenant("post", reverse("dna-create"), data={"content": {"version": v}})
            views.dna_create(req)

        hist_req = rf_with_tenant("get", reverse("dna-history"))
        versions = json.loads(views.dna_history(hist_req).content)
        assert len(versions) == 3

    def test_dna_current_404_no_dna(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Naked")
        request = rf_with_tenant("get", reverse("dna-current"))
        response = views.dna_current(request)
        assert response.status_code == 404


@pytest.mark.django_db
class TestSourceModel:
    def test_source_creation(self):
        company = Company.objects.create(schema_name="src-test", name="SrcTest")
        source = Source.objects.create(
            company=company,
            url="https://example.com",
        )
        assert source.status == Source.STATUS_PENDING
        assert str(source) == "https://example.com (pending)"

    def test_source_status_choices(self):
        company = Company.objects.create(schema_name="src-test2", name="SrcTest2")
        s = Source.objects.create(company=company, url="https://x.com", status=Source.STATUS_SCRAPED)
        assert s.status == "scraped"


@pytest.mark.django_db
class TestScraperClient:
    def test_mock_scraper_returns_data(self):
        from apps.companies.scraper import MockScraperClient

        client = MockScraperClient()
        result = client.scrape("https://rossi-metalli.it")
        assert result["title"] == "Rossi Metalli SRL — Prodotti Siderurgici"
        assert "Chi Siamo" in result["markdown"]

    def test_mock_scraper_fail_mode(self):
        from apps.companies.scraper import MockScraperClient

        client = MockScraperClient(fail=True)
        with pytest.raises(RuntimeError, match="Mock failure"):
            client.scrape("https://x.com")

    def test_factory_returns_mock_when_no_key(self):
        from apps.companies.scraper import MockScraperClient, get_scraper

        with patch.dict("os.environ", {}, clear=True):
            client = get_scraper()
            assert isinstance(client, MockScraperClient)

    def test_retry_on_failure(self):
        from apps.companies.scraper import FireCrawlClient

        client = FireCrawlClient(api_key="test-key", base_url="http://localhost:1")
        with pytest.raises(RuntimeError, match="after 3 attempts"):
            client.scrape("https://x.com")


@pytest.mark.django_db
class TestSourceAPI:
    def test_list_requires_login(self):
        client = TestClient()
        response = client.get(reverse("source-list-create"))
        assert response.status_code == 302

    def test_create_source_enqueues_task(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("source-list-create"), data={"url": "https://rossi-metalli.it"})
        response = views.source_list_create(request)
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["url"] == "https://rossi-metalli.it"
        assert data["status"] == "pending"

        source = Source.objects.get(pk=data["id"])
        assert source.status in ("scraped", "failed")

    def test_create_requires_url(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("source-list-create"), data={})
        response = views.source_list_create(request)
        assert response.status_code == 400

    def test_source_detail(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        source = Source.objects.create(company=company, url="https://example.com", status=Source.STATUS_SCRAPED)
        request = rf_with_tenant("get", reverse("source-detail", args=[source.id]))
        response = views.source_detail(request, pk=source.id)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["url"] == "https://example.com"

    def test_source_detail_404(self, rf_with_tenant):
        request = rf_with_tenant("get", reverse("source-detail", args=[999]))
        response = views.source_detail(request, pk=999)
        assert response.status_code == 404

    def test_list_returns_sources(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        Source.objects.create(company=company, url="https://a.it", status=Source.STATUS_SCRAPED)
        Source.objects.create(company=company, url="https://b.it", status=Source.STATUS_FAILED)
        request = rf_with_tenant("get", reverse("source-list-create"))
        response = views.source_list_create(request)
        assert response.status_code == 200
        sources = json.loads(response.content)
        assert len(sources) == 2


@pytest.mark.django_db
class TestScrapeTask:
    def test_task_marks_source_scraped(self):
        company = Company.objects.create(schema_name="task-test", name="TaskTest")
        source = Source.objects.create(company=company, url="https://rossi-metalli.it")

        from apps.companies.tasks import scrape_source
        scrape_source(source.id)

        source.refresh_from_db()
        assert source.status == Source.STATUS_SCRAPED
        assert source.scraped_data is not None
        assert source.scraped_data["title"] == "Rossi Metalli SRL — Prodotti Siderurgici"

    def test_task_handles_missing_source(self):
        from apps.companies.tasks import scrape_source
        scrape_source(999)  # should not raise

    def test_task_marks_failed_on_scrape_error(self):
        company = Company.objects.create(schema_name="task-test2", name="TaskTest2")
        source = Source.objects.create(company=company, url="https://fail.example")

        with patch("apps.companies.tasks.get_scraper") as mock_factory:
            from apps.companies.scraper import MockScraperClient
            mock_factory.return_value = MockScraperClient(fail=True)

            from apps.companies.tasks import scrape_source
            scrape_source(source.id)

        source.refresh_from_db()
        assert source.status == Source.STATUS_FAILED
        assert source.error_msg is not None


@pytest.mark.django_db
class TestLLMClient:
    def test_mock_returns_structured_dna(self):
        from apps.companies.llm_client import MockLLMClient
        client = MockLLMClient()
        result = client.generate("test prompt")
        data = json.loads(result.text)
        assert "identita" in data
        assert "modelli_mentali" in data
        assert "logica_decisionale" in data
        assert isinstance(data["modelli_mentali"]["pilastri"], list)
        assert result.tokens_in > 0
        assert result.cost > 0

    def test_parse_llm_json_extracts_balanced_object(self):
        from apps.companies.llm_client import _parse_llm_json

        text = 'Ecco il JSON richiesto:\n{"outer": {"inner": 1}}\nFine.'

        assert _parse_llm_json(text, context="test") == {"outer": {"inner": 1}}

    def test_generate_with_retry_retries_only_after_parse_failure(self):
        from apps.companies.llm_client import LLMResult, _generate_with_retry

        class BadThenGoodClient:
            def __init__(self):
                self.temperatures = []

            def generate(self, prompt, *, model=None, temperature=None, system_prompt=None):
                self.temperatures.append(temperature)
                if len(self.temperatures) == 1:
                    return LLMResult("not json", 1, 1, 0, 1)
                return LLMResult('{"ok": true}', 1, 1, 0, 1)

        client = BadThenGoodClient()

        result, payload = _generate_with_retry(
            client,
            "prompt",
            temperatures=(0.5, 0.3, 0.2),
            context="test-retry",
        )

        assert payload == {"ok": True}
        assert result.text == '{"ok": true}'
        assert client.temperatures == [0.5, 0.3]

    def test_factory_returns_mock_when_no_key(self):
        from apps.companies.llm_client import MockLLMClient, get_llm_client
        with patch.dict("os.environ", {}, clear=True):
            client = get_llm_client()
            assert isinstance(client, MockLLMClient)

    def test_openai_client_raises_without_key(self):
        from apps.companies.llm_client import OpenAIClient
        with pytest.raises(RuntimeError, match="LLM_API_KEY not set"):
            OpenAIClient(api_key="")


@pytest.mark.django_db
class TestLLMGenerateAPI:
    def test_generate_requires_login(self):
        client = TestClient()
        response = client.post(reverse("dna-generate"), {})
        assert response.status_code == 302

    def test_generate_requires_source_id(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("dna-generate"), data={})
        response = views.dna_generate(request)
        assert response.status_code == 400

    def test_generate_requires_scraped_source(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        source = Source.objects.create(company=company, url="https://x.it", status=Source.STATUS_PENDING)
        request = rf_with_tenant("post", reverse("dna-generate"), data={"source_id": source.id})
        response = views.dna_generate(request)
        assert response.status_code == 400
        assert b"not scraped" in response.content

    def test_generate_creates_dna_and_llm_call(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        source = Source.objects.create(
            company=company, url="https://rossi-metalli.it",
            status=Source.STATUS_SCRAPED,
            scraped_data={"markdown": "# Rossi Metalli\nAzienda siderurgica."},
        )
        request = rf_with_tenant("post", reverse("dna-generate"), data={"source_id": source.id})
        response = views.dna_generate(request)
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["version"] == 1
        assert data["content"]["identita"]["postura"] != ""
        assert data["tokens_in"] > 0

        assert LLMCall.objects.count() == 1
        call = LLMCall.objects.first()
        assert call.tokens_in > 0
        assert "deepseek" in call.model_name

    def test_generate_404_for_wrong_company(self, rf_with_tenant):
        other = Company.objects.create(schema_name="other", name="Other")
        source = Source.objects.create(company=other, url="https://x.it", status=Source.STATUS_SCRAPED)
        request = rf_with_tenant("post", reverse("dna-generate"), data={"source_id": source.id})
        response = views.dna_generate(request)
        assert response.status_code == 404


@pytest.mark.django_db
class TestPipelineAPI:
    def test_create_requires_login(self):
        client = TestClient()
        response = client.post(reverse("pipeline-run-create"), {})
        assert response.status_code == 302

    def test_create_requires_source_id(self, rf_with_tenant):
        request = rf_with_tenant("post", reverse("pipeline-run-create"), data={})
        response = views.pipeline_run_create(request)
        assert response.status_code == 400

    def test_create_enqueues_pipeline(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        source = Source.objects.create(company=company, url="https://rossi-metalli.it")
        request = rf_with_tenant(
            "post",
            reverse("pipeline-run-create"),
            data={"source_id": source.id},
        )
        response = views.pipeline_run_create(request)
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["status"] == "pending"

        run = PipelineRun.objects.get(pk=data["id"])
        assert run.status in ("running", "completed")

    def test_suspended_workspace_cannot_create_pipeline(self, rf_with_tenant, monkeypatch):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=Plan.get_default(),
            status=WorkspaceSubscription.STATUS_SUSPENDED,
        )
        company = Company.objects.create(schema_name="test-tenant", name="T")
        source = Source.objects.create(company=company, url="https://rossi-metalli.it")

        request = rf_with_tenant(
            "post",
            reverse("pipeline-run-create"),
            data={"source_id": source.id},
        )
        response = views.pipeline_run_create(request)

        assert response.status_code == 403
        assert "Workspace sospeso" in response.content.decode()
        assert PipelineRun.objects.count() == 0

    def test_detail_returns_status(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="T")
        run = PipelineRun.objects.create(company=company, status=PipelineRun.STATUS_COMPLETED)
        request = rf_with_tenant("get", reverse("pipeline-run-detail", args=[run.id]))
        response = views.pipeline_run_detail(request, pk=run.id)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "completed"

    def test_detail_404(self, rf_with_tenant):
        request = rf_with_tenant("get", reverse("pipeline-run-detail", args=[999]))
        response = views.pipeline_run_detail(request, pk=999)
        assert response.status_code == 404


@pytest.mark.django_db
class TestPipelineTask:
    def test_pipeline_completes_end_to_end(self):
        company = Company.objects.create(schema_name="pipe-e2e", name="PipeE2E")
        source = Source.objects.create(
            company=company, url="https://rossi-metalli.it",
            status=Source.STATUS_PENDING,
        )
        run = PipelineRun.objects.create(company=company, source=source)

        from apps.companies.tasks import run_pipeline
        run_pipeline(run.id)

        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_COMPLETED
        assert run.current_step == "4/4: Completamento"
        assert company.dna_versions.count() == 1
        assert LLMCall.objects.count() == 1

    def test_pipeline_marks_failed_on_error(self):
        company = Company.objects.create(schema_name="pipe-fail", name="PipeFail")
        source = Source.objects.create(company=company, url="https://fail.example")
        run = PipelineRun.objects.create(company=company, source=source)

        with patch("apps.companies.tasks.get_scraper") as mock_scraper_factory:
            from apps.companies.scraper import MockScraperClient
            mock_scraper_factory.return_value = MockScraperClient(fail=True)

            from apps.companies.tasks import run_pipeline
            run_pipeline(run.id)

        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_FAILED
        assert run.error_msg is not None


@pytest.mark.django_db
class TestDNAFeedback:
    def test_recalculate_no_feedback(self):
        company = Company.objects.create(schema_name="fb-1", name="FB1")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        assert CompanyDNA.recalculate_confidence(dna.id) is None

    def test_recalculate_single_feedback(self):
        company = Company.objects.create(schema_name="fb-2", name="FB2")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        DNAFeedback.objects.create(dna=dna, rating=4)
        assert CompanyDNA.recalculate_confidence(dna.id) == 4.0

    def test_recalculate_multiple_feedback_recency_weighted(self):
        company = Company.objects.create(schema_name="fb-3", name="FB3")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        DNAFeedback.objects.create(dna=dna, rating=1)
        DNAFeedback.objects.create(dna=dna, rating=5)
        score = CompanyDNA.recalculate_confidence(dna.id)
        # With equal timestamps ordering is undefined; score must be between 1 and 5
        assert 1 < score < 5

    def test_feedback_api_invalid_rating(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="FB API")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        req = rf_with_tenant("post", f"/api/company/dna/{dna.id}/feedback/", {"rating": 6})
        from apps.companies.views import dna_feedback
        resp = dna_feedback(req, pk=dna.id)
        assert resp.status_code == 400
        assert "rating must be 1-5" in resp.content.decode()

    def test_feedback_api_success(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="FB OK")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        req = rf_with_tenant("post", f"/api/company/dna/{dna.id}/feedback/", {
            "rating": 5, "comment": "Perfetto",
        })
        from apps.companies.views import dna_feedback
        resp = dna_feedback(req, pk=dna.id)
        data = json.loads(resp.content)
        assert resp.status_code == 201
        assert data["rating"] == 5
        assert data["comment"] == "Perfetto"
        assert data["confidence_score"] == 5.0
        dna.refresh_from_db()
        assert dna.confidence_score == 5.0

    def test_feedback_api_wrong_tenant(self, rf_with_tenant):
        req = rf_with_tenant("post", "/api/company/dna/999/feedback/", {"rating": 3})
        from apps.companies.views import dna_feedback
        resp = dna_feedback(req, pk=999)
        assert resp.status_code == 404

    def test_score_persisted_on_dna(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="FB Save")
        dna = CompanyDNA.objects.create(company=company, version=1, content={})
        DNAFeedback.objects.create(dna=dna, rating=2)
        DNAFeedback.objects.create(dna=dna, rating=4)
        dna.confidence_score = CompanyDNA.recalculate_confidence(dna.id)
        dna.save(update_fields=["confidence_score"])
        dna.refresh_from_db()
        assert dna.confidence_score is not None


@pytest.mark.django_db
class TestOnboardingViews:
    def test_public_onboarding_uses_workspace_cookie(self, monkeypatch):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        Domain.objects.create(
            domain="test-tenant.zeus.cais.uno",
            tenant=tenant,
            is_primary=True,
        )
        req = RequestFactory().get("/onboarding/")
        req.tenant = type("PublicTenant", (), {"schema_name": "public"})()
        req.user = AnonymousUser()
        req.COOKIES[WORKSPACE_COOKIE] = "test-tenant.zeus.cais.uno"

        resp = views.onboarding_index(req)

        assert resp.status_code == 302
        assert resp["Location"] == "https://test-tenant.zeus.cais.uno/onboarding/"

    def test_onboarding_index_shows_source_form(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        req = rf_with_tenant("get", "/onboarding/")
        resp = views.onboarding_index(req)
        assert resp.status_code == 200
        assert b"URL del sito aziendale" in resp.content
        assert b"cais.uno" in resp.content
        assert b"hx-post" in resp.content
        assert b"hx-target=\"#onboarding-step\"" in resp.content

    def test_onboarding_revise_shows_prefilled_source_form(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        Source.objects.create(company=company, url="https://cais.uno", status=Source.STATUS_SCRAPED)
        CompanyFile.objects.create(
            company=company,
            original_name="note-azienda.txt",
            content_text="Nota gia inserita.",
        )
        CompanyFile.objects.create(
            company=company,
            original_name="profilo.pdf",
            content_text="Documento esistente.",
        )
        CompanyDNA.objects.create(company=company, version=1, content={"identita": "test"})

        req = rf_with_tenant("get", "/onboarding/?revise=1")
        resp = views.onboarding_index(req)

        assert resp.status_code == 200
        assert b"Revisione non distruttiva" in resp.content
        assert b'value="https://cais.uno"' in resp.content
        assert b"Nota gia inserita" in resp.content
        assert b"profilo.pdf" in resp.content
        assert b"Continua alle risposte" in resp.content

    def test_onboarding_source_create_detects_no_initial_changes(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        Source.objects.create(company=company, url="https://cais.uno", status=Source.STATUS_SCRAPED)
        CompanyFile.objects.create(
            company=company,
            original_name="note-azienda.txt",
            content_text="Nota stabile.",
        )
        CompanyDNA.objects.create(company=company, version=1, content={"identita": "test"})

        req = rf_with_tenant("post", "/onboarding/source/", {
            "url": "https://cais.uno",
            "company_notes": "Nota stabile.",
        }, form=True)
        req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.onboarding_source_create(req)

        assert resp.status_code == 200
        assert b"Nessuna modifica rilevata" in resp.content
        assert Source.objects.filter(company=company).count() == 1
        assert PipelineRun.objects.filter(company=company).count() == 0

    def test_onboarding_source_create_generates_dna(self, rf_with_tenant):
        from apps.companies.llm_client import MockLLMClient

        with patch("apps.companies.tasks.get_llm_client", return_value=MockLLMClient()):
            req = rf_with_tenant(
                "post",
                "/onboarding/source/",
                {"url": "https://rossi-metalli.it"},
                form=True,
            )
            req.META["HTTP_HX_REQUEST"] = "true"
            resp = views.onboarding_source_create(req)

        assert resp.status_code == 200
        assert b"DNA Aziendale generato" in resp.content
        assert Company.objects.get(schema_name="test-tenant").sources.count() == 1
        assert PipelineRun.objects.filter(company__schema_name="test-tenant").count() == 1
        assert CompanyDNA.objects.filter(company__schema_name="test-tenant").count() == 1

    def test_onboarding_source_create_accepts_bare_domain(self, rf_with_tenant):
        from apps.companies.llm_client import MockLLMClient

        with patch("apps.companies.tasks.get_llm_client", return_value=MockLLMClient()):
            req = rf_with_tenant(
                "post",
                "/onboarding/source/",
                {"url": "cais.uno"},
                form=True,
            )
            req.META["HTTP_HX_REQUEST"] = "true"
            resp = views.onboarding_source_create(req)

        assert resp.status_code == 200
        source = Source.objects.get(company__schema_name="test-tenant")
        assert source.url == "https://cais.uno"

    def test_onboarding_source_create_uses_company_notes(self, rf_with_tenant):
        from apps.companies.llm_client import MockLLMClient

        with patch("apps.companies.tasks.get_llm_client", return_value=MockLLMClient()):
            req = rf_with_tenant("post", "/onboarding/source/", {
                "url": "https://rossi-metalli.it",
                "company_notes": "Certificazione ISO 9001 e tempi rapidi.",
            }, form=True)
            req.META["HTTP_HX_REQUEST"] = "true"
            resp = views.onboarding_source_create(req)

        assert resp.status_code == 200
        company = Company.objects.get(schema_name="test-tenant")
        assert company.company_files.count() == 1
        assert "Certificazione ISO 9001" in company.company_files.first().content_text
        assert "Certificazione ISO 9001" in LLMCall.objects.first().prompt_text

    def test_company_file_quota_blocks_onboarding(self, rf_with_tenant, monkeypatch):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        WorkspaceSubscription.objects.create(client=tenant, plan=Plan.get_default())
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        five_mb = 5 * 1024 * 1024
        CompanyFile.objects.create(
            company=company,
            original_name="big-doc.pdf",
            content_text="test",
            file_size=five_mb,
        )

        req = rf_with_tenant("post", "/onboarding/source/", {
            "url": "https://rossi-metalli.it",
            "company_notes": "Nuovo documento oltre quota.",
        }, form=True)
        req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.onboarding_source_create(req)

        assert resp.status_code == 403
        assert b"Limite" in resp.content
        assert Source.objects.count() == 0

    def test_onboarding_source_create_invalid_url(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        req = rf_with_tenant("post", "/onboarding/source/", {}, form=True)
        req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.onboarding_source_create(req)
        assert resp.status_code == 400
        assert b"Inserisci un URL valido" in resp.content

    def test_onboarding_source_create_non_htmx_redirects_to_full_page(self, rf_with_tenant):
        from apps.companies.llm_client import MockLLMClient

        with patch("apps.companies.tasks.get_llm_client", return_value=MockLLMClient()):
            req = rf_with_tenant(
                "post",
                "/onboarding/source/",
                {"url": "https://rossi-metalli.it"},
                form=True,
            )
            resp = views.onboarding_source_create(req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("onboarding-index")

    def test_onboarding_status_completed(self, rf_with_tenant):
        from apps.companies.llm_client import MockLLMClient

        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        source = Source.objects.create(
            company=company, url="https://rossi-metalli.it",
            status=Source.STATUS_SCRAPED,
            scraped_data={"markdown": "Rossi Metalli SRL produce acciai speciali."},
        )
        run = PipelineRun.objects.create(company=company, source=source, status=PipelineRun.STATUS_PENDING)
        with patch("apps.companies.tasks.get_llm_client", return_value=MockLLMClient()):
            from apps.companies.tasks import run_pipeline
            run_pipeline(run.id)
        req = rf_with_tenant("get", f"/onboarding/status/{run.id}/")
        resp = views.onboarding_status(req, pk=run.id)
        data = json.loads(resp.content)
        assert resp.status_code == 200
        assert data["status"] == "completed"
        assert data["dna_id"] is not None

    def test_onboarding_status_htmx_returns_progress_html(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        source = Source.objects.create(
            company=company,
            url="https://rossi-metalli.it",
            status=Source.STATUS_PENDING,
        )
        run = PipelineRun.objects.create(
            company=company,
            source=source,
            status=PipelineRun.STATUS_RUNNING,
            current_step="scrape",
        )
        req = rf_with_tenant("get", f"/onboarding/status/{run.id}/")
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.onboarding_status(req, pk=run.id)

        assert resp.status_code == 200
        assert b"Analisi Neurale in corso" in resp.content
        assert b'"current_step"' not in resp.content

    def test_onboarding_status_htmx_returns_dna_html(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        source = Source.objects.create(
            company=company,
            url="https://rossi-metalli.it",
            status=Source.STATUS_SCRAPED,
        )
        run = PipelineRun.objects.create(
            company=company,
            source=source,
            status=PipelineRun.STATUS_COMPLETED,
        )
        CompanyDNA.objects.create(
            company=company,
            version=1,
            content={"chi_siamo": "Rossi Metalli"},
        )
        req = rf_with_tenant("get", f"/onboarding/status/{run.id}/")
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.onboarding_status(req, pk=run.id)

        assert resp.status_code == 200
        assert b"DNA Aziendale generato" in resp.content
        assert b'"current_step"' not in resp.content

    def test_onboarding_dna_reset_clears_data(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        source = Source.objects.create(
            company=company,
            url="https://rossi-metalli.it",
            status=Source.STATUS_SCRAPED,
        )
        PipelineRun.objects.create(company=company, source=source, status=PipelineRun.STATUS_COMPLETED)
        dna = CompanyDNA.objects.create(company=company, version=1, content={"chi_siamo": "test"})
        CompanyQuestion.objects.create(
            company=company,
            dna=dna,
            code="A1",
            section_key="chi_siamo",
            principle="test",
            question="test?",
        )
        CompanyFile.objects.create(company=company, original_name="doc.txt", content_text="test")

        req = rf_with_tenant("post", reverse("onboarding-dna-reset"), form=True)
        resp = views.onboarding_dna_reset(req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("onboarding-index")
        assert CompanyDNA.objects.filter(company=company).count() == 0
        assert CompanyQuestion.objects.filter(company=company).count() == 0
        assert CompanyFile.objects.filter(company=company).count() == 0
        assert PipelineRun.objects.filter(company=company).count() == 0
        assert Source.objects.filter(company=company).count() == 0


@pytest.mark.django_db
class TestDNAQuestions:
    def _make_pre_dna(self, company):
        return CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            content={
                "identita": {"postura": "Azienda manifatturiera B2B.", "convinzioni": ["qualita"]},
                "modelli_mentali": {
                    "pilastri": ["Qualita", "Rapidita"],
                    "sequenza_di_lettura": "parte dal caso d'uso",
                },
                "nucleo_tecnico": {
                    "approccio_distintivo": "Meccanica di precisione.",
                    "trade_off_scelti": "rapidita con controllo qualita",
                    "famiglie_prodotto": ["componenti"],
                },
                "confini": {
                    "anti_pattern": ["promesse non verificabili"],
                    "richieste_rifiutate": "fuori tolleranza non validato",
                },
                "tono": {
                    "registro": "tecnico-accessibile",
                    "esempi": [{
                        "sbagliato": "siamo i migliori",
                        "giusto": "validiamo il vincolo tecnico",
                    }],
                },
                "logica_decisionale": {
                    "filosofia_custom": "custom solo se ha senso tecnico",
                    "escalation": "coinvolgere tecnico senior",
                },
            },
        )

    def test_questions_page_creates_ten_a_questions(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        self._make_pre_dna(company)

        req = rf_with_tenant("get", reverse("dna-questions"))
        resp = views.dna_questions(req)

        assert resp.status_code == 200
        assert CompanyQuestion.objects.filter(company=company).count() == 10
        assert b"A1" in resp.content
        assert b"A10" in resp.content
        first_question = CompanyQuestion.objects.filter(company=company).first()
        assert first_question.plan_slug == Plan.SLUG_STARTER
        assert first_question.answer_depth == "generica"
        assert "almeno 2 pagine" in first_question.answer_guidance
        assert LLMCall.objects.filter(company=company).count() == 1

    def test_professional_questions_use_context_and_documents(
        self,
        rf_with_tenant,
        monkeypatch,
    ):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_PROFESSIONAL,
            defaults=Plan.default_values(Plan.SLUG_PROFESSIONAL),
        )
        WorkspaceSubscription.objects.create(client=tenant, plan=plan)
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        self._make_pre_dna(company)
        CompanyFile.objects.create(
            company=company,
            original_name="certificazioni.txt",
            content_text="ISO 9001 e tracciabilita dei lotti.",
        )

        resp = views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))

        assert resp.status_code == 200
        questions = list(CompanyQuestion.objects.filter(company=company))
        assert questions[0].plan_slug == Plan.SLUG_PROFESSIONAL
        assert questions[0].answer_depth == "mirata"
        assert any("ISO 9001" in question.question for question in questions)

    def test_enterprise_questions_use_analyst_depth(
        self,
        rf_with_tenant,
        monkeypatch,
    ):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_ENTERPRISE,
            defaults=Plan.default_values(Plan.SLUG_ENTERPRISE),
        )
        WorkspaceSubscription.objects.create(client=tenant, plan=plan)
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        self._make_pre_dna(company)

        resp = views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))

        assert resp.status_code == 200
        question = CompanyQuestion.objects.filter(company=company).first()
        assert question.plan_slug == Plan.SLUG_ENTERPRISE
        assert question.answer_depth == "analitica"
        assert "mentalita" in question.question
        assert "mentalita" in question.answer_guidance

    def test_submit_answers_creates_complete_dna(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)

        get_req = rf_with_tenant("get", reverse("dna-questions"))
        views.dna_questions(get_req)
        questions = list(pre_dna.questions.all())
        data = {f"answer_{question.id}": f"Risposta {question.code}" for question in questions}

        post_req = rf_with_tenant("post", reverse("dna-questions"), data, form=True)
        resp = views.dna_questions(post_req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-generating")
        complete_dna = CompanyDNA.objects.get(company=company, dna_type=CompanyDNA.TYPE_COMPLETE)
        assert complete_dna.version == 2
        assert complete_dna.is_current is True
        assert complete_dna.is_export_ready() is False
        assert len(complete_dna.content["questionario_a1_a20"]) == 10
        assert complete_dna.content["profilo_questionario"]["plan"] == Plan.SLUG_STARTER
        assert complete_dna.content["profilo_questionario"]["starter_minimum_pages"] == 2
        identita_text = json.dumps(complete_dna.content["identita"], ensure_ascii=False)
        assert "sintetizzata" in identita_text or "sintesi" in identita_text.lower()
        assert "Risposta A1" not in identita_text
        assert "Approfondimenti cliente" not in identita_text
        pre_dna.refresh_from_db()
        assert pre_dna.is_current is False

    def test_safe_merge_synthesis_normalizes_legacy_layer_aliases(self):
        original = {"sintesi_cognitiva": "Sintesi precedente"}
        synthesis = {
            "sintesi_cognitiva": "Sintesi aggiornata",
            "identita_e_promessa": "Identita generata",
            "confini_produttivi": "Confini generati",
            "tono_comunicativo": "Tono generato",
        }

        merged = views._safe_merge_synthesis(original, synthesis)

        assert merged["sintesi_cognitiva"] == "Sintesi aggiornata"
        assert merged["identita"] == "Identita generata"
        assert merged["confini"] == "Confini generati"
        assert merged["tono"] == "Tono generato"
        assert "identita_e_promessa" not in merged

    def test_submit_same_answers_does_not_regenerate_complete_dna(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))
        questions = list(pre_dna.questions.all())
        for question in questions:
            question.answer = f"Risposta {question.code}"
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        company.dna_versions.filter(is_current=True).update(is_current=False)
        CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "Completo"},
        )
        data = {f"answer_{question.id}": question.answer for question in questions}

        req = rf_with_tenant("post", reverse("dna-questions"), data, form=True)
        resp = views.dna_questions(req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-review")
        assert CompanyDNA.objects.filter(
            company=company,
            dna_type=CompanyDNA.TYPE_COMPLETE,
        ).count() == 1

    def test_changed_answers_regenerate_and_wait_for_new_complete_version(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))
        questions = list(pre_dna.questions.all())
        for question in questions:
            question.answer = f"Risposta {question.code}"
            question.answered_at = timezone.now()
            question.save(update_fields=["answer", "answered_at"])
        company.dna_versions.filter(is_current=True).update(is_current=False)
        CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "Completo vecchio"},
        )
        data = {f"answer_{question.id}": question.answer for question in questions}
        data[f"answer_{questions[0].id}"] = "Risposta modificata"

        req = rf_with_tenant("post", reverse("dna-questions"), data, form=True)
        req.session = {}
        resp = views.dna_questions(req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-generating")
        assert req.session["pending_complete_min_version"] == 3
        assert CompanyDNA.objects.filter(
            company=company,
            dna_type=CompanyDNA.TYPE_COMPLETE,
        ).count() == 2

        wait_req = rf_with_tenant("get", reverse("dna-generating"))
        wait_req.session = req.session
        wait_resp = views.dna_generating(wait_req)

        assert wait_resp.status_code == 302
        assert wait_resp["Location"] == reverse("dna-review")
        assert "pending_complete_min_version" not in wait_req.session

    def test_dna_generating_waits_then_hx_redirects(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")

        resp = views.dna_generating(rf_with_tenant("get", reverse("dna-generating")))
        assert resp.status_code == 200
        assert b"Stiamo generando il DNA" in resp.content

        hx_req = rf_with_tenant("get", reverse("dna-generating"))
        hx_req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.dna_generating(hx_req)
        assert resp.status_code == 204
        assert "HX-Redirect" not in resp

        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Completo"},
        )

        hx_req = rf_with_tenant("get", reverse("dna-generating"))
        hx_req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.dna_generating(hx_req)
        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("dna-review")

        resp = views.dna_generating(rf_with_tenant("get", reverse("dna-generating")))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-review")

    def test_dna_sections_hide_nested_description_keys(self):
        sections = views._dna_sections({
            "identita": {"descrizione": "Testo identita pulito. [SRC:scrape]"},
            "modelli_mentali": [
                {"descrizione": "Qualita [SRC:answer]"},
                {"descrizione": "Rapidita [SRC:file]"},
            ],
            "nucleo_tecnico": {"testo": "Testo nucleo pulito. [SRC:note]"},
            "confini": {"value": "Testo confini pulito."},
            "tono": {"description": "Testo tono pulito."},
            "logica_decisionale": {"contenuto": "Testo logica pulito."},
        })

        values = {section["key"]: section["value"] for section in sections}
        assert values["identita"] == "Testo identita pulito."
        assert values["modelli_mentali"] == "Qualita\n\nRapidita"
        assert values["nucleo_tecnico"] == "Testo nucleo pulito."
        assert values["confini"] == "Testo confini pulito."
        assert values["tono"] == "Testo tono pulito."
        assert values["logica_decisionale"] == "Testo logica pulito."
        assert "descrizione" not in values["identita"]
        assert "{" not in values["identita"]
        assert "[SRC:" not in " ".join(values.values())

    def test_public_document_uses_sintesi_cognitiva_without_layer_titles(self):
        content = {
            "sintesi_cognitiva": "CAIS integra l'intelligenza artificiale nei processi operativi. [SRC:scrape]",
            "identita": {"postura": "Test identita"},
            "modelli_mentali": {"pilastri": ["Test pilastro"]},
        }

        public_document = views._dna_public_document(content)

        assert public_document == "CAIS integra l'intelligenza artificiale nei processi operativi."
        assert "[SRC:" not in public_document
        assert "Test identita" not in public_document
        assert "modelli_mentali" not in public_document

    def test_public_document_falls_back_to_layer_text_without_labels(self):
        content = {
            "identita": {"postura": "Test identita"},
            "tono": {"registro": "Test tono"},
        }

        public_document = views._dna_public_document(content)

        assert "Test identita" in public_document
        assert "Test tono" in public_document
        assert "Chi siamo" not in public_document
        assert "Il nostro tono" not in public_document

    def test_final_document_combines_synthesis_and_layers_without_labels(self):
        content = {
            "sintesi_cognitiva": "Sintesi finale per il cliente. [SRC:scrape]",
            "identita": "Identita narrativa completa. [SRC:file]",
            "modelli_mentali": "Metodo decisionale completo. [SRC:note]",
        }

        final_document = views._dna_final_document(content)

        assert "Sintesi finale per il cliente." in final_document
        assert "Identita narrativa completa." in final_document
        assert "Metodo decisionale completo." in final_document
        assert "[SRC:" not in final_document
        assert "Chi siamo" not in final_document
        assert "Come ragioniamo" not in final_document

    def test_document_paragraphs_preserve_final_document_blocks(self):
        document = "Prima parte.\n\nSeconda parte con\nlinea interna.\n\n\nTerza parte."

        paragraphs = views._document_paragraphs(document)

        assert paragraphs == [
            "Prima parte.",
            "Seconda parte con linea interna.",
            "Terza parte.",
        ]

    def test_document_paragraphs_split_long_blocks_on_sentence_boundaries(self):
        sentence = "Questo principio operativo deve restare leggibile anche dentro la visualizzazione finale."
        # 25 sentences -> ~1750 chars -> exceeds 1600 threshold, must split
        document = " ".join([sentence] * 25)

        paragraphs = views._document_paragraphs(document)

        assert len(paragraphs) > 1
        assert all(paragraph.endswith(".") for paragraph in paragraphs)
        assert " ".join(paragraphs) == document

    def test_document_paragraphs_keeps_medium_blocks_intact(self):
        sentence = "Questo principio operativo deve restare leggibile anche dentro la visualizzazione finale."
        # 12 sentences -> ~840 chars -> under 1600 threshold, kept as one paragraph
        document = " ".join([sentence] * 12)

        paragraphs = views._document_paragraphs(document)

        assert len(paragraphs) == 1
        assert paragraphs[0] == document

    def test_public_document_preserves_structured_paragraphs(self):
        content = {
            "sintesi_cognitiva": [
                "Primo principio operativo. [SRC:scrape]",
                {"testo": "Secondo principio operativo. [SRC:file]"},
            ],
        }

        public_document = views._dna_public_document(content)

        assert public_document == "Primo principio operativo.\n\nSecondo principio operativo."
        assert views._document_paragraphs(public_document) == [
            "Primo principio operativo.",
            "Secondo principio operativo.",
        ]

    def test_render_dna_pdf_uses_continuous_final_document_without_layer_titles(self):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "Identita narrativa"},
        )
        final_document = "Sintesi finale.\n\nIdentita narrativa.\n\nMetodo operativo."

        pdf_bytes = views._render_dna_pdf(company, dna, final_document)
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in pdf)

        assert "Sintesi finale." in text
        assert "Identita narrativa." in text
        assert "Metodo operativo." in text
        assert "Sintesi Cognitiva" not in text
        assert "Chi siamo e come ci poniamo" not in text

    def test_submit_answers_requires_all_answers(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"identita": "Test"},
        )
        views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))
        first_question = pre_dna.questions.first()

        req = rf_with_tenant("post", reverse("dna-questions"), {
            f"answer_{first_question.id}": "Solo una risposta",
        }, form=True)
        resp = views.dna_questions(req)

        assert resp.status_code == 400
        assert CompanyDNA.objects.filter(
            company=company,
            dna_type=CompanyDNA.TYPE_COMPLETE,
        ).count() == 0

    def test_duplicate_llm_question_codes_are_normalized(self):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        payload = {
            "questions": [
                {
                    "code": "A1",
                    "section_key": "identita",
                    "principle": f"Principio {index}",
                    "question": f"Domanda {index}",
                    "answer_depth": "generica",
                    "answer_guidance": "Guida",
                }
                for index in range(10)
            ]
        }
        result = SimpleNamespace(
            text=json.dumps(payload),
            tokens_in=1,
            tokens_out=1,
            cost=0,
            latency_ms=1,
        )
        client = SimpleNamespace(generate=lambda prompt, **kw: result)

        with patch("apps.companies.views.get_llm_client", return_value=client):
            questions = views._generate_company_questions(company, pre_dna)

        assert len(questions) == 10
        assert len({question.code for question in questions}) == 10
        assert [question.pool for question in questions[:5]] == ["template"] * 5
        assert [question.pool for question in questions[5:]] == ["kb_anchored"] * 5

    def test_gap_engine_creates_follow_up_round(self, rf_with_tenant, monkeypatch):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))
        questions = list(pre_dna.questions.filter(question_round=1))
        data = {f"answer_{question.id}": f"Risposta {question.code}" for question in questions}

        def fake_evaluation(company, pre_dna, questions, plan_slug):
            return {
                "overall_sufficient": False,
                "evaluations": [
                    {"question_code": "A1", "status": "insufficiente", "rationale": "manca giudizio"},
                ],
                "follow_ups": [
                    {
                        "target_question_code": "A1",
                        "section_key": "identita",
                        "principle": "Giudizio aziendale",
                        "question": "Quale principio non negoziabile guida questa scelta?",
                        "answer_depth": "mirata",
                        "answer_guidance": "Risposta breve ma specifica.",
                    }
                ],
            }

        monkeypatch.setattr(views, "_evaluate_answer_sufficiency", fake_evaluation)

        req = rf_with_tenant("post", reverse("dna-questions"), data, form=True)
        resp = views.dna_questions(req)

        assert resp.status_code == 302
        follow_ups = list(pre_dna.questions.filter(question_round=2))
        assert len(follow_ups) == 1
        assert resp["Location"] == reverse("dna-gap-questions", args=[2])
        assert follow_ups[0].pool == CompanyQuestion.POOL_KB_ANCHORED
        assert follow_ups[0].parent_question.code == "A1"

    def test_gap_engine_proceeds_to_complete_dna_when_sufficient(self, rf_with_tenant, monkeypatch):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        views.dna_questions(rf_with_tenant("get", reverse("dna-questions")))
        questions = list(pre_dna.questions.filter(question_round=1))
        data = {f"answer_{question.id}": f"Risposta {question.code}" for question in questions}

        monkeypatch.setattr(
            views,
            "_evaluate_answer_sufficiency",
            lambda *a, **k: {"overall_sufficient": True, "evaluations": [], "follow_ups": []},
        )

        req = rf_with_tenant("post", reverse("dna-questions"), data, form=True)
        resp = views.dna_questions(req)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-generating")
        assert CompanyDNA.objects.filter(company=company, dna_type=CompanyDNA.TYPE_COMPLETE).exists()

    def test_gap_round_view_saves_answers_and_triggers_next_evaluation(
        self, rf_with_tenant, monkeypatch
    ):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = self._make_pre_dna(company)
        q1 = CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="A1",
            section_key="identita",
            principle="test",
            question="test?",
            question_round=1,
            answer="base",
            answered_at=timezone.now(),
        )
        follow_up = CompanyQuestion.objects.create(
            company=company,
            dna=pre_dna,
            code="F1",
            section_key="identita",
            principle="Approfondimento",
            question="Approfondimento?",
            question_round=2,
        )

        monkeypatch.setattr(
            views,
            "_evaluate_answer_sufficiency",
            lambda *a, **k: {"overall_sufficient": True, "evaluations": [], "follow_ups": []},
        )

        req = rf_with_tenant(
            "post",
            reverse("dna-gap-questions", args=[2]),
            {f"answer_{follow_up.id}": "Risposta approfondita"},
            form=True,
        )
        resp = views.dna_gap_questions(req, round_number=2)

        assert resp.status_code == 302
        assert resp["Location"] == reverse("dna-generating")
        follow_up.refresh_from_db()
        assert follow_up.answer == "Risposta approfondita"


@pytest.mark.django_db
class TestDNAReviewViews:
    def test_section_approve_htmx_updates_review_fragment(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("dna-section-approve", args=[dna.pk, "identita"]),
            {},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.dna_section_approve(req, dna.pk, "identita")

        assert resp.status_code == 200
        assert b'id="dna-review-root"' in resp.content
        assert "HX-Redirect" not in resp
        assert SectionApproval.objects.filter(dna=dna, section_key="identita").exists()

    def test_section_edit_htmx_updates_review_fragment(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("dna-section-edit", args=[dna.pk, "identita"]),
            {"text": "Test aggiornato"},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.dna_section_edit(req, dna.pk, "identita")

        assert resp.status_code == 200
        assert b'id="dna-review-root"' in resp.content
        assert "HX-Redirect" not in resp
        new_dna = CompanyDNA.objects.get(company=company, is_current=True)
        assert new_dna.version == 2
        assert new_dna.content["identita"] == "Test aggiornato"

    def test_final_narrative_layer_approval_is_not_blocked_by_safe_mode(self, rf_with_tenant):
        from apps.companies.dna_schemas import LAYER_KEYS

        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={key: f"Testo narrativo per {key}" for key in LAYER_KEYS},
            _enrichment={
                "validation": {
                    "safe_mode": True,
                    "flags": [{
                        "guard": "layer_completeness",
                        "severity": "CRITICAL",
                        "layer": "global",
                        "message": "Il contenuto non rispetta lo schema DNA a 6 strati.",
                    }],
                },
            },
        )
        approver = rf_with_tenant("get", "/").user
        for key in LAYER_KEYS[:-1]:
            SectionApproval.objects.create(dna=dna, section_key=key, approved_by=approver)

        req = rf_with_tenant(
            "post",
            reverse("dna-section-approve", args=[dna.pk, LAYER_KEYS[-1]]),
            {},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.dna_section_approve(req, dna.pk, LAYER_KEYS[-1])
        dna.refresh_from_db()

        assert resp.status_code == 200
        assert dna.is_approved is not None
        assert b"DNA aziendale approvato" in resp.content


# rf_with_tenant fixture is defined in tests/conftest.py (shared).


@pytest.mark.django_db
class TestProductModel:
    def test_product_creation(self):
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(company=company, name="Vasca BVCI", slug="vasca-bvci")
        assert product.name == "Vasca BVCI"
        assert str(product) == "Vasca BVCI"
        assert product.company == company

    def test_unique_slug_per_company(self):
        company = Company.objects.create(schema_name="testco", name="TestCo")
        Product.objects.create(company=company, name="Vasca A", slug="vasca-a")
        with pytest.raises(Exception):
            Product.objects.create(company=company, name="Vasca A dup", slug="vasca-a")


@pytest.mark.django_db
class TestProductDNAModel:
    def test_product_dna_creation(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            content={"identita": "test"},
            created_by=user,
        )
        assert dna.is_current is True
        assert dna.version == 1
        assert str(dna) == "Vasca v1"

    def test_product_has_status_tipologia_codice(self):
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(
            company=company,
            name="Canale Ispezionabile",
            slug="canale-ispezionabile",
            tipologia="canale",
            codice="CI-001",
        )
        assert product.status == Product.STATUS_BOZZA
        assert product.get_status_display() == "Bozza"
        assert product.tipologia == "canale"
        assert product.codice == "CI-001"

    def test_product_codice_unique_per_company(self):
        company = Company.objects.create(schema_name="testco", name="TestCo")
        Product.objects.create(company=company, name="A", slug="a", codice="CI-001")
        with pytest.raises(Exception):
            Product.objects.create(company=company, name="B", slug="b", codice="CI-001")

    def test_product_dna_missing_sections_six_layers(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            content={"identita_tecnica": "test"},
            created_by=user,
        )
        assert len(dna.missing_sections()) == 6
        ProductSectionApproval.objects.create(
            dna=dna,
            section_key="identita_tecnica",
            approved_by=user,
        )
        assert len(dna.missing_sections()) == 5

    def test_product_question_has_pool_and_round(self):
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            content={"identita": "test"},
        )
        q = ProductQuestion.objects.create(
            product=product,
            dna=dna,
            code="D1",
            question="test?",
            pool=ProductQuestion.POOL_KB_ANCHORED,
            question_round=2,
        )
        assert q.pool == "kb_anchored"
        assert q.question_round == 2
        assert q.parent_question is None


@pytest.mark.django_db
class TestProductViews:
    def test_product_list_create(self, rf_with_tenant):
        request = rf_with_tenant("get", "/products/")
        response = views.product_list_create(request)
        assert response.status_code == 200

    def test_product_create(self, rf_with_tenant):
        request = rf_with_tenant("post", "/products/", data={"name": "Vasca BVCI"}, form=True)
        response = views.product_list_create(request)
        assert response.status_code == 200
        assert Product.objects.filter(name="Vasca BVCI").exists()

    def test_product_create_with_subscription_uses_current_count(self, rf_with_tenant, monkeypatch):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        WorkspaceSubscription.objects.create(client=tenant, plan=Plan.get_default())

        request = rf_with_tenant("post", "/products/", data={"name": "Vasca BVCI"}, form=True)
        response = views.product_list_create(request)

        assert response.status_code == 200
        assert Product.objects.filter(name="Vasca BVCI").exists()
        tenant.subscription.refresh_from_db()
        assert tenant.subscription.product_dnas_used == 1

    def test_product_detail(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        request = rf_with_tenant("get", f"/products/{product.pk}/")
        response = views.product_detail(request, product.pk)
        assert response.status_code == 200

    def test_product_detail_shows_uploaded_files(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        ProductFile.objects.create(
            product=product,
            original_name="scheda.txt",
            content_text="Scheda tecnica",
        )

        request = rf_with_tenant("get", f"/products/{product.pk}/")
        response = views.product_detail(request, product.pk)

        assert response.status_code == 200
        assert b"File caricati" in response.content
        assert b"scheda.txt" in response.content

    def test_product_file_upload_browser_redirects_to_detail(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        request = rf_with_tenant(
            "post",
            reverse("product-file-upload", args=[product.pk]),
            {"notes": "Nota prodotto"},
            form=True,
        )

        response = views.product_file_upload(request, product.pk)

        assert response.status_code == 302
        assert response["Location"] == reverse("product-detail", args=[product.pk])
        assert product.product_files.filter(original_name="note-prodotto.txt").exists()

    def test_product_image_upload_stores_placeholder_text(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        request = rf_with_tenant(
            "post",
            reverse("product-file-upload", args=[product.pk]),
            {
                "file": SimpleUploadedFile(
                    "brochure.png",
                    b"\x89PNG\r\n\x1a\n",
                    content_type="image/png",
                ),
            },
            form=True,
        )

        response = views.product_file_upload(request, product.pk)

        assert response.status_code == 302
        product_file = product.product_files.get(original_name="brochure.png")
        assert "OCR/vision non ancora attiva" in product_file.content_text

    def test_product_dna_generate_browser_redirects_to_detail(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        ProductFile.objects.create(
            product=product,
            original_name="scheda.txt",
            content_text="Scheda tecnica",
        )
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"descrizione": "Test"},
        )
        request = rf_with_tenant(
            "post",
            reverse("product-dna-generate", args=[product.pk]),
            form=True,
        )
        with patch("apps.companies.tasks.generate_product_dna_task.delay"):
            response = views.product_dna_generate(request, product.pk)

        assert response.status_code == 302
        # Behavior: async generation redirects to detail with ?generating=1
        # so the page can poll the task status.
        assert response["Location"] == reverse("product-detail", args=[product.pk]) + "?generating=1"

    def test_duplicate_product_question_codes_are_normalized(self):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"descrizione": "Test"},
        )
        payload = {
            "questions": [
                {
                    "code": "D1",
                    "section_key": "descrizione",
                    "principle": f"Principio {index}",
                    "question": f"Domanda {index}",
                    "answer_depth": "generica",
                    "answer_guidance": "Guida",
                }
                for index in range(10)
            ]
        }
        result = SimpleNamespace(
            text=json.dumps(payload),
            tokens_in=1,
            tokens_out=1,
            cost=0,
            latency_ms=1,
        )
        client = SimpleNamespace(generate=lambda prompt, **kw: result)

        with patch("apps.companies.views.get_llm_client", return_value=client):
            questions = views._generate_product_questions(product, dna)

        assert len(questions) == 10
        assert len({question.code for question in questions}) == 10

    def test_product_questions_loading_does_not_dispatch_duplicate_task(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"identita_tecnica": "Pre DNA in preparazione"},
        )
        request = rf_with_tenant("get", reverse("product-questions", args=[product.pk]))

        with patch("apps.companies.tasks.generate_product_questions_task.delay") as delay:
            response = views.product_questions(request, product.pk)

        assert response.status_code == 200
        assert b"ZEUS sta generando le domande" in response.content
        delay.assert_not_called()

    def test_complete_product_dna_rewrites_sections_instead_of_appending_answers(self):
        user = User.objects.create_user(username="p", email="p@x.it", password="pw")
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        pre_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"identita": "Identita base"},
        )
        ProductQuestion.objects.create(
            product=product,
            dna=pre_dna,
            code="D1",
            section_key="identita",
            principle="Identita prodotto",
            question="Cosa distingue il prodotto?",
            answer="Risposta tecnica cliente",
        )

        complete_dna = views._create_complete_product_dna(product, pre_dna, user)

        assert complete_dna.version == 2
        assert "sintesi_cognitiva" in complete_dna.content
        assert "identita" in complete_dna.content
        # Behavior: completing the DNA transitions the product to in_validazione
        # (ready for review), not in_costruzione anymore.
        assert product.status == Product.STATUS_IN_VALIDAZIONE

    def test_product_section_approve_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={
                "identita_tecnica": "Test",
                "architettura": "x",
                "specifiche": "x",
                "applicazione": "x",
                "vincoli": "x",
                "configurazione": "x",
            },
        )
        req = rf_with_tenant(
            "post",
            reverse("product-section-approve", args=[product.pk, "identita_tecnica"]),
            {},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.product_section_approve(req, product.pk, "identita_tecnica")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("product-review", args=[product.pk])
        assert ProductSectionApproval.objects.filter(
            dna=dna,
            section_key="identita_tecnica",
        ).exists()

    # --- Decision 1B: auto-promote to in_validazione when 6/6 sections approved ---

    _FULL_SPECIALIST_CONTENT = {
        key: f"Contenuto {key}" for key in (
            "identita_tecnica",
            "architettura",
            "specifiche",
            "applicazione",
            "vincoli",
            "configurazione",
        )
    }

    def test_approving_last_section_promotes_product_to_in_validazione(self, rf_with_tenant):
        """1B: approving the 6th section auto-promotes product to in_validazione."""
        from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS

        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(
            company=company, name="Vasca", slug="vasca", status=Product.STATUS_IN_COSTRUZIONE
        )
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content=dict(self._FULL_SPECIALIST_CONTENT),
        )
        # Approve the first 5 sections
        for key in PRODUCT_LAYER_KEYS[:-1]:
            ProductSectionApproval.objects.create(
                dna=dna, section_key=key, approved_by=None, is_clarification=False
            )
        # Approve the 6th (last) section via the view
        last_key = PRODUCT_LAYER_KEYS[-1]
        req = rf_with_tenant(
            "post",
            reverse("product-section-approve", args=[product.pk, last_key]),
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        views.product_section_approve(req, product.pk, last_key)

        product.refresh_from_db()
        dna.refresh_from_db()
        assert product.status == Product.STATUS_IN_VALIDAZIONE
        assert dna.is_fully_approved()

    def test_approving_non_last_section_keeps_product_in_costruzione(self, rf_with_tenant):
        """1B: approving a non-final section does NOT change status yet."""
        from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS

        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(
            company=company, name="Vasca", slug="vasca", status=Product.STATUS_IN_COSTRUZIONE
        )
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content=dict(self._FULL_SPECIALIST_CONTENT),
        )
        req = rf_with_tenant(
            "post",
            reverse("product-section-approve", args=[product.pk, PRODUCT_LAYER_KEYS[0]]),
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        views.product_section_approve(req, product.pk, PRODUCT_LAYER_KEYS[0])

        product.refresh_from_db()
        assert product.status == Product.STATUS_IN_COSTRUZIONE

    def test_product_section_edit_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"identita_tecnica": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("product-section-edit", args=[product.pk, "identita_tecnica"]),
            {"text": "Test aggiornato"},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.product_section_edit(req, product.pk, "identita_tecnica")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("product-review", args=[product.pk])
        new_dna = ProductDNA.objects.get(product=product, is_current=True)
        assert new_dna.version == 2
        assert new_dna.content["identita_tecnica"] == "Test aggiornato"


class TestCrossSpecialistThreshold:
    """Decision 2B: Motore B requires at least 2 active specialists."""

    @staticmethod
    def _make_active_specialist(company, name, slug, codice):
        product = Product.objects.create(
            company=company,
            name=name,
            slug=slug,
            codice=codice,
            status=Product.STATUS_ATTIVO,
        )
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={
                "identita_tecnica": "x",
                "architettura": "x",
                "specifiche": "x",
                "applicazione": "x",
                "vincoli": "x",
                "configurazione": "x",
            },
        )
        return product

    def _make_company_with_generale(self):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "DNA generale base"},
            is_current=True,
        )
        return company

    def test_cross_specialist_redirects_with_zero_specialists(self, rf_with_tenant):
        company = self._make_company_with_generale()
        req = rf_with_tenant("post", reverse("dna-cross-specialist-analyze"))
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(req, "session", "session")
        setattr(req, "_messages", FallbackStorage(req))

        resp = views.dna_cross_specialist_analyze(req)

        assert resp.status_code == 302
        # No LLMCall should have been made
        assert LLMCall.objects.count() == 0

    def test_cross_specialist_redirects_with_single_specialist(self, rf_with_tenant):
        company = self._make_company_with_generale()
        self._make_active_specialist(company, "Vasca A", "vasca-a", "CI-001")
        req = rf_with_tenant("post", reverse("dna-cross-specialist-analyze"))
        # messages.info needs the messages middleware storage attached
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(req, "session", "session")
        setattr(req, "_messages", FallbackStorage(req))

        resp = views.dna_cross_specialist_analyze(req)

        assert resp.status_code == 302
        assert LLMCall.objects.count() == 0

    def test_cross_specialist_proceeds_with_two_specialists(self, rf_with_tenant, monkeypatch):
        company = self._make_company_with_generale()
        self._make_active_specialist(company, "Vasca A", "vasca-a", "CI-001")
        self._make_active_specialist(company, "Vasca B", "vasca-b", "CI-002")
        req = rf_with_tenant("post", reverse("dna-cross-specialist-analyze"))
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(req, "session", "session")
        setattr(req, "_messages", FallbackStorage(req))

        # Mock the LLM so we verify the gate passes without a real API call.
        fake = SimpleNamespace(
            text='{"summary": "ok", "shared_patterns": [], "conflicts": [], "consolidation_proposals": []}',
            tokens_in=10,
            tokens_out=10,
            cost=0.0,
            latency_ms=1,
        )
        monkeypatch.setattr(
            "apps.companies.views._generate_with_retry",
            lambda *a, **kw: (fake, json.loads(fake.text)),
        )
        monkeypatch.setattr("apps.companies.views.get_llm_client", lambda: None)

        resp = views.dna_cross_specialist_analyze(req)

        # When the gate passes and LLM returns empty analysis, the view still
        # stores the fallback and redirects to dna-review.
        assert resp.status_code == 302


@pytest.mark.django_db
class TestConsistencyMotor:
    def _make_company_with_dna(self):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"identita": "DNA generale", "confini": "Confini generali"},
            is_current=True,
        )
        return company

    def _make_product(self, company, name, slug, codice, status):
        product = Product.objects.create(
            company=company,
            name=name,
            slug=slug,
            codice=codice,
            status=status,
        )
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={key: f"{name} {key}" for key in views.PRODUCT_LAYER_KEYS},
        )
        return product

    def test_product_promote_triggers_consistency_audit_every_third_active(
        self, rf_with_tenant, monkeypatch,
    ):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        self._make_product(company, "B", "b", "B", Product.STATUS_ATTIVO)
        product = self._make_product(company, "C", "c", "C", Product.STATUS_IN_VALIDAZIONE)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("product-promote", args=[product.pk]),
            form=True,
        )

        resp = views.product_promote(req, product.pk)

        product.refresh_from_db()
        company_dna.refresh_from_db()
        assert resp.status_code == 302
        assert product.status == Product.STATUS_ATTIVO
        assert company_dna.content["_consistency_audit_pending"]["scope"] == ConsistencyIssue.SCOPE_PERIODIC
        assert called == {
            "company_id": company.pk,
            "scope": ConsistencyIssue.SCOPE_PERIODIC,
            "tenant_schema": "test-tenant",
        }

    def test_consistency_audit_run_marks_pending_and_dispatches(self, rf_with_tenant, monkeypatch):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("consistency-audit-run"),
            form=True,
        )

        resp = views.consistency_audit_run(req)

        company_dna.refresh_from_db()
        assert resp.status_code == 302
        assert company_dna.content["_consistency_audit_pending"]["scope"] == ConsistencyIssue.SCOPE_PERIODIC
        assert called == {
            "company_id": company.pk,
            "scope": ConsistencyIssue.SCOPE_PERIODIC,
            "tenant_schema": "test-tenant",
        }

    def test_product_consistency_check_marks_specialist_pending(self, rf_with_tenant, monkeypatch):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        product = self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("product-consistency-check", args=[product.pk]),
            form=True,
        )

        resp = views.product_consistency_check(req, product.pk)

        company_dna.refresh_from_db()
        pending = company_dna.content["_consistency_audit_pending"]
        assert resp.status_code == 302
        assert pending["scope"] == ConsistencyIssue.SCOPE_SPECIALIST
        assert pending["product_id"] == product.pk
        assert called == {
            "company_id": company.pk,
            "scope": ConsistencyIssue.SCOPE_SPECIALIST,
            "product_id": product.pk,
            "tenant_schema": "test-tenant",
        }

    def test_product_consistency_check_allows_complete_draft_specialist(
        self, rf_with_tenant, monkeypatch,
    ):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        product = self._make_product(company, "A", "a", "A", Product.STATUS_IN_COSTRUZIONE)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("product-consistency-check", args=[product.pk]),
            form=True,
        )

        resp = views.product_consistency_check(req, product.pk)

        company_dna.refresh_from_db()
        assert resp.status_code == 302
        assert company_dna.content["_consistency_audit_pending"]["product_id"] == product.pk
        assert called["scope"] == ConsistencyIssue.SCOPE_SPECIALIST

    def test_product_file_upload_active_specialist_triggers_t2_audit(
        self, rf_with_tenant, monkeypatch,
    ):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        product = self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("product-file-upload", args=[product.pk]),
            {"notes": "Nuovo documento tecnico"},
            form=True,
        )

        resp = views.product_file_upload(req, product.pk)

        product.refresh_from_db()
        company_dna.refresh_from_db()
        assert resp.status_code == 302
        assert product.status == Product.STATUS_UPDATING
        assert product.product_files.filter(original_name="note-prodotto.txt").exists()
        assert company_dna.content["_consistency_audit_pending"]["scope"] == "specialist"
        assert called == {
            "company_id": company.pk,
            "scope": ConsistencyIssue.SCOPE_SPECIALIST,
            "product_id": product.pk,
            "tenant_schema": "test-tenant",
        }

    def test_product_promote_uses_professional_consistency_threshold(
        self, rf_with_tenant, monkeypatch,
    ):
        monkeypatch.setattr(Client, "auto_create_schema", False)
        tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_PROFESSIONAL,
            defaults=Plan.default_values(Plan.SLUG_PROFESSIONAL),
        )
        WorkspaceSubscription.objects.create(client=tenant, plan=plan)
        company = self._make_company_with_dna()
        self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        product = self._make_product(company, "B", "b", "B", Product.STATUS_IN_VALIDAZIONE)
        called = {}

        def fake_delay(company_id, **kwargs):
            called["company_id"] = company_id
            called.update(kwargs)

        monkeypatch.setattr(tasks.run_consistency_audit, "delay", fake_delay)
        req = rf_with_tenant(
            "post",
            reverse("product-promote", args=[product.pk]),
            form=True,
        )

        resp = views.product_promote(req, product.pk)

        assert resp.status_code == 302
        assert called["scope"] == ConsistencyIssue.SCOPE_PERIODIC
        assert called["tenant_schema"] == "test-tenant"

    def test_updating_specialist_returns_to_validation_after_reapproval(self, rf_with_tenant):
        company = self._make_company_with_dna()
        product = self._make_product(company, "A", "a", "A", Product.STATUS_UPDATING)
        dna = product.dna_versions.get(dna_type=ProductDNA.TYPE_COMPLETE)

        for section_key in views.PRODUCT_LAYER_KEYS:
            req = rf_with_tenant(
                "post",
                reverse("product-section-approve", args=[product.pk, section_key]),
                form=True,
            )
            resp = views.product_section_approve(req, product.pk, section_key)
            assert resp.status_code == 200

        product.refresh_from_db()
        dna.refresh_from_db()
        assert dna.is_fully_approved() is True
        assert product.status == Product.STATUS_IN_VALIDAZIONE

    def test_product_publish_creates_channel_snapshot(self, rf_with_tenant):
        company = self._make_company_with_dna()
        product = self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        dna = product.dna_versions.get(dna_type=ProductDNA.TYPE_COMPLETE)
        dna.is_approved = timezone.now()
        dna.save(update_fields=["is_approved"])
        req = rf_with_tenant(
            "post",
            reverse("product-publish", args=[product.pk]),
            {"channel": ProductPublication.CHANNEL_WEBSITE},
            form=True,
        )

        resp = views.product_publish(req, product.pk)

        publication = ProductPublication.objects.get(product=product)
        assert resp.status_code == 302
        assert publication.channel == ProductPublication.CHANNEL_WEBSITE
        assert publication.status == ProductPublication.STATUS_PUBLISHED
        assert f"product_dna_version: {dna.version}" in publication.content_md
        assert "# A — DNA Specialista" in publication.content_md

    def test_product_publish_archives_existing_channel_snapshot(self, rf_with_tenant):
        company = self._make_company_with_dna()
        product = self._make_product(company, "A", "a", "A", Product.STATUS_ATTIVO)
        dna = product.dna_versions.get(dna_type=ProductDNA.TYPE_COMPLETE)
        dna.is_approved = timezone.now()
        dna.save(update_fields=["is_approved"])
        ProductPublication.objects.create(
            product=product,
            product_dna=dna,
            channel=ProductPublication.CHANNEL_WEBSITE,
            content_md="old",
        )
        req = rf_with_tenant(
            "post",
            reverse("product-publish", args=[product.pk]),
            {"channel": ProductPublication.CHANNEL_WEBSITE},
            form=True,
        )

        resp = views.product_publish(req, product.pk)

        assert resp.status_code == 302
        assert ProductPublication.objects.filter(
            product=product,
            channel=ProductPublication.CHANNEL_WEBSITE,
            status=ProductPublication.STATUS_PUBLISHED,
        ).count() == 1
        assert ProductPublication.objects.filter(status=ProductPublication.STATUS_ARCHIVED).count() == 1

    def test_dna_renderer_outputs_product_markdown(self):
        from apps.companies.dna_renderer import render_sintesi_cognitiva

        content = {
            "sintesi_cognitiva": "Sintesi breve",
            "identita_tecnica": "Identita tecnica",
            "specifiche": {"dimensioni": "100x50 mm"},
        }

        rendered = render_sintesi_cognitiva(content, "Canale", product=True)

        assert rendered.startswith("# Canale")
        assert "## Sintesi Cognitiva" in rendered
        assert "Identita tecnica" in rendered
        assert "dimensioni: 100x50 mm" in rendered

    def test_consistency_report_hx_returns_partial_with_pending(self, rf_with_tenant):
        company = self._make_company_with_dna()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        company_dna.content["_consistency_audit_pending"] = {"scope": ConsistencyIssue.SCOPE_PERIODIC}
        company_dna.save(update_fields=["content"])
        req = rf_with_tenant("get", reverse("consistency-report"))
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.consistency_report(req)

        body = resp.content.decode()
        assert resp.status_code == 200
        assert "consistency-report-root" in body
        assert "Audit in corso" in body
        assert "<!DOCTYPE" not in body

    def test_consistency_issue_action_resolves_issue(self, rf_with_tenant):
        company = self._make_company_with_dna()
        issue = ConsistencyIssue.objects.create(
            company=company,
            scope=ConsistencyIssue.SCOPE_PERIODIC,
            severity=ConsistencyIssue.SEVERITY_MEDIUM,
            title="Warning confini",
            description="Descrizione",
        )
        req = rf_with_tenant(
            "post",
            reverse("consistency-issue-action", args=[issue.pk, "resolve"]),
            form=True,
        )

        resp = views.consistency_issue_action(req, issue.pk, "resolve")

        issue.refresh_from_db()
        assert resp.status_code == 302
        assert issue.status == ConsistencyIssue.STATUS_RESOLVED
        assert issue.resolved_at is not None
