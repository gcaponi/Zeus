import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as TestClient
from django.test import RequestFactory
from django.urls import reverse

from apps.companies import views
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
        dna1 = CompanyDNA.objects.create(company=company, version=1, content={"v": 1}, is_current=False, created_by=user)
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
        company = Company.objects.create(schema_name="test-tenant", name="Naked")
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
        from httpx import RequestError

        from apps.companies.scraper import RETRY_MAX, FireCrawlClient

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
        assert "chi_siamo" in data
        assert "mission" in data
        assert "pilastri" in data
        assert isinstance(data["pilastri"], list)
        assert result.tokens_in == 350
        assert result.cost == 0.0001

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
        assert data["content"]["chi_siamo"] != ""
        assert data["tokens_in"] > 0

        assert LLMCall.objects.count() == 1
        call = LLMCall.objects.first()
        assert call.tokens_in == 350
        assert call.model_name == "deepseek-chat"

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
        assert run.current_step == "done"
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
        for index in range(5):
            CompanyFile.objects.create(
                company=company,
                original_name=f"doc-{index}.txt",
                content_text="test",
            )

        req = rf_with_tenant("post", "/onboarding/source/", {
            "url": "https://rossi-metalli.it",
            "company_notes": "Nuovo documento oltre quota.",
        }, form=True)
        req.META["HTTP_HX_REQUEST"] = "true"
        resp = views.onboarding_source_create(req)

        assert resp.status_code == 403
        assert b"Limite file aziendali" in resp.content
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
        assert b"Analisi in corso" in resp.content
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
                "chi_siamo": "Azienda manifatturiera B2B.",
                "mission": "Servire clienti tecnici.",
                "settore": "Meccanica.",
                "mercato": "Italia ed Europa.",
                "pilastri": ["Qualita", "Rapidita"],
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
        assert "riformulata integrando le risposte" in complete_dna.content["chi_siamo"]
        assert "Approfondimenti cliente" not in complete_dna.content["chi_siamo"]
        assert "Risposta A1" not in complete_dna.content["chi_siamo"]
        pre_dna.refresh_from_db()
        assert pre_dna.is_current is False

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
            "chi_siamo": {"descrizione": "Testo chi siamo pulito."},
            "mission": {"description": "Testo mission pulito."},
            "settore": {"testo": "Testo settore pulito."},
            "mercato": {"value": "Testo mercato pulito."},
            "pilastri": [{"descrizione": "Qualita"}, {"descrizione": "Rapidita"}],
        })

        values = {section["key"]: section["value"] for section in sections}
        assert values["chi_siamo"] == "Testo chi siamo pulito."
        assert values["mission"] == "Testo mission pulito."
        assert values["settore"] == "Testo settore pulito."
        assert values["mercato"] == "Testo mercato pulito."
        assert values["pilastri"] == "Qualita, Rapidita"
        assert "descrizione" not in values["chi_siamo"]
        assert "{" not in values["chi_siamo"]

    def test_submit_answers_requires_all_answers(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        pre_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"chi_siamo": "Test"},
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
                    "section_key": "chi_siamo",
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
        client = SimpleNamespace(generate=lambda prompt: result)

        with patch("apps.companies.views.get_llm_client", return_value=client):
            questions = views._generate_company_questions(company, pre_dna)

        assert len(questions) == 10
        assert len({question.code for question in questions}) == 10


@pytest.mark.django_db
class TestDNAReviewViews:
    def test_section_approve_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("dna-section-approve", args=[dna.pk, "chi_siamo"]),
            {},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.dna_section_approve(req, dna.pk, "chi_siamo")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("dna-review")
        assert SectionApproval.objects.filter(dna=dna, section_key="chi_siamo").exists()

    def test_section_edit_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("dna-section-edit", args=[dna.pk, "chi_siamo"]),
            {"text": "Test aggiornato"},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.dna_section_edit(req, dna.pk, "chi_siamo")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("dna-review")
        new_dna = CompanyDNA.objects.get(company=company, is_current=True)
        assert new_dna.version == 2
        assert new_dna.content["chi_siamo"] == "Test aggiornato"


