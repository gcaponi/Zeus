from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client as TestClient
from django.test import RequestFactory
from django.urls import reverse

from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
)
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

        response = client.get(reverse("zeus-admin-clients"))

        assert response.status_code == 302

        response = client.get(reverse("zeus-admin-client-detail", args=[1]))

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
        assert b"/zeus-admin/clients/?segment=active" in response.content
        assert b"/zeus-admin/clients/?segment=complete_dna" in response.content

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

    def test_clients_page_filters_real_dashboard_data(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="clients-staff",
            email="clients@example.com",
            password="pw",
            is_staff=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        active_tenant = TenantClient.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli",
        )
        Domain.objects.create(
            tenant=active_tenant,
            domain="rossi.zeus.cais.uno",
            is_primary=True,
        )
        WorkspaceSubscription.objects.create(
            client=active_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        suspended_tenant = TenantClient.objects.create(
            schema_name="bianchi-infissi",
            name="Bianchi Infissi",
        )
        WorkspaceSubscription.objects.create(
            client=suspended_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_SUSPENDED,
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
        request = RequestFactory().get(
            reverse("zeus-admin-clients"),
            {"segment": "active", "q": "rossi"},
        )
        request.user = staff

        response = views.clients(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Clienti ZEUS" in html
        assert "Rossi Metalli" in html
        assert "Bianchi Infissi" not in html
        assert "1 di 2 workspace" in html

    def test_clients_htmx_returns_results_partial(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="htmx-staff",
            email="htmx@example.com",
            password="pw",
            is_staff=True,
        )
        request = RequestFactory().get(
            reverse("zeus-admin-clients"),
            HTTP_HX_REQUEST="true",
        )
        request.user = staff

        response = views.clients(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Risultati Clienti" in html
        assert "Clienti ZEUS" not in html

    def test_client_detail_shows_configuration_and_uploaded_files(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="detail-staff",
            email="detail@example.com",
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
            notes="Cliente strategico",
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
        CompanyFile.objects.create(
            company=company,
            original_name="Scheda tecnica aziendale.pdf",
            content_text="Contenuto scheda aziendale",
            file_size=2048,
            uploaded_by=staff,
        )
        product = Product.objects.create(
            company=company,
            name="Prodotto Alpha",
            slug="prodotto-alpha",
        )
        ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"descrizione": "Alpha"},
        )
        ProductFile.objects.create(
            product=product,
            original_name="Manuale prodotto.pdf",
            content_text="Istruzioni del prodotto Alpha",
            file_size=4096,
            uploaded_by=staff,
        )
        request = RequestFactory().get(
            reverse("zeus-admin-client-detail", args=[tenant.pk]),
        )
        request.user = staff

        response = views.client_detail(request, tenant.pk)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Configurazione Rapida" in html
        assert "Limiti Piano" in html
        assert "Rossi Metalli" in html
        assert "Scheda tecnica aziendale.pdf" in html
        assert "Prodotto Alpha" in html
        assert "Manuale prodotto.pdf" in html
        assert "Cambia Password" in html

    def test_client_detail_updates_subscription_configuration(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="config-staff",
            email="config@example.com",
            password="pw",
            is_staff=True,
        )
        foundation, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        professional, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_PROFESSIONAL,
            defaults=Plan.default_values(Plan.SLUG_PROFESSIONAL),
        )
        tenant = TenantClient.objects.create(
            schema_name="config-client",
            name="Config Client",
            on_trial=True,
        )
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=foundation,
            status=WorkspaceSubscription.STATUS_TRIAL,
        )
        request = RequestFactory().post(
            reverse("zeus-admin-client-detail", args=[tenant.pk]),
            {
                "plan_id": str(professional.pk),
                "status": WorkspaceSubscription.STATUS_SUSPENDED,
                "paid_until": "2026-07-01",
                "notes": "Upgrade sospeso in attesa pagamento",
            },
        )
        request.user = staff

        response = views.client_detail(request, tenant.pk)

        tenant.refresh_from_db()
        tenant.subscription.refresh_from_db()
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/clients/{tenant.pk}/?saved=1")
        assert tenant.on_trial is False
        assert tenant.paid_until == date(2026, 7, 1)
        assert tenant.subscription.plan == professional
        assert tenant.subscription.status == WorkspaceSubscription.STATUS_SUSPENDED
        assert tenant.subscription.notes == "Upgrade sospeso in attesa pagamento"
