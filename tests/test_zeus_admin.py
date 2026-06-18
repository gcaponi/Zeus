import pytest
from django.contrib.auth import get_user_model
from django.test import Client as TestClient
from django.test import RequestFactory
from django.urls import reverse

from apps.companies.models import Company, CompanyDNA, LLMCall, PipelineRun, Product
from apps.core.models import Client as TenantClient
from apps.core.models import Domain, Plan, WorkspaceSubscription
from apps.zeus_admin import views

User = get_user_model()


@pytest.mark.django_db
class TestZeusAdminDashboard:
    def test_requires_staff_user(self):
        user = User.objects.create_user(
            username="normal",
            email="normal@example.com",
            password="pw",
        )
        client = TestClient()
        client.force_login(user)

        response = client.get(reverse("zeus-admin-dashboard"))

        assert response.status_code == 302

    def test_staff_user_sees_real_dashboard_data(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="pw",
            is_staff=True,
        )
        tenant = TenantClient.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli",
        )
        Domain.objects.create(
            tenant=tenant,
            domain="rossi.zeus.cais.uno",
            is_primary=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        company = Company.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli SRL",
        )
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Rossi"},
        )
        Product.objects.create(company=company, name="Canale X", slug="canale-x")
        PipelineRun.objects.create(
            company=company,
            status=PipelineRun.STATUS_COMPLETED,
            current_step="done",
        )
        LLMCall.objects.create(
            company=company,
            model_name="test-model",
            prompt_text="prompt",
            response_text="response",
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.25,
            latency_ms=100,
        )
        request = RequestFactory().get(reverse("zeus-admin-dashboard"))
        request.user = staff

        response = views.dashboard(request)

        assert response.status_code == 200
        assert b"Dashboard" in response.content
        assert b"Rossi Metalli" in response.content
        assert b"rossi.zeus.cais.uno" in response.content
        assert b"Foundation" in response.content
        assert b"DNA completo" in response.content
        assert b"2500" in response.content
        assert b"Sistema" in response.content
        assert b"Database" in response.content
        assert b"Celery Worker" in response.content
        assert b"Storage" in response.content

    def test_system_health_in_context(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="health-staff",
            email="health@example.com",
            password="pw",
            is_staff=True,
        )
        request = RequestFactory().get(reverse("zeus-admin-dashboard"))
        request.user = staff

        response = views.dashboard(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Database" in html
        assert "Celery Worker" in html
        assert "Storage" in html
        assert "Uptime" in html
        assert "Online" in html