@pytest.fixture
def rf_with_tenant(django_user_model):
    """RequestFactory with request.tenant + authenticated user."""
    from django.test.client import RequestFactory

    rf = RequestFactory()
    user = django_user_model.objects.create_user(username="u", email="test@x.it", password="pw")

    def _make(method, path, data=None, form=False):
        if method == "post":
            if form:
                req = rf.post(path, data or {})
            else:
                req = rf.post(path, json.dumps(data or {}), content_type="application/json")
        else:
            req = rf.get(path)

        class FakeTenant:
            schema_name = "test-tenant"
            name = "Test Tenant"

        req.tenant = FakeTenant()
        req.user = user
        return req

    return _make


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
            content={"descrizione": "test"},
            created_by=user,
        )
        assert dna.is_current is True
        assert dna.version == 1
        assert str(dna) == "Vasca v1"

    def test_product_dna_missing_sections(self, django_user_model):
        user = django_user_model.objects.create_user(username="t", email="test@x.it", password="pw")
        company = Company.objects.create(schema_name="testco", name="TestCo")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            content={"descrizione": "test"},
            created_by=user,
        )
        assert len(dna.missing_sections()) == 5
        ProductSectionApproval.objects.create(
            dna=dna,
            section_key="descrizione",
            approved_by=user,
        )
        assert len(dna.missing_sections()) == 4


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
        assert b"File prodotto caricati" in response.content
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
        with patch(
            "apps.companies.tasks._generate_product_dna",
            return_value=(dna, SimpleNamespace(cost_usd=0)),
        ):
            response = views.product_dna_generate(request, product.pk)

        assert response.status_code == 302
        assert response["Location"] == reverse("product-detail", args=[product.pk])

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
        client = SimpleNamespace(generate=lambda prompt: result)

        with patch("apps.companies.views.get_llm_client", return_value=client):
            questions = views._generate_product_questions(product, dna)

        assert len(questions) == 10
        assert len({question.code for question in questions}) == 10

    def test_complete_product_dna_rewrites_sections_instead_of_appending_answers(self):
        user = User.objects.create_user(username="p", email="p@x.it", password="pw")
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        pre_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"descrizione": "Descrizione base"},
        )
        ProductQuestion.objects.create(
            product=product,
            dna=pre_dna,
            code="D1",
            section_key="descrizione",
            principle="Identita prodotto",
            question="Cosa distingue il prodotto?",
            answer="Risposta tecnica cliente",
        )

        complete_dna = views._create_complete_product_dna(product, pre_dna, user)

        assert complete_dna.version == 2
        assert "riformulata integrando le risposte" in complete_dna.content["descrizione"]
        assert "Approfondimenti cliente" not in complete_dna.content["descrizione"]
        assert "Risposta tecnica cliente" not in complete_dna.content["descrizione"]

    def test_product_section_approve_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"descrizione": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("product-section-approve", args=[product.pk, "descrizione"]),
            {},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.product_section_approve(req, product.pk, "descrizione")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("product-review", args=[product.pk])
        assert ProductSectionApproval.objects.filter(
            dna=dna,
            section_key="descrizione",
        ).exists()

    def test_product_section_edit_htmx_redirects_to_review(self, rf_with_tenant):
        company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        product = Product.objects.create(company=company, name="Vasca", slug="vasca")
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"descrizione": "Test"},
        )
        req = rf_with_tenant(
            "post",
            reverse("product-section-edit", args=[product.pk, "descrizione"]),
            {"text": "Test aggiornato"},
            form=True,
        )
        req.META["HTTP_HX_REQUEST"] = "true"

        resp = views.product_section_edit(req, product.pk, "descrizione")

        assert resp.status_code == 204
        assert resp["HX-Redirect"] == reverse("product-review", args=[product.pk])
        new_dna = ProductDNA.objects.get(product=product, is_current=True)
        assert new_dna.version == 2
        assert new_dna.content["descrizione"] == "Test aggiornato"
