import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client as TestClient
from django.urls import reverse

from apps.companies.models import Company, CompanyDNA, LLMCall, Source
from apps.companies import views

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
        from apps.companies.scraper import get_scraper, MockScraperClient

        with patch.dict("os.environ", {}, clear=True):
            client = get_scraper()
            assert isinstance(client, MockScraperClient)

    def test_retry_on_failure(self):
        from apps.companies.scraper import FireCrawlClient, RETRY_MAX
        from httpx import RequestError

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
        from apps.companies.llm_client import get_llm_client, MockLLMClient
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
        assert call.model_name == "gpt-4o-mini"

    def test_generate_404_for_wrong_company(self, rf_with_tenant):
        other = Company.objects.create(schema_name="other", name="Other")
        source = Source.objects.create(company=other, url="https://x.it", status=Source.STATUS_SCRAPED)
        request = rf_with_tenant("post", reverse("dna-generate"), data={"source_id": source.id})
        response = views.dna_generate(request)
        assert response.status_code == 404


@pytest.fixture
def rf_with_tenant(django_user_model):
    """RequestFactory with request.tenant + authenticated user."""
    from django.test.client import RequestFactory

    rf = RequestFactory()
    user = django_user_model.objects.create_user(username="u", email="test@x.it", password="pw")

    def _make(method, path, data=None):
        if method == "post":
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
